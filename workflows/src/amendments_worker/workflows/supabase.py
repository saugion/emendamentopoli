"""Supabase adapter: all PostgREST details (env config, headers, chunking,
timeouts, retry on reads, column aliases, row mapping) live behind this small
interface. The workflow activities are thin wrappers that call these functions;
tests exercise them through the same seam with an httpx mock transport.
"""

import asyncio
import json
import os
from typing import TYPE_CHECKING

import httpx

from .models import Amendment

if TYPE_CHECKING:
    from .evaluation import Evaluation

SAVE_CHUNK = 500
READ_TIMEOUT = 90.0
WRITE_TIMEOUT = 150.0
_RETRYABLE = (403, 429, 500, 502, 503)


def _config() -> tuple[str, dict[str, str]]:
    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_KEY"]
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    return base, headers


async def _get(base: str, headers: dict[str, str], url: str) -> object:
    async with httpx.AsyncClient(timeout=READ_TIMEOUT, headers=headers) as client:
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(5 * attempt)
            resp = await client.get(url)
            if resp.status_code in _RETRYABLE and attempt < 2:
                continue
            resp.raise_for_status()
            return resp.json()
    raise RuntimeError("unreachable")


async def _post(
    base: str,
    headers: dict[str, str],
    path: str,
    payload: list[dict],
    prefer: str | None,
) -> object:
    request_headers = {**headers, **({"Prefer": prefer} if prefer else {})}
    async with httpx.AsyncClient(timeout=WRITE_TIMEOUT, headers=request_headers) as client:
        resp = await client.post(f"{base}{path}", json=payload)
        resp.raise_for_status()
        text = resp.text
        return json.loads(text) if text else None


def amendment_row(a: Amendment) -> dict:
    return {
        "id": a.id,
        "legislatura": a.legislature,
        "numero": a.number,
        "tipo": a.type,
        "sede_commissione": a.committee_seat,
        "atto_uri": a.act_uri,
        "ddl": a.bill,
        "url_testo": a.text_url,
        "url_testo_xml": a.text_xml_url,
        "data": a.date,
        "testo": a.text,
        "firmatari": ", ".join(a.signatories) if a.signatories else None,
        "firmatari_uri": ", ".join(a.signatory_uris) if a.signatory_uris else None,
    }


def evaluation_row(v: "Evaluation") -> dict:
    return {
        "emendamento_id": v.amendment_id,
        "score_totale": v.total_score,
        "riepilogo": v.summary,
        "modello": v.model,
        "ts": v.ts,
    }


def score_rows(evaluation_id: int, v: "Evaluation") -> list[dict]:
    return [
        {
            "valutazione_id": evaluation_id,
            "criterio": s.criterion,
            "punteggio": s.score,
            "motivazione": s.rationale,
        }
        for s in v.scores
    ]


async def saved_amendments_map(legislature: int) -> dict[str, dict[str, str | None]]:
    base, headers = _config()
    url = (
        f"{base}/rest/v1/emendamenti"
        f"?select=id,data,firmatari&legislatura=eq.{legislature}&limit=100000"
    )
    rows = await _get(base, headers, url)
    return {r["id"]: {"data": r["data"], "firmatari": r["firmatari"]} for r in rows}


async def upsert_amendments(amendments: list[Amendment]) -> int:
    base, headers = _config()
    rows = [amendment_row(a) for a in amendments]
    total = 0
    for i in range(0, len(rows), SAVE_CHUNK):
        chunk = rows[i : i + SAVE_CHUNK]
        await _post(
            base,
            headers,
            "/rest/v1/emendamenti?on_conflict=id",
            chunk,
            "resolution=merge-duplicates",
        )
        total += len(chunk)
    return total


async def insert_evaluations(evaluations: list["Evaluation"]) -> int:
    base, headers = _config()
    total = 0
    for i in range(0, len(evaluations), SAVE_CHUNK):
        chunk = evaluations[i : i + SAVE_CHUNK]
        inserted = await _post(
            base,
            headers,
            "/rest/v1/valutazioni",
            [evaluation_row(v) for v in chunk],
            "return=representation",
        )
        score_payload = [
            row
            for v, inserted_row in zip(chunk, inserted)
            for row in score_rows(inserted_row["id"], v)
        ]
        if score_payload:
            await _post(base, headers, "/rest/v1/punteggi", score_payload, None)
        total += len(chunk)
    return total


async def amendments_without_evaluation(limit: int) -> list[Amendment]:
    base, headers = _config()
    url = (
        f"{base}/rest/v1/emendamenti"
        "?select=id,legislature:legislatura,number:numero,type:tipo,committee_seat:sede_commissione,"
        "act_uri:atto_uri,bill:ddl,text_url:url_testo,text_xml_url:url_testo_xml,date:data,text:testo"
        ",valutazioni!left(id)"
        f"&valutazioni.id=is.null&order=created_at.desc&limit={limit}"
    )
    rows = await _get(base, headers, url)
    return [Amendment(**{k: r[k] for k in Amendment.model_fields if k in r}) for r in rows]
