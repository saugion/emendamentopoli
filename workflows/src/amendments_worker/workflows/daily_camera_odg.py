"""Nightly workflow: Camera orders of the day (ODG) from dati.camera.it (SPARQL metadata)
plus the sicross text pages from documenti.camera.it; saves every text version to Supabase
and (from Phase 2) evaluates the money-relevant current versions with Mistral.

Incremental mode uses the `camera_odg_modified` cursor (ods:modified, 2-day overlap for
discovery); sicross pages are re-fetched only for new items, changed metadati_hash,
in_corso items or the recent-window pass (the last CAMERA_ODG_RECENT_DAYS of dc:date),
because ods:modified is bulk re-stamped. Backfill mode (input date..end_date) scans one
dc:date window and never touches the cursor.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from pydantic import BaseModel, Field, model_validator

import mistralai.workflows as workflows

from ..sources import common
from ..sources import supabase as source_supabase
from ..sources.camera_odg import (
    CAMERA_SPARQL_PAGE,
    CameraOdgItem,
    camera_odg_rows,
    metadati_hash,
    odg_atto,
    parse_odg_bindings,
    parse_sicross,
    query_camera_odg,
)
from ..sources.common import CAMERA_HTML_TIMEOUT, SPARQL_CAMERA_ENDPOINT, polite_get
from ..sources.rilevanza import calcola_rilevanza
from .evaluation import evaluate_and_save

CAMERA_ODG_CHUNK = 50
CAMERA_ODG_RECENT_DAYS = 14
CURSOR_NAME = "camera_odg_modified"


class DailyCameraOdgInput(BaseModel):
    date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Backfill: first day of dc:date window (YYYY-MM-DD). Null = incremental mode (ods:modified cursor)",
    )
    end_date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Backfill: last day (inclusive); default = date",
    )
    legislature: int = Field(default=19, ge=19, le=19)
    max_items: int = Field(default=1500, ge=1, le=5000, description="Max sicross pages fetched in this execution")
    evaluate: bool = True

    @model_validator(mode="after")
    def _consistent_period(self) -> "DailyCameraOdgInput":
        if self.end_date and not self.date:
            raise ValueError("end_date requires date")
        if self.date and self.end_date and self.end_date < self.date:
            raise ValueError("end_date must be greater than or equal to date")
        return self


class DailyCameraOdgOutput(BaseModel):
    mode: Literal["incremental", "backfill"]
    since: str
    cursor: str | None              # new cursor value (incremental) or None
    sparql_items: int               # distinct ODG returned by SPARQL
    fetched: int                    # sicross pages fetched
    not_found: int                  # FILE NON TROVATO
    skipped_unchanged: int
    saved_rows: int                 # version rows upserted
    stopped_by_max_items: bool
    evaluated: int
    reused_evaluations: int
    ids: list[str]                  # ids of current rows saved in this run
    errors: list[str]
    evaluation_errors: list[str]


class OdgChunkResult(BaseModel):
    processed: int                 # items of the chunk handled (fetched or skipped), in order
    fetched: int
    not_found: int
    skipped_unchanged: int
    saved_rows: int
    saved_ids: list[str]
    last_modified: str | None      # ods:modified of the last handled item
    to_evaluate: list[str]         # ids of saved current rows to evaluate (empty until Phase 2)
    errors: list[str]


# --- Supabase helpers (monkeypatched in tests with fakes.FakeSupabase) ---


async def db_cursor_get(nome: str) -> str | None:
    base, key = source_supabase.supabase_base_and_key()
    async with httpx.AsyncClient(timeout=45.0, headers=source_supabase.supabase_headers(key)) as client:
        rows = await source_supabase.select_all(
            client, "ingest_cursori", "nome,valore", {"nome": f"eq.{nome}"}
        )
    return rows[0]["valore"] if rows else None


async def db_cursor_set(nome: str, valore: str) -> None:
    base, key = source_supabase.supabase_base_and_key()
    async with httpx.AsyncClient(timeout=45.0, headers=source_supabase.supabase_headers(key)) as client:
        await source_supabase.upsert(
            client,
            "ingest_cursori",
            [{"nome": nome, "valore": valore, "aggiornato_at": datetime.now(timezone.utc).isoformat()}],
            "nome",
        )


async def db_current_rows(keys: list[str]) -> dict[str, dict]:
    """Current rows (corrente=is.true) of the given natural keys: chiave_naturale -> row."""
    base, key = source_supabase.supabase_base_and_key()
    in_list = "in.(" + ",".join(f'"{k}"' for k in keys) + ")"
    async with httpx.AsyncClient(timeout=45.0, headers=source_supabase.supabase_headers(key)) as client:
        rows = await source_supabase.select_all(
            client,
            "emendamenti",
            "id,chiave_naturale,metadati_hash,esito,data,ordine_versione",
            {"corrente": "is.true", "chiave_naturale": in_list},
        )
    return {r["chiave_naturale"]: r for r in rows}


async def db_atti_flags(legislature: int) -> dict[str, dict]:
    base, key = source_supabase.supabase_base_and_key()
    async with httpx.AsyncClient(timeout=45.0, headers=source_supabase.supabase_headers(key)) as client:
        rows = await source_supabase.select_all(
            client,
            "atti",
            "uri,is_dl_conversione,is_bilancio,is_commissione_bilancio_referente",
            {"legislatura": f"eq.{legislature}"},
            key="uri",
        )
    return {r["uri"]: r for r in rows}


async def db_save_odg_rows(rows: list[dict]) -> None:
    base, key = source_supabase.supabase_base_and_key()
    async with httpx.AsyncClient(timeout=150.0, headers=source_supabase.supabase_headers(key)) as client:
        await source_supabase.upsert(client, "emendamenti", rows, "id")
        await source_supabase.rpc(
            client,
            "ricalcola_versioni",
            {"p_chiavi": sorted({r["chiave_naturale"] for r in rows})},
        )


# --- activities ---


@workflows.activity(start_to_close_timeout=timedelta(seconds=30), retry_policy_max_attempts=3)
async def camera_cursor_get(nome: str) -> str | None:
    return await db_cursor_get(nome)


@workflows.activity(start_to_close_timeout=timedelta(seconds=30), retry_policy_max_attempts=3)
async def camera_recent_window(days: int) -> tuple[str, str]:
    """(date_from, date_to) as YYYYMMDD of the recent-window pass; an activity because
    workflow code must stay deterministic (no datetime.now() in the workflow itself)."""
    now = datetime.now(timezone.utc)
    return (now - timedelta(days=days)).strftime("%Y%m%d"), now.strftime("%Y%m%d")


@workflows.activity(start_to_close_timeout=timedelta(seconds=30), retry_policy_max_attempts=3)
async def camera_cursor_set(nome: str, valore: str) -> None:
    await db_cursor_set(nome, valore)


@workflows.activity(start_to_close_timeout=timedelta(seconds=300), retry_policy_max_attempts=1)
async def fetch_camera_odg_page(
    legislature: int, since: str, date_from: str | None, date_to: str | None
) -> list[CameraOdgItem]:
    q = query_camera_odg(legislature, since, date_from, date_to)
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await polite_get(
            client,
            SPARQL_CAMERA_ENDPOINT,
            params={"query": q, "format": "application/sparql-results+json"},
            headers={"Accept": "application/sparql-results+json"},
            timeout=common.CAMERA_SPARQL_TIMEOUT,
        )
    return parse_odg_bindings(resp.json()["results"]["bindings"])


@workflows.activity(start_to_close_timeout=timedelta(seconds=900), retry_policy_max_attempts=2)
async def process_camera_odg_chunk(
    items: list[CameraOdgItem], max_fetch: int, incremental: bool, force_refetch: bool = False
) -> OdgChunkResult:
    keys = [f"camera:19:odg:{i.identifier.strip().upper()}" for i in items]
    existing = await db_current_rows(keys)
    atti = await db_atti_flags(19)
    fetched = 0
    not_found = 0
    skipped = 0
    errors: list[str] = []
    rows: list[dict] = []
    handled: list[CameraOdgItem] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for item, key in zip(items, keys):
            row = existing.get(key)
            mh = metadati_hash(item)
            needs_fetch = (
                force_refetch
                or row is None
                or row.get("metadati_hash") is None
                or row.get("metadati_hash") != mh
                or row.get("esito") == "in_corso"
            )
            if not needs_fetch:
                skipped += 1
                handled.append(item)
                continue
            if fetched >= max_fetch:
                break  # remaining items are not handled; picked up by the next run
            fetched += 1
            handled.append(item)
            url = item.relation.replace("http://", "https://", 1)
            page = None
            try:
                resp = await polite_get(client, url, timeout=CAMERA_HTML_TIMEOUT)
                text = resp.content.decode("utf-8", errors="replace")
                page = parse_sicross(text)
                if page is None:
                    not_found += 1
            except Exception as e:
                errors.append(f"{item.identifier}: {type(e).__name__}: {e}")
                page = None
            item_rows = camera_odg_rows(item, page)
            flags = _odg_atto_flags(item, atti)
            for r in item_rows:
                r["rilevante_spesa"], r["rilevanza_motivo"] = calcola_rilevanza(r["testo"], flags)
            rows.extend(item_rows)
    # current row per key = highest ordine_versione among the new rows and the stored one
    max_ordine: dict[str, int] = {k: (existing[k].get("ordine_versione") or 0) for k in keys if k in existing}
    for r in rows:
        k = r["chiave_naturale"]
        max_ordine[k] = max(max_ordine.get(k, 0), r["ordine_versione"])
    to_evaluate = [
        r["id"]
        for r in rows
        if r.get("rilevante_spesa") is True
        and r.get("testo")
        and r["ordine_versione"] == max_ordine.get(r["chiave_naturale"])
    ]
    saved_ids = [r["id"] for r in rows]
    if rows:
        await db_save_odg_rows(rows)
    return OdgChunkResult(
        processed=len(handled),
        fetched=fetched,
        not_found=not_found,
        skipped_unchanged=skipped,
        saved_rows=len(rows),
        saved_ids=saved_ids,
        last_modified=handled[-1].modified if handled else None,
        to_evaluate=to_evaluate,
        errors=errors,
    )


def _odg_atto_flags(item: CameraOdgItem, atti: dict[str, dict]) -> dict[str, bool]:
    """Level A signals: atti table flags by atto_uri plus the SPARQL title/identifier rules."""
    flags: dict[str, bool] = {}
    atto_uri, _, _ = odg_atto(item.identifier.strip())
    a = atti.get(atto_uri) if atto_uri else None
    if a:
        flags["atto_dl"] = bool(a.get("is_dl_conversione"))
        flags["atto_bilancio"] = bool(a.get("is_bilancio"))
        flags["atto_commissione_bilancio"] = bool(a.get("is_commissione_bilancio_referente"))
    if item.identifier.strip().upper().startswith("9/DOC.VIII,") or "BILANCIO INTERNO" in item.titolo:
        flags["atto_bilancio_interno"] = True
    if "P.D.L. DI BILANCIO" in item.titolo:
        flags["atto_bilancio"] = True
    return flags


@workflows.workflow.define(
    name="camera-odg-giornalieri",
    workflow_display_name="Daily ODG (Camera)",
    workflow_description=(
        "Downloads from dati.camera.it (SPARQL, CC BY-SA 4.0) the Camera orders of the day changed "
        "since the cursor (or in the dc:date window date..end_date), fetches the sicross texts from "
        "documenti.camera.it, saves every text version to Supabase and evaluates the money-relevant "
        "current versions with Mistral."
    ),
    execution_timeout=timedelta(hours=2),
)
class DailyCameraOdgWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: DailyCameraOdgInput) -> DailyCameraOdgOutput:
        incremental = input.date is None
        if incremental:
            cursor_value = await camera_cursor_get(CURSOR_NAME)
            if cursor_value:
                since_dt = datetime.fromisoformat(cursor_value.replace("Z", "+00:00")) - timedelta(days=2)
                since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                since = "2000-01-01T00:00:00Z"
            date_from = date_to = None
        else:
            since = "2000-01-01T00:00:00Z"
            date_from = input.date.replace("-", "")
            date_to = (input.end_date or input.date).replace("-", "")

        sparql_items = 0
        fetched = 0
        not_found = 0
        skipped = 0
        saved_rows = 0
        ids: list[str] = []
        errors: list[str] = []
        evaluation_errors: list[str] = []
        evaluated = 0
        reused = 0
        stopped = False
        cursor_out: str | None = None
        eval_ids: list[str] = []

        async def run_pass(since_arg: str, df: str | None, dt: str | None, force: bool) -> None:
            nonlocal sparql_items, fetched, not_found, skipped, saved_rows, ids, errors
            nonlocal stopped, cursor_out
            seen: set[str] = set()
            cursor = since_arg
            while True:
                page = await fetch_camera_odg_page(input.legislature, cursor, df, dt)
                new = [i for i in page if i.aic not in seen]
                seen |= {i.aic for i in new}
                sparql_items += len(new)
                last_page = len(page) < CAMERA_SPARQL_PAGE
                for start in range(0, len(new), CAMERA_ODG_CHUNK):
                    chunk = new[start : start + CAMERA_ODG_CHUNK]
                    result = await process_camera_odg_chunk(
                        chunk, input.max_items - fetched, incremental and not force, force
                    )
                    fetched += result.fetched
                    not_found += result.not_found
                    skipped += result.skipped_unchanged
                    saved_rows += result.saved_rows
                    ids.extend(result.saved_ids)
                    eval_ids.extend(result.to_evaluate)
                    errors.extend(result.errors)
                    if result.last_modified and (cursor_out is None or result.last_modified > cursor_out):
                        cursor_out = result.last_modified
                        if incremental and not force:
                            await camera_cursor_set(CURSOR_NAME, result.last_modified)
                    if result.processed < len(chunk):
                        stopped = True
                        return
                if last_page or stopped:
                    return
                if not new:
                    raise RuntimeError("keyset stalled: >= 1000 ODG share ods:modified " + cursor)
                cursor = page[-1].modified

        await run_pass(since, date_from, date_to, force=False)

        if incremental and not stopped and CAMERA_ODG_RECENT_DAYS > 0:
            recent_from, recent_to = await camera_recent_window(CAMERA_ODG_RECENT_DAYS)
            await run_pass("2000-01-01T00:00:00Z", recent_from, recent_to, force=True)

        if input.evaluate and eval_ids:
            sem = asyncio.Semaphore(10)

            async def _eval(amendment_id: str):
                async with sem:
                    return await evaluate_and_save(amendment_id)

            outcomes = await asyncio.gather(*[_eval(i) for i in eval_ids], return_exceptions=True)
            for o in outcomes:
                if isinstance(o, BaseException):
                    evaluation_errors.append(f"{type(o).__name__}: {o}")
                elif o.status == "evaluated":
                    evaluated += 1
                elif o.status == "reused":
                    reused += 1

        return DailyCameraOdgOutput(
            mode="incremental" if incremental else "backfill",
            since=since,
            cursor=cursor_out if incremental else None,
            sparql_items=sparql_items,
            fetched=fetched,
            not_found=not_found,
            skipped_unchanged=skipped,
            saved_rows=saved_rows,
            stopped_by_max_items=stopped,
            evaluated=evaluated,
            reused_evaluations=reused,
            ids=ids,
            errors=errors,
            evaluation_errors=evaluation_errors,
        )
