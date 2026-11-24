"""LLM evaluation of amendments (public-interest rubric) and saving to Supabase.

The Mistral call goes through the SDK plugin (get_mistral_client), which uses
the run identity: in the managed deployment it is the service account
(workflow_executor), so no MISTRAL_API_KEY is needed.
"""

import math
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

import mistralai.workflows as workflows

from ..sources import supabase as source_supabase
from ..sources.common import testo_hash
from . import supabase
from .models import Amendment

MODEL = "mistral-small-latest"
RUBRIC = {"trasparenza": 0.3, "salute": 0.25, "ambiente": 0.25, "concretezza": 0.2}
RUBRIC_VERSION = 2
CHAMBER_LABEL = {"senato": "Senato", "camera": "Camera"}
CHAMBER_SUBJECT = {"senato": "Il Senato", "camera": "La Camera"}


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
    rubric_version: int = 1
    text_hash: str | None = None
    financial_impact: float | None = None
    allocates_resources: bool | None = None
    estimated_amount_eur: float | None = None
    financial_rationale: str | None = None
    copied_from: int | None = None


class _ScoreJSON(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    score: float = Field(alias="punteggio")
    rationale: str = Field(alias="motivazione")


class EvaluationJSON(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    scores: dict[str, _ScoreJSON] = Field(alias="punteggi")
    summary: str = Field(alias="riepilogo")
    financial_impact: _ScoreJSON | None = Field(default=None, alias="impatto_finanziario")
    allocates_resources: bool | None = Field(default=None, alias="alloca_risorse")
    estimated_amount_eur: float | None = Field(default=None, alias="importo_stimato_eur")


def evaluation_prompt(eme: Amendment) -> str:
    kind = "Ordine del giorno" if eme.type == "G" else "Emendamento"
    seat = "Commissione" if eme.committee_seat else "Assemblea"
    chamber = CHAMBER_LABEL.get(eme.chamber, "Senato")
    subject = CHAMBER_SUBJECT.get(eme.chamber, "Il Senato")
    return (
        "Sei un analista parlamentare indipendente. Valuta l'utilita' per l'interesse pubblico "
        "di questo emendamento italiano.\n\n"
        f"EMENDAMENTO: {kind} {eme.number} al {eme.bill or eme.act_uri} "
        f"({chamber}, {eme.date or 'data n/d'}, sede: {seat})\n"
        f"{subject} e' l'organo presso cui l'atto e' stato presentato.\n"
        f'TESTO:\n"""{eme.text or "(testo non disponibile)"}"""\n\n'
        "Criteri (punteggio 0-10 ciascuno, con motivazione di 1-2 frasi):\n"
        "- trasparenza: contributo a trasparenza democratica, legalita', accountability\n"
        "- salute: impatto (diretto o indiretto) su salute pubblica\n"
        "- ambiente: impatto su ambiente, clima, territorio\n"
        "- concretezza: efficacia tecnica e specificita' (punta basso le disposizioni vaghe o solo propagandistiche)\n\n"
        "Valuta inoltre, separatamente e senza farlo pesare sui criteri precedenti:\n"
        "- impatto_finanziario: quanto l'atto incide sull'allocazione di denaro pubblico (0 = nessun effetto "
        "sulla spesa o sulle entrate; 10 = stanzia, sposta o taglia risorse rilevanti), con motivazione di 1-2 frasi\n"
        "- alloca_risorse: true se l'atto stanzia, aumenta, riduce o sposta risorse pubbliche "
        "(anche come impegno al Governo), altrimenti false\n"
        "- importo_stimato_eur: importo complessivo in euro esplicitamente indicato nel testo "
        "(numero senza separatori, es. 5000000); null se il testo non indica un importo\n\n"
        "Rispondi SOLO con JSON valido:\n"
        '{"punteggi":{"trasparenza":{"punteggio":N,"motivazione":"..."},'
        '"salute":{"punteggio":N,"motivazione":"..."},'
        '"ambiente":{"punteggio":N,"motivazione":"..."},'
        '"concretezza":{"punteggio":N,"motivazione":"..."}},'
        '"riepilogo":"2-3 frasi sul valore complessivo",'
        '"impatto_finanziario":{"punteggio":N,"motivazione":"..."},'
        '"alloca_risorse":true,'
        '"importo_stimato_eur":null}'
    )


def composite_score(scores: list[Score]) -> float:
    tot = sum(RUBRIC.get(s.criterion, 0) * s.score for s in scores)
    return round(tot, 1)


def build_evaluation(eme: Amendment, parsed: EvaluationJSON) -> Evaluation:
    """Rubric v2: the 4 weighted criteria plus the financial fields stored in the
    valutazioni columns (never in punteggi, so score_totale stays comparable)."""
    if parsed.financial_impact is None or parsed.allocates_resources is None:
        raise ValueError("rubric v2 response without impatto_finanziario/alloca_risorse")
    scores = [
        Score(criterion=c, score=s.score, rationale=s.rationale)
        for c, s in parsed.scores.items()
    ]
    impact = min(10.0, max(0.0, parsed.financial_impact.score))
    amount = parsed.estimated_amount_eur
    if amount is not None:
        if not math.isfinite(amount):
            amount = None
        elif amount < 0:  # the model may express a cut as negative: store the absolute value
            amount = abs(amount)
    return Evaluation(
        amendment_id=eme.id,
        scores=scores,
        total_score=composite_score(scores),
        summary=parsed.summary,
        model=MODEL,
        ts=datetime.now(timezone.utc).isoformat(),
        rubric_version=RUBRIC_VERSION,
        text_hash=eme.text_hash or testo_hash(eme.text),
        financial_impact=impact,
        allocates_resources=parsed.allocates_resources,
        estimated_amount_eur=amount,
        financial_rationale=parsed.financial_impact.rationale,
    )


async def _evaluate(eme: Amendment) -> Evaluation:
    """Plain-function body of the evaluate_amendment activity (LLM call)."""
    from mistralai.workflows.plugins.mistralai import get_mistral_client

    client = get_mistral_client()
    resp = await client.chat.complete_async(
        model=MODEL,
        messages=[{"role": "user", "content": evaluation_prompt(eme)}],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    parsed = EvaluationJSON.model_validate_json(resp.choices[0].message.content)
    return build_evaluation(eme, parsed)


async def _save(evaluations: list[Evaluation]) -> int:
    """Plain-function body of the save_evaluations activity (idempotent upsert)."""
    return await supabase.insert_evaluations(evaluations)


@workflows.activity(start_to_close_timeout=timedelta(seconds=120), retry_policy_max_attempts=2)
async def evaluate_amendment(eme: Amendment) -> Evaluation:
    return await _evaluate(eme)


@workflows.activity(start_to_close_timeout=timedelta(seconds=180), retry_policy_max_attempts=2)
async def save_evaluations(evaluations: list[Evaluation]) -> int:
    return await _save(evaluations)


class EvalOutcome(BaseModel):
    amendment_id: str
    status: Literal["evaluated", "reused", "skipped"]
    reason: str | None = None      # for skipped: "already_evaluated" | "not_relevant" | "not_current" | "no_text"


_AMENDMENT_SELECT = (
    "id,legislature:legislatura,number:numero,type:tipo,committee_seat:sede_commissione,"
    "act_uri:atto_uri,bill:ddl,text_url:url_testo,text_xml_url:url_testo_xml,date:data,text:testo,"
    "chamber:camera,natural_key:chiave_naturale,text_hash:testo_hash,relevant_spending:rilevante_spesa,"
    "corrente,valutazioni(id)"
)
_VALUTAZIONE_BY_HASH_SELECT = (
    "id,score_totale,riepilogo,modello,testo_hash,rubrica_versione,impatto_finanziario,alloca_risorse,"
    "importo_stimato_eur,motivazione_finanziaria,punteggi(criterio,punteggio,motivazione)"
)


async def _fetch_amendment_row(client: httpx.AsyncClient, amendment_id: str) -> dict | None:
    rows = await source_supabase.select_all(
        client, "emendamenti", _AMENDMENT_SELECT, {"id": f"eq.{amendment_id}"}
    )
    return rows[0] if rows else None


async def _find_evaluation_by_text_hash(client: httpx.AsyncClient, text_hash: str) -> dict | None:
    rows = await source_supabase.select_all(
        client,
        "valutazioni",
        _VALUTAZIONE_BY_HASH_SELECT,
        {"rubrica_versione": "eq.2", "testo_hash": f"eq.{text_hash}"},
        page_size=1,
    )
    return rows[0] if rows else None


async def evaluate_and_save_impl(client: httpx.AsyncClient, amendment_id: str) -> EvalOutcome:
    """Plain-function body of evaluate_and_save: gate (already evaluated / not current /
    not relevant / no text), reuse by testo_hash (valutazioni.copiata_da), else LLM."""
    row = await _fetch_amendment_row(client, amendment_id)
    if row is None:
        return EvalOutcome(amendment_id=amendment_id, status="skipped", reason="not_found")
    if row.get("valutazioni"):
        return EvalOutcome(amendment_id=amendment_id, status="skipped", reason="already_evaluated")
    if not row.get("corrente"):
        return EvalOutcome(amendment_id=amendment_id, status="skipped", reason="not_current")
    if row.get("relevant_spending") is not True:
        return EvalOutcome(amendment_id=amendment_id, status="skipped", reason="not_relevant")
    eme = Amendment(**{k: row[k] for k in Amendment.model_fields if k in row})
    if not eme.text:
        return EvalOutcome(amendment_id=amendment_id, status="skipped", reason="no_text")
    if eme.text_hash:
        src = await _find_evaluation_by_text_hash(client, eme.text_hash)
        if src is not None:
            copy = Evaluation(
                amendment_id=amendment_id,
                scores=[
                    Score(criterion=p["criterio"], score=p["punteggio"], rationale=p["motivazione"])
                    for p in src.get("punteggi") or []
                ],
                total_score=src.get("score_totale"),
                summary=src.get("riepilogo") or "",
                model=src.get("modello") or MODEL,
                ts=datetime.now(timezone.utc).isoformat(),
                rubric_version=2,
                text_hash=eme.text_hash,
                financial_impact=src.get("impatto_finanziario"),
                allocates_resources=src.get("alloca_risorse"),
                estimated_amount_eur=src.get("importo_stimato_eur"),
                financial_rationale=src.get("motivazione_finanziaria"),
                copied_from=src["id"],
            )
            inserted = await _save([copy])
            return EvalOutcome(
                amendment_id=amendment_id,
                status="reused" if inserted else "skipped",
                reason=None if inserted else "already_evaluated",
            )
    evaluation = await _evaluate(eme)
    await _save([evaluation])
    return EvalOutcome(amendment_id=amendment_id, status="evaluated")


@workflows.activity(start_to_close_timeout=timedelta(seconds=180), retry_policy_max_attempts=2)
async def evaluate_and_save(amendment_id: str) -> EvalOutcome:
    """Evaluates one stored amendment (gated + reuse) and saves the valutazione.
    Saving uses the same on_conflict/ignore-duplicates POST as save_evaluations,
    so a concurrent duplicate is a no-op."""
    base, key = source_supabase.supabase_base_and_key()
    async with httpx.AsyncClient(timeout=60.0, headers=source_supabase.supabase_headers(key)) as client:
        return await evaluate_and_save_impl(client, amendment_id)
