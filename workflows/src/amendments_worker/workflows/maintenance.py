"""Maintenance workflow: recomputes testo_hash and the money-relevance pre-filter
(rilevante_spesa, rilevanza_motivo) for stored amendments, in keyset pages of 1000.

Run once after migration 0006 is applied (backfills the Senate rows) and after any
rule change (R1 -> R2/R3). No texts cross the activity boundary: reading, computing and
writing all happen inside the maintenance_page activity.
"""
from __future__ import annotations


from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, Field

import mistralai.workflows as workflows

from ..sources import supabase as source_supabase
from ..sources.common import testo_hash
from ..sources.rilevanza import calcola_rilevanza

MAINTENANCE_PAGE = 1000


class MaintenanceInput(BaseModel):
    only_missing: bool = True
    regola: Literal["R1", "R2", "R3"] = "R1"


class MaintenancePageResult(BaseModel):
    read: int
    updated: int
    last_id: str | None
    errors: list[str]


class MaintenanceOutput(BaseModel):
    read: int
    updated: int
    errors: list[str]


_PAGE_SELECT = (
    "id,legislatura,camera,tipo,numero,atto_uri,testo,testo_hash,rilevante_spesa,rilevanza_motivo"
)


async def db_maintenance_read(after_id: str | None, only_missing: bool) -> list[dict]:
    import httpx
    base, key = source_supabase.supabase_base_and_key()
    filters = {"rilevante_spesa": "is.null"} if only_missing else {}
    if after_id is not None:
        filters["id"] = f"gt.{after_id}"
    async with httpx.AsyncClient(timeout=90.0, headers=source_supabase.supabase_headers(key)) as client:
        resp = await client.get(
            f"{base}/rest/v1/emendamenti",
            params={"select": _PAGE_SELECT, "order": "id.asc", "limit": str(MAINTENANCE_PAGE), **filters},
            headers=source_supabase.supabase_headers(key),
        )
        resp.raise_for_status()
        return resp.json()


async def db_atti_flags_all() -> dict[str, dict]:
    import httpx
    base, key = source_supabase.supabase_base_and_key()
    async with httpx.AsyncClient(timeout=90.0, headers=source_supabase.supabase_headers(key)) as client:
        rows = await source_supabase.select_all(
            client,
            "atti",
            "uri,is_dl_conversione,is_bilancio,is_commissione_bilancio_referente",
            key="uri",
        )
    return {r["uri"]: r for r in rows}


async def db_maintenance_write(rows: list[dict]) -> None:
    import httpx
    base, key = source_supabase.supabase_base_and_key()
    async with httpx.AsyncClient(timeout=150.0, headers=source_supabase.supabase_headers(key)) as client:
        await source_supabase.upsert(client, "emendamenti", rows, "id")


def row_atto_flags(row: dict, atti: dict[str, dict]) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    a = atti.get(row.get("atto_uri") or "")
    if a:
        if a.get("is_dl_conversione"):
            flags["atto_dl"] = True
        if a.get("is_bilancio"):
            flags["atto_bilancio"] = True
        if a.get("is_commissione_bilancio_referente"):
            flags["atto_commissione_bilancio"] = True
    if row.get("camera") == "camera":
        # dc:title is not stored: keep atto_bilancio_interno if it was computed at ingest
        # (from dc:title) or derive it from the identifier (9/DOC.VIII, = Camera internal budget)
        numero = (row.get("numero") or "").upper()
        motivi = row.get("rilevanza_motivo") or []
        if numero.startswith("9/DOC.VIII,") or "atto_bilancio_interno" in motivi:
            flags["atto_bilancio_interno"] = True
    return flags


def compute_maintenance_updates(rows: list[dict], atti: dict[str, dict], regola: str) -> list[dict]:
    updates = []
    for row in rows:
        flags = row_atto_flags(row, atti)
        th = testo_hash(row.get("testo"))
        rilevante, motivi = calcola_rilevanza(row.get("testo"), flags, regola=regola)
        updates.append(
            {
                "id": row["id"],
                "legislatura": row["legislatura"],
                "testo_hash": th,
                "rilevante_spesa": rilevante,
                "rilevanza_motivo": motivi,
            }
        )
    return updates


@workflows.activity(start_to_close_timeout=timedelta(seconds=300), retry_policy_max_attempts=2)
async def maintenance_page(after_id: str | None, only_missing: bool, regola: str) -> MaintenancePageResult:
    rows = await db_maintenance_read(after_id, only_missing)
    atti = await db_atti_flags_all()
    updates = compute_maintenance_updates(rows, atti, regola)
    errors: list[str] = []
    updated = 0
    if updates:
        try:
            await db_maintenance_write(updates)
            updated = len(updates)
        except Exception as e:
            errors.append(f"write: {type(e).__name__}: {e}")
    return MaintenancePageResult(
        read=len(rows),
        updated=updated,
        last_id=rows[-1]["id"] if rows else None,
        errors=errors,
    )


@workflows.workflow.define(
    name="manutenzione-emendamenti",
    workflow_display_name="Maintenance: hashes and relevance",
    workflow_description=(
        "Recomputes testo_hash and the money-relevance pre-filter (rilevante_spesa, "
        "rilevanza_motivo) for stored amendments, in pages of 1000."
    ),
    execution_timeout=timedelta(hours=2),
)
class MaintenanceWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: MaintenanceInput) -> MaintenanceOutput:
        read = 0
        updated = 0
        errors: list[str] = []
        after_id: str | None = None
        while True:
            result = await maintenance_page(after_id, input.only_missing, input.regola)
            read += result.read
            updated += result.updated
            errors.extend(result.errors)
            if result.read < MAINTENANCE_PAGE:
                break
            after_id = result.last_id
        return MaintenanceOutput(read=read, updated=updated, errors=errors)
