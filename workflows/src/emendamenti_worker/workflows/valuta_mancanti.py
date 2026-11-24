"""Backfill: valuta gli emendamenti gia' in Supabase che non hanno ancora una valutazione."""

import asyncio
import os
from datetime import timedelta

from pydantic import BaseModel, Field

import mistralai.workflows as workflows

from .modelli import Emendamento
from .valutazione import salva_valutazioni, valuta_emendamento


class ValutaMancantiInput(BaseModel):
    limite: int = Field(default=500, ge=1, le=5000, description="Quanti emendamenti senza valutazione processare al massimo")


class ValutaMancantiOutput(BaseModel):
    trovati: int
    valutati: int
    errori: list[str]
    ids: list[str]


@workflows.activity(start_to_close_timeout=timedelta(seconds=120), retry_policy_max_attempts=3)
async def emendamenti_senza_valutazione(limite: int) -> list[Emendamento]:
    import httpx

    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_KEY"]
    url = (
        f"{base}/rest/v1/emendamenti"
        "?select=id,legislatura,numero,tipo,sede_commissione,atto_uri,ddl,url_testo,url_testo_xml,data,testo"
        ",valutazioni!left(id)"
        f"&valutazioni.id=is.null&order=created_at.desc&limit={limite}"
    )
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.get(
            url, headers={"apikey": key, "Authorization": f"Bearer {key}"}
        )
        resp.raise_for_status()
        rows = resp.json()
    return [Emendamento(**{k: r[k] for k in Emendamento.model_fields if k in r}) for r in rows]


@workflows.workflow.define(
    name="valuta-emendamenti-mancanti",
    workflow_display_name="Valuta emendamenti mancanti (backfill)",
    workflow_description=(
        "Legge da Supabase gli emendamenti senza valutazione (anti-join su valutazioni), "
        "li valuta con Mistral secondo la rubrica di interesse pubblico e scrive "
        "valutazioni + punteggi."
    ),
    execution_timeout=timedelta(hours=2),
)
class ValutaEmendamentiMancantiWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: ValutaMancantiInput) -> ValutaMancantiOutput:
        mancanti = await emendamenti_senza_valutazione(input.limite)
        risultati = await asyncio.gather(
            *[valuta_emendamento(e) for e in mancanti],
            return_exceptions=True,
        )
        errori = [
            f"{m.id}: {type(r).__name__}: {r}"
            for m, r in zip(mancanti, risultati)
            if isinstance(r, BaseException)
        ]
        valutazioni = [r for r in risultati if not isinstance(r, BaseException)]
        valutati = await salva_valutazioni(valutazioni) if valutazioni else 0
        return ValutaMancantiOutput(
            trovati=len(mancanti),
            valutati=valutati,
            errori=errori,
            ids=[v.emendamento_id for v in valutazioni],
        )
