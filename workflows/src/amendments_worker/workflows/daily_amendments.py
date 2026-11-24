"""Nightly workflow: downloads the amendments of the previous day (or of a period) and saves them to Supabase.

Source: SPARQL endpoint of dati.senato.it (Open Data, CC BY 3.0 license,
attribution "Senato della Repubblica - dati.senato.it"). The osr:Emendamento
class does not expose the submission date, so the most recent amendments are
taken (decreasing numeric id, which follows the submission order) and the date
is read from each one's Akoma Ntoso text. With end_date an entire interval is
downloaded (e.g. a one-month backfill): the SPARQL scan proceeds in pages of
100 with an early stop when a read date is earlier than start date. The
pagination/policy loop lives in ingest.py; this file only wires the adapters.
"""

import asyncio
from datetime import timedelta

from pydantic import BaseModel, Field, model_validator

import mistralai.workflows as workflows

from ..sources.senato import (  # re-exported: tests import query_recent_metadata from this module
    SPARQL_ENDPOINT,
    query_recent_metadata,
    sparql_url,
)
from ..sources.supabase import select_all, supabase_base_and_key, supabase_headers
from . import akn
from . import supabase
from .ingest import IngestDeps, ingest_period
from .models import Amendment
from .evaluation import save_evaluations, evaluate_amendment

UA = "curl/8.5.0"  # CloudFront only accepts curl-like user-agents on these paths
TIMEZONE = "Europe/Rome"


class DailyAmendmentsInput(BaseModel):
    date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Start of the period to download (YYYY-MM-DD); default: yesterday, Europe/Rome timezone",
    )
    end_date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="End of the period (YYYY-MM-DD, inclusive); default: same day as date",
    )
    legislature: int = 19
    limit: int = Field(
        default=1000,
        ge=1,
        le=20000,
        description="Maximum number of recent amendments to consider",
    )

    @model_validator(mode="after")
    def _consistent_period(self) -> "DailyAmendmentsInput":
        if self.date and self.end_date and self.end_date < self.date:
            raise ValueError("end_date must be greater than or equal to date")
        return self


class DailyAmendmentsOutput(BaseModel):
    date: str
    end_date: str
    legislature: int
    metadata_fetched: int
    analyzed: int
    found: int
    saved: int
    evaluated: int
    ids: list[str]
    errors: list[str]
    evaluation_errors: list[str]


@workflows.activity(start_to_close_timeout=timedelta(seconds=30), retry_policy_max_attempts=1)
async def yesterday() -> str:
    from datetime import datetime, timedelta as delta
    from zoneinfo import ZoneInfo

    return (datetime.now(ZoneInfo(TIMEZONE)) - delta(days=1)).date().isoformat()


@workflows.activity(start_to_close_timeout=timedelta(seconds=300), retry_policy_max_attempts=3)
async def saved_amendments_map(legislature: int) -> dict[str, dict[str, str | None]]:
    # keyset-paginated read: PostgREST caps responses at max-rows=1000, a single
    # limit=100000 request silently truncates and re-saved amendments would be re-evaluated
    import httpx

    base, key = supabase_base_and_key()
    async with httpx.AsyncClient(timeout=45.0, headers=supabase_headers(key)) as client:
        rows = await select_all(
            client, "emendamenti", "id,data,firmatari", {"legislatura": f"eq.{legislature}"}
        )
    return {r["id"]: {"data": r["data"], "firmatari": r["firmatari"]} for r in rows}


@workflows.activity(start_to_close_timeout=timedelta(seconds=300), retry_policy_max_attempts=3)
async def fetch_metadata_page(legislature: int, page_limit: int, offset: int) -> list[Amendment]:
    import httpx

    url = sparql_url(query_recent_metadata(legislature, page_limit, offset))
    async with httpx.AsyncClient(
        timeout=90.0,
        headers={"Accept": "application/sparql-results+json", "User-Agent": UA},
    ) as client:
        for attempt in range(4):
            if attempt:
                await asyncio.sleep(20 * attempt)
            resp = await client.get(url)
            if resp.status_code in (403, 429, 500, 502, 503) and attempt < 3:
                continue
            if not resp.is_success:
                raise RuntimeError(f"SPARQL {resp.status_code}: {resp.text[:500]}")
            break
        bindings = resp.json()["results"]["bindings"]
    amendments = [
        Amendment(
            id=b["eme"]["value"],
            legislature=legislature,
            number=b["number"]["value"],
            type=b["type"]["value"],
            committee_seat=b["committeeFlag"]["value"] == "1",
            act_uri=b["act"]["value"],
            text_url=b["textUrl"]["value"],
            text_xml_url=b["textXmlUrl"]["value"],
        )
        for b in bindings
    ]
    amendments.sort(key=lambda a: int(a.id.rsplit("/", 1)[-1]), reverse=True)
    return amendments


@workflows.activity(start_to_close_timeout=timedelta(seconds=60), retry_policy_max_attempts=3)
async def enrich_amendment(eme: Amendment) -> Amendment:
    import httpx

    url = eme.text_xml_url
    if url.startswith("http:"):
        url = "https:" + url[len("http:") :]
    await asyncio.sleep(0.3)
    async with httpx.AsyncClient(timeout=45.0, headers={"User-Agent": UA}) as client:
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(5 * attempt)
            resp = await client.get(url)
            if resp.status_code in (403, 429) and attempt < 2:
                continue
            resp.raise_for_status()
            break
        # the XML is iso-8859-1: decode bytes as latin1
        xml = resp.content.decode("windows-1252")
    parsed = akn.parse(xml, eme.legislature)
    eme.date = parsed.date
    eme.bill = parsed.bill
    eme.text = parsed.text
    eme.signatories = parsed.signatories
    eme.signatory_uris = parsed.signatory_uris
    return eme


@workflows.activity(start_to_close_timeout=timedelta(seconds=180), retry_policy_max_attempts=2)
async def save_amendments(amendments: list[Amendment]) -> int:
    import httpx

    from ..sources.senato import save_senato_amendments

    base, key = supabase_base_and_key()
    async with httpx.AsyncClient(timeout=150.0, headers=supabase_headers(key)) as client:
        return await save_senato_amendments(client, amendments)


@workflows.workflow.define(
    name="emendamenti-giornalieri",
    workflow_display_name="Daily amendments (Senato)",
    workflow_description=(
        "Downloads from dati.senato.it (SPARQL, CC BY 3.0) the amendments submitted in the given "
        "period (date..end_date, default: only yesterday, Europe/Rome timezone; useful for "
        "multi-day backfills), fetches the Akoma Ntoso texts, saves the amendments to Supabase "
        "(upsert by id) and evaluates them with Mistral (evaluations + scores)."
    ),
    execution_timeout=timedelta(hours=2),
)
class DailyAmendmentsWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: DailyAmendmentsInput) -> DailyAmendmentsOutput:
        start_date = input.date if input.date else await yesterday()
        end_date = input.end_date if input.end_date else start_date
        deps = IngestDeps(
            fetch_page=fetch_metadata_page,
            enrich=enrich_amendment,
            saved_map=saved_amendments_map,
            save=save_amendments,
            evaluate=evaluate_amendment,
            save_evaluations=save_evaluations,
        )
        report = await ingest_period(start_date, end_date, input.legislature, input.limit, deps)
        return DailyAmendmentsOutput(
            date=start_date,
            end_date=end_date,
            legislature=input.legislature,
            metadata_fetched=report.metadata_fetched,
            analyzed=report.analyzed,
            found=report.found,
            saved=report.saved,
            evaluated=report.evaluated,
            ids=report.ids,
            errors=report.errors,
            evaluation_errors=report.evaluation_errors,
        )
