"""Workflow: Camera bills (ocd:atto) from dati.camera.it SPARQL -> atti table.

Incremental mode uses the camera_atti_modified cursor (ods:modified, 2-day overlap);
full=True ignores the cursor and re-reads all bills of the legislature. Keyset pagination,
dedupe by URI, upsert on uri.
"""

from datetime import datetime, timedelta

import httpx
from pydantic import BaseModel, Field

import mistralai.workflows as workflows

from ..sources import common
from ..sources import supabase as source_supabase
from ..sources.camera_atti import (
    CAMERA_ATTI_PAGE,
    CameraAttoItem,
    atti_row,
    parse_atti_bindings,
    query_camera_atti,
)
from ..sources.common import SPARQL_CAMERA_ENDPOINT, polite_get
from .daily_camera_odg import camera_cursor_get, camera_cursor_set

CURSOR_NAME = "camera_atti_modified"
ATTI_CHUNK = 200


class CameraAttiInput(BaseModel):
    legislature: int = Field(default=19, ge=19, le=19)
    full: bool = False


class CameraAttiOutput(BaseModel):
    since: str
    cursor: str
    read: int
    saved: int
    dl: int
    bilancio: int
    commissione_bilancio: int
    errors: list[str]


async def db_save_atti(rows: list[dict]) -> None:
    base, key = source_supabase.supabase_base_and_key()
    async with httpx.AsyncClient(timeout=150.0, headers=source_supabase.supabase_headers(key)) as client:
        await source_supabase.upsert(client, "atti", rows, "uri")


@workflows.activity(start_to_close_timeout=timedelta(seconds=300), retry_policy_max_attempts=1)
async def fetch_camera_atti_page(legislature: int, since: str) -> list[CameraAttoItem]:
    q = query_camera_atti(legislature, since)
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await polite_get(
            client,
            SPARQL_CAMERA_ENDPOINT,
            params={"query": q, "format": "application/sparql-results+json"},
            headers={"Accept": "application/sparql-results+json"},
            timeout=common.CAMERA_SPARQL_TIMEOUT,
        )
    return parse_atti_bindings(resp.json()["results"]["bindings"])


@workflows.activity(start_to_close_timeout=timedelta(seconds=600), retry_policy_max_attempts=2)
async def process_atti_chunk(items: list[CameraAttoItem], legislature: int) -> int:
    rows = [atti_row(i, legislature) for i in items]
    await db_save_atti(rows)
    return len(rows)


@workflows.workflow.define(
    name="camera-atti",
    workflow_display_name="Camera bills (atti)",
    workflow_description=(
        "Downloads from dati.camera.it (SPARQL, CC BY-SA 4.0) the Camera bills changed since the cursor, "
        "cleans titles and flags decree-law conversions, budget laws and bills referred to the V Commissione "
        "Bilancio; upserts them into the atti table."
    ),
    execution_timeout=timedelta(minutes=30),
)
class CameraAttiWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: CameraAttiInput) -> CameraAttiOutput:
        if input.full:
            since = "2000-01-01T00:00:00Z"
        else:
            cursor_value = await camera_cursor_get(CURSOR_NAME)
            if cursor_value:
                since_dt = datetime.fromisoformat(cursor_value.replace("Z", "+00:00")) - timedelta(days=2)
                since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                since = "2000-01-01T00:00:00Z"
        read = 0
        saved = 0
        dl = bilancio = comm_bil = 0
        errors: list[str] = []
        cursor = since
        seen: set[str] = set()
        while True:
            page = await fetch_camera_atti_page(input.legislature, cursor)
            new = [i for i in page if i.uri not in seen]
            seen |= {i.uri for i in new}
            read += len(new)
            for start in range(0, len(new), ATTI_CHUNK):
                chunk = new[start : start + ATTI_CHUNK]
                try:
                    saved += await process_atti_chunk(chunk, input.legislature)
                except Exception as e:
                    errors.append(f"atti chunk: {type(e).__name__}: {e}")
                for i in chunk:
                    row = atti_row(i, input.legislature)
                    dl += row["is_dl_conversione"]
                    bilancio += row["is_bilancio"]
                    comm_bil += row["is_commissione_bilancio_referente"]
            if len(page) < CAMERA_ATTI_PAGE:
                break
            if not new:
                raise RuntimeError("keyset stalled: >= 1000 atti share ods:modified " + cursor)
            cursor = page[-1].modified
        if not input.full:
            await camera_cursor_set(CURSOR_NAME, cursor)
        return CameraAttiOutput(
            since=since,
            cursor=cursor,
            read=read,
            saved=saved,
            dl=dl,
            bilancio=bilancio,
            commissione_bilancio=comm_bil,
            errors=errors,
        )
