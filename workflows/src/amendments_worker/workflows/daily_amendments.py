"""Nightly workflow: downloads the amendments of the previous day (or of a period) and saves them to Supabase.

Source: SPARQL endpoint of dati.senato.it (Open Data, CC BY 3.0 license,
attribution "Senato della Repubblica - dati.senato.it"). The osr:Emendamento
class does not expose the submission date, so the most recent amendments are
taken (decreasing numeric id, which follows the submission order) and the date
is read from each one's Akoma Ntoso text. With end_date an entire interval is
downloaded (e.g. a one-month backfill): the SPARQL scan proceeds in pages of
SPARQL_PAGE with an early stop when a read date is earlier than start date.
"""

import asyncio
import re
from datetime import timedelta
from urllib.parse import quote

from pydantic import BaseModel, Field, model_validator

import mistralai.workflows as workflows

from .models import SAVE_CHUNK, Amendment
from .evaluation import Evaluation, save_evaluations, evaluate_amendment

SPARQL_ENDPOINT = "https://dati.senato.it/sparql"
UA = "curl/8.5.0"  # CloudFront only accepts curl-like user-agents on these paths
TIMEZONE = "Europe/Rome"
ENRICH_CONCURRENCY = 8
EVALUATION_CONCURRENCY = 10
SPARQL_PAGE = 100


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


def query_recent_metadata(legislature: int, limit: int, offset: int = 0) -> str:
    return (
        "PREFIX osr: <http://dati.senato.it/osr/>\n"
        "SELECT ?eme ?number ?type ?committeeFlag ?act ?textUrl ?textXmlUrl WHERE {\n"
        "  ?eme a osr:Emendamento ;\n"
        f"       osr:legislatura {legislature} ;\n"
        "       osr:numero ?number ;\n"
        "       osr:tipo ?type ;\n"
        "       osr:flagCommissione ?committeeFlag ;\n"
        "       osr:oggetto ?act ;\n"
        "       osr:URLTesto ?textUrl ;\n"
        "       osr:URLTestoXml ?textXmlUrl .\n"
        "}\n"
        # 7-digit ids in legislature XIX: lexicographic DESC order is also numeric DESC
        "ORDER BY DESC(?eme)\n"
        f"LIMIT {limit} OFFSET {offset}"
    )


_RE_DATE = re.compile(r'FRBRdate date="(\d{4}-\d{2}-\d{2})" name="presentazione"')
_RE_BILL = re.compile(r'activeRef[^>]*showAs="(Ddl [^"]+)"')
_RE_BODY = re.compile(r"<an:amendmentBody>([\s\S]*?)</an:amendmentBody>")
_RE_DOC = re.compile(r"<(?:an:)?doc\b[^>]*>.*?</(?:an:)?doc>", re.S)
_RE_NUMERIC_ENTITY = re.compile(r"&#x[0-9a-fA-F]+;")
_RE_TAG = re.compile(r"<[^>]+>")
_RE_TLC_PERSON = re.compile(r"<(?:an:)?TLCPerson\b[^>]*>")
_RE_DOC_PROPONENT = re.compile(r"<(?:an:)?docProponent\b([^>]*)>([\s\S]*?)</(?:an:)?docProponent>")
_RE_ATTR_ID = re.compile(r' id="([^"]+)"')
_RE_ATTR_HREF = re.compile(r' href="([^"]+)"')
_RE_REFERS_TO = re.compile(r' refersTo="#([^"]+)"')
_RE_SENATO_PERSON = re.compile(r"^https?://dati\.senato\.it/(?:senatore/)?(\d+)/?$")
_NAMED_ENTITIES = {
    "&#039;": "'",
    "&apos;": "'",
    "&quot;": '"',
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
}


def extract_date(xml: str) -> str | None:
    m = _RE_DATE.search(xml)
    return m.group(1) if m else None


def extract_bill(xml: str) -> str | None:
    m = _RE_BILL.search(xml)
    return m.group(1) if m else None


def extract_text(xml: str) -> str | None:
    m = _RE_BODY.search(xml)
    if not m:
        return None
    s = _RE_DOC.sub("", m.group(1))
    s = _RE_NUMERIC_ENTITY.sub(" ", s)
    s = _RE_TAG.sub(" ", s)
    s = s.replace("&nbsp;", " ")
    s = re.sub(r"\s+", " ", s).strip()
    for entity, value in _NAMED_ENTITIES.items():
        s = s.replace(entity, value)
    return s


def decode_entities(s: str) -> str:
    for entity, value in _NAMED_ENTITIES.items():
        s = s.replace(entity, value)
    return s


def senator_profile(href: str, legislature: int) -> str:
    # dati.senato.it href (e.g. .../senatore/407 or .../29521) -> profile page on senato.it
    # ("" if not a person, e.g. senatore/0 = "Il Relatore")
    m = _RE_SENATO_PERSON.match(href)
    if not m:
        return re.sub(r"^http:", "https:", href)
    if set(m.group(1)) == {"0"}:
        return ""
    return f"https://www.senato.it/leg/{legislature}/BGT/Schede/Attsen/{m.group(1).zfill(8)}.htm"


def extract_signatories(xml: str, legislature: int) -> tuple[list[str], list[str]]:
    # signatories: each <an:docProponent> in the preface references (refersTo) an
    # <an:TLCPerson> in <an:references> with an href to dati.senato.it, from which
    # the senato.it profile page URL is derived (first proponent = proposer)
    persons: dict[str, str] = {}
    for m in _RE_TLC_PERSON.finditer(xml):
        id_m = _RE_ATTR_ID.search(m.group(0))
        href_m = _RE_ATTR_HREF.search(m.group(0))
        if id_m and href_m:
            persons[id_m.group(1)] = href_m.group(1)
    names: list[str] = []
    uris: list[str] = []
    for m in _RE_DOC_PROPONENT.finditer(xml):
        name = decode_entities(re.sub(r"\s+", " ", _RE_TAG.sub(" ", m.group(2))).strip())
        if not name or name in names:
            continue
        ref_m = _RE_REFERS_TO.search(m.group(1))
        href = persons.get(ref_m.group(1)) if ref_m else None
        names.append(name)
        uris.append(senator_profile(href, legislature) if href else "")
    return names, uris


def supabase_row(a: Amendment) -> dict:
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


@workflows.activity(start_to_close_timeout=timedelta(seconds=30), retry_policy_max_attempts=1)
async def yesterday() -> str:
    from datetime import datetime, timedelta as delta
    from zoneinfo import ZoneInfo

    return (datetime.now(ZoneInfo(TIMEZONE)) - delta(days=1)).date().isoformat()


@workflows.activity(start_to_close_timeout=timedelta(seconds=60), retry_policy_max_attempts=3)
async def saved_amendments_map(legislature: int) -> dict[str, dict[str, str | None]]:
    import os

    import httpx

    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_KEY"]
    url = f"{base}/rest/v1/emendamenti?select=id,data,firmatari&legislatura=eq.{legislature}&limit=100000"
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.get(url, headers={"apikey": key, "Authorization": f"Bearer {key}"})
        resp.raise_for_status()
        return {r["id"]: {"data": r["data"], "firmatari": r["firmatari"]} for r in resp.json()}


@workflows.activity(start_to_close_timeout=timedelta(seconds=120), retry_policy_max_attempts=3)
async def fetch_metadata_page(legislature: int, page_limit: int, offset: int) -> list[Amendment]:
    import httpx

    url = (
        SPARQL_ENDPOINT
        + "?query="
        + quote(query_recent_metadata(legislature, page_limit, offset))
        + "&format=application%2Fsparql-results%2Bjson"
    )
    async with httpx.AsyncClient(
        timeout=90.0,
        headers={"Accept": "application/sparql-results+json", "User-Agent": UA},
    ) as client:
        for attempt in range(4):
            if attempt:
                await asyncio.sleep(10 * attempt)
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
    await asyncio.sleep(0.2)
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
    eme.date = extract_date(xml)
    eme.bill = extract_bill(xml)
    eme.text = extract_text(xml)
    eme.signatories, eme.signatory_uris = extract_signatories(xml, eme.legislature)
    return eme


@workflows.activity(start_to_close_timeout=timedelta(seconds=180), retry_policy_max_attempts=2)
async def save_amendments(amendments: list[Amendment]) -> int:
    import os

    import httpx

    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_KEY"]
    rows = [supabase_row(a) for a in amendments]
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    total = 0
    async with httpx.AsyncClient(timeout=150.0) as client:
        for i in range(0, len(rows), SAVE_CHUNK):
            chunk = rows[i : i + SAVE_CHUNK]
            resp = await client.post(
                f"{base}/rest/v1/emendamenti?on_conflict=id",
                headers=headers,
                json=chunk,
            )
            resp.raise_for_status()
            total += len(chunk)
    return total


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
        enrich_sem = asyncio.Semaphore(ENRICH_CONCURRENCY)

        async def _enrich(m: Amendment) -> Amendment:
            async with enrich_sem:
                return await enrich_amendment(m)

        already_saved = await saved_amendments_map(input.legislature)
        metadata: list[Amendment] = []
        analyzed: list[Amendment] = []
        errors: list[str] = []
        offset = 0
        while len(metadata) < input.limit:
            page = await fetch_metadata_page(
                input.legislature, min(SPARQL_PAGE, input.limit - len(metadata)), offset
            )
            if not page:
                break
            metadata.extend(page)
            offset += len(page)
            # re-enrich amendments already saved but still without signatories (backfill)
            to_enrich = [
                m
                for m in page
                if m.id not in already_saved or not already_saved[m.id]["firmatari"]
            ]
            results = await asyncio.gather(
                *[_enrich(m) for m in to_enrich],
                return_exceptions=True,
            )
            errors.extend(
                f"{m.id}: {type(r).__name__}: {r}"
                for m, r in zip(to_enrich, results)
                if isinstance(r, BaseException)
            )
            page_ok = [r for r in results if not isinstance(r, BaseException)]
            analyzed.extend(page_ok)
            dates = [a.date for a in page_ok if a.date]
            dates += [
                already_saved[m.id]["data"]
                for m in page
                if m.id in already_saved and already_saved[m.id]["data"]
            ]
            if dates and min(dates) < start_date:
                break
        in_period = [a for a in analyzed if a.date and start_date <= a.date <= end_date]
        saved = await save_amendments(in_period) if in_period else 0
        # evaluate only newly seen amendments: already-saved ones have a valutazione already
        to_evaluate = [a for a in in_period if a.id not in already_saved]
        eval_sem = asyncio.Semaphore(EVALUATION_CONCURRENCY)

        async def _evaluate(a: Amendment) -> Evaluation:
            async with eval_sem:
                return await evaluate_amendment(a)

        eval_results = await asyncio.gather(
            *[_evaluate(a) for a in to_evaluate],
            return_exceptions=True,
        )
        evaluation_errors = [
            f"{a.id}: {type(r).__name__}: {r}"
            for a, r in zip(to_evaluate, eval_results)
            if isinstance(r, BaseException)
        ]
        evaluations = [r for r in eval_results if not isinstance(r, BaseException)]
        evaluated = await save_evaluations(evaluations) if evaluations else 0
        return DailyAmendmentsOutput(
            date=start_date,
            end_date=end_date,
            legislature=input.legislature,
            metadata_fetched=len(metadata),
            analyzed=len(analyzed),
            found=len(in_period),
            saved=saved,
            evaluated=evaluated,
            ids=[a.id for a in in_period],
            errors=errors,
            evaluation_errors=evaluation_errors,
        )
