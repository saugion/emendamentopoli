"""Backfill: evaluates the amendments already in Supabase that do not have an evaluation yet."""

import asyncio
import os
from datetime import timedelta

from pydantic import BaseModel, Field

import mistralai.workflows as workflows

from .models import Amendment
from .evaluation import save_evaluations, evaluate_amendment


class EvaluateMissingInput(BaseModel):
    limit: int = Field(default=500, ge=1, le=5000, description="Maximum number of amendments without an evaluation to process")


class EvaluateMissingOutput(BaseModel):
    found: int
    evaluated: int
    errors: list[str]
    ids: list[str]


@workflows.activity(start_to_close_timeout=timedelta(seconds=120), retry_policy_max_attempts=3)
async def amendments_without_evaluation(limit: int) -> list[Amendment]:
    import httpx

    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_KEY"]
    url = (
        f"{base}/rest/v1/emendamenti"
        "?select=id,legislature:legislatura,number:numero,type:tipo,committee_seat:sede_commissione,"
        "act_uri:atto_uri,bill:ddl,text_url:url_testo,text_xml_url:url_testo_xml,date:data,text:testo"
        ",valutazioni!left(id)"
        f"&valutazioni.id=is.null&order=created_at.desc&limit={limit}"
    )
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.get(
            url, headers={"apikey": key, "Authorization": f"Bearer {key}"}
        )
        resp.raise_for_status()
        rows = resp.json()
    return [Amendment(**{k: r[k] for k in Amendment.model_fields if k in r}) for r in rows]


@workflows.workflow.define(
    name="valuta-emendamenti-mancanti",
    workflow_display_name="Evaluate missing amendments (backfill)",
    workflow_description=(
        "Reads from Supabase the amendments without an evaluation (anti-join on valutazioni), "
        "evaluates them with Mistral according to the public-interest rubric and writes "
        "valutazioni + punteggi."
    ),
    execution_timeout=timedelta(hours=2),
)
class EvaluateMissingWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: EvaluateMissingInput) -> EvaluateMissingOutput:
        missing = await amendments_without_evaluation(input.limit)
        results = await asyncio.gather(
            *[evaluate_amendment(a) for a in missing],
            return_exceptions=True,
        )
        errors = [
            f"{m.id}: {type(r).__name__}: {r}"
            for m, r in zip(missing, results)
            if isinstance(r, BaseException)
        ]
        evaluations = [r for r in results if not isinstance(r, BaseException)]
        evaluated = await save_evaluations(evaluations) if evaluations else 0
        return EvaluateMissingOutput(
            found=len(missing),
            evaluated=evaluated,
            errors=errors,
            ids=[v.amendment_id for v in evaluations],
        )
