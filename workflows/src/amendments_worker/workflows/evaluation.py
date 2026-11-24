"""LLM evaluation of amendments (public-interest rubric) and saving to Supabase.

The Mistral call goes through the SDK plugin (get_mistral_client), which uses
the run identity: in the managed deployment it is the service account
(workflow_executor), so no MISTRAL_API_KEY is needed.
"""

import os
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field

import mistralai.workflows as workflows

from .models import SAVE_CHUNK, Amendment

MODEL = "mistral-small-latest"
RUBRIC = {"trasparenza": 0.3, "salute": 0.25, "ambiente": 0.25, "concretezza": 0.2}


class Score(BaseModel):
    criterion: str
    score: float  # 0-10
    rationale: str


class Evaluation(BaseModel):
    amendment_id: str
    scores: list[Score]
    total_score: float  # 0-10, weighted average
    summary: str
    model: str
    ts: str


class _ScoreJSON(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    score: float = Field(alias="punteggio")
    rationale: str = Field(alias="motivazione")


class EvaluationJSON(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    scores: dict[str, _ScoreJSON] = Field(alias="punteggi")
    summary: str = Field(alias="riepilogo")


def evaluation_prompt(eme: Amendment) -> str:
    kind = "Ordine del giorno" if eme.type == "G" else "Emendamento"
    seat = "Commissione" if eme.committee_seat else "Assemblea"
    return (
        "Sei un analista parlamentare indipendente. Valuta l'utilita' per l'interesse pubblico "
        "di questo emendamento italiano.\n\n"
        f"EMENDAMENTO: {kind} {eme.number} al {eme.bill or eme.act_uri} "
        f"(Senato, {eme.date or 'data n/d'}, sede: {seat})\n"
        f'TESTO:\n"""{eme.text or "(testo non disponibile)"}"""\n\n'
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


def composite_score(scores: list[Score]) -> float:
    tot = sum(RUBRIC.get(s.criterion, 0) * s.score for s in scores)
    return round(tot, 1)


def evaluation_row(v: Evaluation) -> dict:
    return {
        "emendamento_id": v.amendment_id,
        "score_totale": v.total_score,
        "riepilogo": v.summary,
        "modello": v.model,
        "ts": v.ts,
    }


def score_rows(evaluation_id: int, v: Evaluation) -> list[dict]:
    return [
        {
            "valutazione_id": evaluation_id,
            "criterio": s.criterion,
            "punteggio": s.score,
            "motivazione": s.rationale,
        }
        for s in v.scores
    ]


@workflows.activity(start_to_close_timeout=timedelta(seconds=120), retry_policy_max_attempts=2)
async def evaluate_amendment(eme: Amendment) -> Evaluation:
    from mistralai.workflows.plugins.mistralai import get_mistral_client

    client = get_mistral_client()
    resp = await client.chat.complete_async(
        model=MODEL,
        messages=[{"role": "user", "content": evaluation_prompt(eme)}],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    parsed = EvaluationJSON.model_validate_json(resp.choices[0].message.content)
    scores = [
        Score(criterion=c, score=s.score, rationale=s.rationale)
        for c, s in parsed.scores.items()
    ]
    return Evaluation(
        amendment_id=eme.id,
        scores=scores,
        total_score=composite_score(scores),
        summary=parsed.summary,
        model=MODEL,
        ts=datetime.now(timezone.utc).isoformat(),
    )


@workflows.activity(start_to_close_timeout=timedelta(seconds=180), retry_policy_max_attempts=2)
async def save_evaluations(evaluations: list[Evaluation]) -> int:
    import httpx

    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_KEY"]
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    total = 0
    async with httpx.AsyncClient(timeout=150.0) as client:
        for i in range(0, len(evaluations), SAVE_CHUNK):
            chunk = evaluations[i : i + SAVE_CHUNK]
            resp = await client.post(
                f"{base}/rest/v1/valutazioni",
                headers={**headers, "Prefer": "return=representation"},
                json=[evaluation_row(v) for v in chunk],
            )
            resp.raise_for_status()
            inserted = resp.json()
            score_rows_payload = [
                row
                for v, row in zip(chunk, inserted)
                for row in score_rows(row["id"], v)
            ]
            if score_rows_payload:
                resp2 = await client.post(
                    f"{base}/rest/v1/punteggi", headers=headers, json=score_rows_payload
                )
                resp2.raise_for_status()
            total += len(chunk)
    return total
