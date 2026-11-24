"""Valutazione LLM degli emendamenti (rubrica di interesse pubblico) e salvataggio su Supabase.

La chiamata a Mistral avviene tramite il plugin dell'SDK (get_mistral_client), che usa
l'identita' del run: nel deployment gestito e' il service account (workflow_executor),
quindi non serve MISTRAL_API_KEY.
"""

import os
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

import mistralai.workflows as workflows

from .modelli import Emendamento

MODELLO = "mistral-small-latest"
RUBRICA = {"trasparenza": 0.3, "salute": 0.25, "ambiente": 0.25, "concretezza": 0.2}


class Punteggio(BaseModel):
    criterio: str
    punteggio: float  # 0-10
    motivazione: str


class Valutazione(BaseModel):
    emendamento_id: str
    punteggi: list[Punteggio]
    score_totale: float  # 0-10, media pesata
    riepilogo: str
    modello: str
    ts: str


class _PunteggioJSON(BaseModel):
    punteggio: float
    motivazione: str


class ValutazioneJSON(BaseModel):
    punteggi: dict[str, _PunteggioJSON]
    riepilogo: str


def prompt_valutazione(eme: Emendamento) -> str:
    tipo = "Ordine del giorno" if eme.tipo == "G" else "Emendamento"
    sede = "Commissione" if eme.sede_commissione else "Assemblea"
    return (
        "Sei un analista parlamentare indipendente. Valuta l'utilita' per l'interesse pubblico "
        "di questo emendamento italiano.\n\n"
        f"EMENDAMENTO: {tipo} {eme.numero} al {eme.ddl or eme.atto_uri} "
        f"(Senato, {eme.data or 'data n/d'}, sede: {sede})\n"
        f'TESTO:\n"""{eme.testo or "(testo non disponibile)"}"""\n\n'
        "Criteri (punteggio 0-10 ciascuno, con motivazione di 1-2 frasi):\n"
        "- trasparenza: contributo a trasparenza democratica, legalita', accountability\n"
        "- salute: impatto (diretto o indiretto) su salute pubblica\n"
        "- ambiente: impatto su ambiente, clima, territorio\n"
        "- concretezza: efficacia tecnica e specificita' (punta basso le disposizioni vaghe o solo propagandistiche)\n\n"
        "Rispondi SOLO con JSON valido:\n"
        '{"punteggi":{"trasparenza":{"punteggio":N,"motivazione":"..."},'
        '"salute":{"punteggio":N,"motivazione":"..."},'
        '"ambiente":{"punteggio":N,"motivazione":"..."},'
        '"concretezza":{"punteggio":N,"motivazione":"..."}},'
        '"riepilogo":"2-3 frasi sul valore complessivo"}'
    )


def score_composito(punteggi: list[Punteggio]) -> float:
    tot = sum(RUBRICA.get(p.criterio, 0) * p.punteggio for p in punteggi)
    return round(tot, 1)


def riga_valutazione(v: Valutazione) -> dict:
    return {
        "emendamento_id": v.emendamento_id,
        "score_totale": v.score_totale,
        "riepilogo": v.riepilogo,
        "modello": v.modello,
        "ts": v.ts,
    }


def righe_punteggi(valutazione_id: int, v: Valutazione) -> list[dict]:
    return [
        {
            "valutazione_id": valutazione_id,
            "criterio": p.criterio,
            "punteggio": p.punteggio,
            "motivazione": p.motivazione,
        }
        for p in v.punteggi
    ]


@workflows.activity(start_to_close_timeout=timedelta(seconds=120), retry_policy_max_attempts=2)
async def valuta_emendamento(eme: Emendamento) -> Valutazione:
    from mistralai.workflows.plugins.mistralai import get_mistral_client

    client = get_mistral_client()
    resp = await client.chat.complete_async(
        model=MODELLO,
        messages=[{"role": "user", "content": prompt_valutazione(eme)}],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    parsed = ValutazioneJSON.model_validate_json(resp.choices[0].message.content)
    punteggi = [
        Punteggio(criterio=c, punteggio=p.punteggio, motivazione=p.motivazione)
        for c, p in parsed.punteggi.items()
    ]
    return Valutazione(
        emendamento_id=eme.id,
        punteggi=punteggi,
        score_totale=score_composito(punteggi),
        riepilogo=parsed.riepilogo,
        modello=MODELLO,
        ts=datetime.now(timezone.utc).isoformat(),
    )


@workflows.activity(start_to_close_timeout=timedelta(seconds=180), retry_policy_max_attempts=2)
async def salva_valutazioni(valutazioni: list[Valutazione]) -> int:
    import httpx

    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_KEY"]
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=150.0) as client:
        resp = await client.post(
            f"{base}/rest/v1/valutazioni",
            headers={**headers, "Prefer": "return=representation"},
            json=[riga_valutazione(v) for v in valutazioni],
        )
        resp.raise_for_status()
        inserted = resp.json()
        punteggi_rows = [
            riga
            for v, row in zip(valutazioni, inserted)
            for riga in righe_punteggi(row["id"], v)
        ]
        if punteggi_rows:
            resp2 = await client.post(
                f"{base}/rest/v1/punteggi", headers=headers, json=punteggi_rows
            )
            resp2.raise_for_status()
    return len(valutazioni)
