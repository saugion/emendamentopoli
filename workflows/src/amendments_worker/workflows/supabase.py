"""Supabase adapter: all PostgREST details (env config, headers, chunking,
timeouts, retry on reads, column aliases, row mapping) live behind this small
interface. The workflow activities are thin wrappers that call these functions;
tests exercise them through the same seam with an httpx mock transport.
"""
from __future__ import annotations


import asyncio
import json
import os
from typing import TYPE_CHECKING


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


async def _get(
    base: str,
    headers: dict[str, str],
    url: str,
    params: dict[str, str] | None = None,
) -> object:
    import httpx
    async with httpx.AsyncClient(timeout=READ_TIMEOUT, headers=headers) as client:
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(5 * attempt)
            resp = await client.get(url, params=params)
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
    import httpx
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
        "rubrica_versione": v.rubric_version,
        "testo_hash": v.text_hash,
        "copiata_da": v.copied_from,
        "impatto_finanziario": v.financial_impact,
        "alloca_risorse": v.allocates_resources,
        "importo_stimato_eur": v.estimated_amount_eur,
        "motivazione_finanziaria": v.financial_rationale,
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


async def insert_evaluations(evaluations: list["Evaluation"]) -> int:
    # idempotent: unique index (emendamento_id, rubrica_versione) + ignore-duplicates;
    # punteggi are matched to the returned rows by emendamento_id, never by position
    base, headers = _config()
    total = 0
    for i in range(0, len(evaluations), SAVE_CHUNK):
        chunk = evaluations[i : i + SAVE_CHUNK]
        inserted = await _post(
            base,
            headers,
            "/rest/v1/valutazioni?on_conflict=emendamento_id,rubrica_versione",
            [evaluation_row(v) for v in chunk],
            "resolution=ignore-duplicates,return=representation",
        )
        by_amendment = {v.amendment_id: v for v in chunk}
        score_payload = [
            row
            for ins in inserted
            for row in score_rows(ins["id"], by_amendment[ins["emendamento_id"]])
        ]
        if score_payload:
            await _post(base, headers, "/rest/v1/punteggi", score_payload, None)
        total += len(inserted)
    return total


async def amendments_without_evaluation(limit: int) -> list[Amendment]:
    # anti-join: select=...,valutazioni() + valutazioni=is.null (embedded filters like
    # valutazioni.id=is.null do NOT filter the top-level rows; this form does)
    base, headers = _config()
    params = {
        "select": (
            "id,legislature:legislatura,number:numero,type:tipo,committee_seat:sede_commissione,"
            "act_uri:atto_uri,bill:ddl,text_url:url_testo,text_xml_url:url_testo_xml,date:data,text:testo"
            ",chamber:camera,relevant_spending:rilevante_spesa,valutazioni()"
        ),
        "valutazioni": "is.null",
        "corrente": "is.true",
        "testo": "not.is.null",
        "rilevante_spesa": "is.true",
        "order": "data.desc.nullslast",
        "limit": str(limit),
    }
    rows = await _get(base, headers, f"{base}/rest/v1/emendamenti", params)
    return [Amendment(**{k: r[k] for k in Amendment.model_fields if k in r}) for r in rows]
