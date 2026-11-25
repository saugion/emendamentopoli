"""Nightly workflow: Camera emendamenti from documenti.camera.it.

Discovery: atto pages on www.camera.it -> list URLs (upserted into
camera_liste_emendamenti). Checking: each list with a conditional GET
(If-Modified-Since; 304 -> only controllato_at is updated). Details: one fetch per
new or changed proposal (list rows carry id + position; the detail page holds every
version). Every version is upserted (id = <chiave>#<position>) and
ricalcola_versioni runs after each save; from Phase 2 only money-relevant current
versions are evaluated (evaluate_and_save).
"""
from __future__ import annotations


import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

import mistralai.workflows as workflows

from ..sources import supabase as source_supabase
from ..sources.camera_emendamenti import (
    ATTO_PAGE,
    detail_url,
    eme_rows,
    extract_list_urls,
    list_url_parts,
    parse_detail,
    parse_list,
)
from ..sources.common import camera_html_timeout, polite_get
from ..sources.rilevanza import calcola_rilevanza
from .daily_camera_odg import camera_cursor_get, camera_cursor_set, db_atti_flags
from .evaluation import evaluate_and_save

CURSOR_NAME = "camera_eme_atti_modified"
DISCOVERY_BATCH = 20
MAX_DETAIL_PER_CALL = 100


class DailyCameraEmendamentiInput(BaseModel):
    legislature: int = Field(default=19, ge=19, le=19)
    atti: list[str] | None = Field(default=None, description="Explicit atto numbers (pilot), e.g. ['3133','3106','3121']; null = cursor mode")
    max_atti: int = Field(default=300, ge=0, le=3500, description="Atto pages to (re)discover in this execution")
    max_liste: int = Field(default=600, ge=0, le=5000, description="List pages to check in this execution")
    max_detail: int = Field(default=2500, ge=0, le=5000, description="Detail pages to fetch in this execution")
    evaluate: bool = True


class DailyCameraEmendamentiOutput(BaseModel):
    atti_discovered: int
    lists_known: int
    lists_checked: int
    lists_not_modified: int
    rows_seen: int
    details_fetched: int
    saved_rows: int
    esito_updates: int
    stopped_by_cap: bool
    evaluated: int
    reused_evaluations: int
    errors: list[str]
    evaluation_errors: list[str]


class DiscoveryResult(BaseModel):
    lists_found: int
    errors: list[str]


class ListResult(BaseModel):
    not_modified: bool
    rows_seen: int
    details_fetched: int
    remaining_details: int     # details still needed for this list after this call
    saved_rows: int
    esito_updates: int
    to_evaluate: list[str]
    errors: list[str]


# --- Supabase helpers (monkeypatched in tests with fakes.FakeSupabase) ---


async def db_atti_for_discovery(cursor: str | None, limit: int) -> list[tuple[str, str]]:
    import httpx
    base, key = source_supabase.supabase_base_and_key()
    filters: dict[str, str] = {"legislatura": "eq.19"}
    if cursor:
        since = (
            datetime.fromisoformat(cursor.replace("Z", "+00:00")) - timedelta(days=2)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        filters["fonte_modificata"] = f"gte.{since}"
    async with httpx.AsyncClient(timeout=60.0, headers=source_supabase.supabase_headers(key)) as client:
        resp = await client.get(
            f"{base}/rest/v1/atti",
            params={
                "select": "numero,fonte_modificata",
                "order": "fonte_modificata.asc,uri.asc",
                "limit": str(limit),
                **filters,
            },
            headers=source_supabase.supabase_headers(key),
        )
        resp.raise_for_status()
        rows = resp.json()
    return [(r["numero"], r["fonte_modificata"]) for r in rows if r.get("fonte_modificata")]


async def db_upsert_liste(rows: list[dict]) -> None:
    import httpx
    base, key = source_supabase.supabase_base_and_key()
    async with httpx.AsyncClient(timeout=90.0, headers=source_supabase.supabase_headers(key)) as client:
        await source_supabase.upsert(client, "camera_liste_emendamenti", rows, "url")


async def db_lists_known() -> int:
    import httpx
    base, key = source_supabase.supabase_base_and_key()
    async with httpx.AsyncClient(timeout=60.0, headers=source_supabase.supabase_headers(key)) as client:
        resp = await client.get(
            f"{base}/rest/v1/camera_liste_emendamenti",
            params={"select": "url", "limit": "1"},
            headers={**source_supabase.supabase_headers(key), "Prefer": "count=exact"},
        )
        resp.raise_for_status()
        return int(resp.headers.get("content-range", "0-0/0").split("/")[-1])


async def db_lists_to_check(atti: list[str] | None, limit: int) -> list[str]:
    import httpx
    base, key = source_supabase.supabase_base_and_key()
    filters: dict[str, str] = {}
    order = "controllato_at.asc.nullsfirst"
    if atti:
        filters["atto_numero"] = "in.(" + ",".join(f'"{a}"' for a in atti) + ")"
        order = "url.asc"
    async with httpx.AsyncClient(timeout=60.0, headers=source_supabase.supabase_headers(key)) as client:
        resp = await client.get(
            f"{base}/rest/v1/camera_liste_emendamenti",
            params={"select": "url", "order": order, "limit": str(limit), **filters},
            headers=source_supabase.supabase_headers(key),
        )
        resp.raise_for_status()
        return [r["url"] for r in resp.json()]


async def db_list_state(url: str) -> dict | None:
    import httpx
    base, key = source_supabase.supabase_base_and_key()
    async with httpx.AsyncClient(timeout=45.0, headers=source_supabase.supabase_headers(key)) as client:
        rows = await source_supabase.select_all(
            client, "camera_liste_emendamenti", "url,last_modified,righe", {"url": f"eq.{url}"}, key="url"
        )
    return rows[0] if rows else None


def _liste_row(url: str, **extra) -> dict:
    # PostgREST upsert checks NOT NULL on the proposed insert row before the
    # conflict arbitration, so every upsert must carry all not-null columns.
    parts = list_url_parts(url)
    atto_numero, sede = parts if parts else ("?", "?")
    return {"url": url, "legislatura": 19, "atto_numero": atto_numero, "sede": sede, **extra}


async def db_list_touch(url: str) -> None:
    await db_upsert_liste(
        [_liste_row(url, controllato_at=datetime.now(timezone.utc).isoformat())]
    )


async def db_list_store_result(url: str, last_modified: str, righe: int) -> None:
    await db_upsert_liste(
        [
            _liste_row(
                url,
                last_modified=last_modified,
                righe=righe,
                controllato_at=datetime.now(timezone.utc).isoformat(),
            )
        ]
    )


async def db_emendamenti_by_fonte(url: str) -> dict[str, dict]:
    import httpx
    base, key = source_supabase.supabase_base_and_key()
    async with httpx.AsyncClient(timeout=90.0, headers=source_supabase.supabase_headers(key)) as client:
        rows = await source_supabase.select_all(
            client,
            "emendamenti",
            "id,metadati_hash,esito,ordine_versione,valutazioni(id)",
            {"fonte_uri": f"eq.{url}"},
        )
    return {r["id"]: r for r in rows}


async def db_save_eme_rows(rows: list[dict]) -> None:
    import httpx
    base, key = source_supabase.supabase_base_and_key()
    async with httpx.AsyncClient(timeout=150.0, headers=source_supabase.supabase_headers(key)) as client:
        await source_supabase.upsert(client, "emendamenti", rows, "id")
        await source_supabase.rpc(
            client,
            "ricalcola_versioni",
            {"p_chiavi": sorted({r["chiave_naturale"] for r in rows})},
        )


def list_row_hash(r) -> str:
    return hashlib.sha256(
        "\x1f".join(
            [r.id, r.position, r.esito or "", " ".join(r.identici), r.primo_id_persona or ""]
        ).encode("utf-8")
    ).hexdigest()


# --- activities ---


@workflows.activity(start_to_close_timeout=timedelta(seconds=60), retry_policy_max_attempts=3)
async def atti_for_discovery(cursor: str | None, limit: int) -> list[tuple[str, str]]:
    return await db_atti_for_discovery(cursor, limit)


@workflows.activity(start_to_close_timeout=timedelta(seconds=600), retry_policy_max_attempts=2)
async def discover_atto_lists(numeri: list[str], legislature: int) -> DiscoveryResult:
    import httpx
    found = 0
    errors: list[str] = []
    rows: list[dict] = []
    seen_urls: set[str] = set()
    async with httpx.AsyncClient(timeout=60.0) as client:
        for numero in numeri:
            try:
                resp = await polite_get(
                    client, ATTO_PAGE.format(legislature=legislature, numero=numero),
                    timeout=camera_html_timeout(),
                )
                page = resp.content.decode("utf-8", errors="replace")
                for url in extract_list_urls(page):
                    # the same list URL can be linked from several atto pages of the
                    # batch: PostgREST rejects a payload with duplicate conflict keys
                    if url in seen_urls:
                        continue
                    parts = list_url_parts(url)
                    if parts is None:
                        continue
                    seen_urls.add(url)
                    atto_numero, sede = parts
                    rows.append(
                        {"url": url, "legislatura": legislature, "atto_numero": atto_numero, "sede": sede}
                    )
                    found += 1
            except Exception as e:
                errors.append(f"atto {numero}: {type(e).__name__}: {e}")
    if rows:
        await db_upsert_liste(rows)
    return DiscoveryResult(lists_found=found, errors=errors)


@workflows.activity(start_to_close_timeout=timedelta(seconds=60), retry_policy_max_attempts=3)
async def lists_to_check(atti: list[str] | None, limit: int) -> list[str]:
    return await db_lists_to_check(atti, limit)


@workflows.activity(start_to_close_timeout=timedelta(seconds=60), retry_policy_max_attempts=3)
async def lists_known_count() -> int:
    return await db_lists_known()


@workflows.activity(start_to_close_timeout=timedelta(seconds=900), retry_policy_max_attempts=2)
async def process_camera_list(url: str, max_detail: int) -> ListResult:
    import httpx
    parts = list_url_parts(url)
    atto_numero, sede = parts if parts else ("?", "?")
    chiave_prefix = f"camera:19:em:AC{atto_numero}:{sede}"
    state = await db_list_state(url)
    headers: dict[str, str] = {}
    if state and state.get("last_modified"):
        headers["If-Modified-Since"] = state["last_modified"]
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await polite_get(client, url, headers=headers, timeout=camera_html_timeout())
        if resp.status_code == 304:
            await db_list_touch(url)
            return ListResult(
                not_modified=True, rows_seen=state.get("righe") or 0, details_fetched=0,
                remaining_details=0, saved_rows=0, esito_updates=0, to_evaluate=[], errors=[],
            )
        page = resp.content.decode("utf-8-sig", errors="replace")
        last_modified = resp.headers.get("last-modified")
        list_rows = parse_list(page)
        db_map = await db_emendamenti_by_fonte(url)
        atti = await db_atti_flags(19)
        atto_flags = {}
        a = atti.get(f"http://dati.camera.it/ocd/attocamera.rdf/ac19_{atto_numero}")
        if a:
            if a.get("is_dl_conversione"):
                atto_flags["atto_dl"] = True
            if a.get("is_bilancio"):
                atto_flags["atto_bilancio"] = True
            if a.get("is_commissione_bilancio_referente"):
                atto_flags["atto_commissione_bilancio"] = True
        need_detail: list = []
        esito_updates = 0
        errors: list[str] = []
        for r in list_rows:
            row_id = f"{chiave_prefix}:{r.id}#{r.position}"
            existing = db_map.get(row_id)
            mh = list_row_hash(r)
            if existing is None:
                need_detail.append(r)
            elif existing.get("metadati_hash") != mh:
                if existing.get("esito") != r.esito:
                    # only the outcome changed: update esito + hash without refetching the detail
                    await db_save_eme_rows(
                        [
                            {
                                "id": row_id,
                                "legislatura": 19,
                                "esito": r.esito,
                                "metadati_hash": mh,
                                "aggiornato_at": datetime.now(timezone.utc).isoformat(),
                            }
                        ]
                    )
                    esito_updates += 1
                else:
                    need_detail.append(r)
        # fetch each needed proposal once, using the row with the latest position
        by_id: dict[str, object] = {}
        for r in need_detail:
            if r.id not in by_id or r.position > by_id[r.id].position:
                by_id[r.id] = r
        cap = min(max_detail, MAX_DETAIL_PER_CALL)
        to_fetch = list(by_id.values())[:cap]
        rows: list[dict] = []
        details_fetched = 0
        for r in to_fetch:
            try:
                dresp = await polite_get(client, detail_url(r.href or ""), timeout=camera_html_timeout())
                dpage = dresp.content.decode("utf-8", errors="replace")
                detail = parse_detail(dpage)
                item_rows = eme_rows(atto_numero, sede, r, detail, url)
                for row in item_rows:
                    row["rilevante_spesa"], row["rilevanza_motivo"] = calcola_rilevanza(
                        row["testo"], atto_flags
                    )
                rows.extend(item_rows)
                details_fetched += 1
            except Exception as e:
                errors.append(f"{r.id}: {type(e).__name__}: {e}")
        max_ordine: dict[str, int] = {}
        for rid, ex in db_map.items():
            if rid.startswith(chiave_prefix + ":"):
                k = rid.rsplit("#", 1)[0]
                max_ordine[k] = max(max_ordine.get(k, 0), ex.get("ordine_versione") or 0)
        for row in rows:
            k = row["chiave_naturale"]
            max_ordine[k] = max(max_ordine.get(k, 0), row["ordine_versione"])
        to_evaluate = [
            row["id"]
            for row in rows
            if row.get("rilevante_spesa") is True
            and row.get("testo")
            and row["ordine_versione"] == max_ordine.get(row["chiave_naturale"])
        ]
        if rows:
            await db_save_eme_rows(rows)
        if last_modified and details_fetched == len(to_fetch) and not errors:
            await db_list_store_result(url, last_modified, len(list_rows))
    return ListResult(
        not_modified=False,
        rows_seen=len(list_rows),
        details_fetched=details_fetched,
        remaining_details=len(by_id) - details_fetched,
        saved_rows=len(rows),
        esito_updates=esito_updates,
        to_evaluate=to_evaluate,
        errors=errors,
    )


@workflows.workflow.define(
    name="camera-emendamenti-giornalieri",
    workflow_display_name="Daily amendments (Camera)",
    workflow_description=(
        "Discovers the amendment lists of Camera bills (camera.it atto pages), checks them with "
        "conditional GET, fetches the details of new or changed amendments from documenti.camera.it, "
        "saves every version to Supabase and evaluates the money-relevant current versions with Mistral."
    ),
    execution_timeout=timedelta(hours=2),
)
class DailyCameraEmendamentiWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: DailyCameraEmendamentiInput) -> DailyCameraEmendamentiOutput:
        errors: list[str] = []
        evaluation_errors: list[str] = []
        atti_discovered = 0
        details_so_far = 0
        stopped = False
        eval_ids: list[str] = []
        lists_checked = 0
        lists_not_modified = 0
        rows_seen = 0
        details_fetched = 0
        saved_rows = 0
        esito_updates = 0

        # (1) discovery
        atti_rows: list[tuple[str, str]] = []
        if input.atti:
            batches = [input.atti[i : i + DISCOVERY_BATCH] for i in range(0, len(input.atti), DISCOVERY_BATCH)]
        else:
            cursor_value = await camera_cursor_get(CURSOR_NAME)
            atti_rows = await atti_for_discovery(cursor_value, input.max_atti)
            batches = [ [n for n, _ in atti_rows[i : i + DISCOVERY_BATCH]] for i in range(0, len(atti_rows), DISCOVERY_BATCH) ]
            atti_discovered = len(atti_rows)
        for i, batch in enumerate(batches):
            result = await discover_atto_lists(batch, input.legislature)
            errors.extend(result.errors)
            # cursor advances only after a batch has been discovered and stored
            if atti_rows:
                last = atti_rows[min((i + 1) * DISCOVERY_BATCH, len(atti_rows)) - 1]
                await camera_cursor_set(CURSOR_NAME, last[1])
        lists_known = await lists_known_count()

        # (2) check the lists
        urls = await lists_to_check(input.atti, input.max_liste)
        for url in urls:
            budget = input.max_detail - details_so_far
            if budget <= 0:
                stopped = True
                break
            while True:
                result = await process_camera_list(url, budget)
                lists_checked += 1
                if result.not_modified:
                    lists_not_modified += 1
                rows_seen += result.rows_seen
                details_fetched += result.details_fetched
                details_so_far += result.details_fetched
                saved_rows += result.saved_rows
                esito_updates += result.esito_updates
                eval_ids.extend(result.to_evaluate)
                errors.extend(result.errors)
                if result.not_modified or result.remaining_details <= 0:
                    break
                budget = input.max_detail - details_so_far
                if budget <= 0:
                    stopped = True
                    break
            if stopped:
                break

        # (3) evaluate
        evaluated = 0
        reused = 0
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

        return DailyCameraEmendamentiOutput(
            atti_discovered=atti_discovered,
            lists_known=lists_known,
            lists_checked=lists_checked,
            lists_not_modified=lists_not_modified,
            rows_seen=rows_seen,
            details_fetched=details_fetched,
            saved_rows=saved_rows,
            esito_updates=esito_updates,
            stopped_by_cap=stopped,
            evaluated=evaluated,
            reused_evaluations=reused,
            errors=errors,
            evaluation_errors=evaluation_errors,
        )
