"""Senate source: SPARQL metadata query and pure Akoma Ntoso parser.

Moved verbatim from workflows/akn.py and workflows/daily_amendments.py so that
the sources/ package owns every source-specific client/parser and the
workflows/ package only wires them. workflows/akn.py and
daily_amendments.py re-export the names, so existing imports keep working.

The AKN parser is shared with src/akn.ts: tests/fixtures/akn/*.xml +
*.expected.json are read by both the Python and the TypeScript test suites,
so the two adapters are pinned to the same behaviour.
"""
from __future__ import annotations


from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import quote

from pydantic import BaseModel, Field

from .common import testo_hash
from . import supabase as source_supabase

if TYPE_CHECKING:
    from ..workflows.models import Amendment

SPARQL_ENDPOINT = "https://dati.senato.it/sparql"


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


def sparql_url(query: str) -> str:
    return (
        SPARQL_ENDPOINT
        + "?query="
        + quote(query)
        + "&format=application%2Fsparql-results%2Bjson"
    )


class ParsedAkn(BaseModel):
    date: str | None = None  # submission date (YYYY-MM-DD)
    bill: str | None = None  # e.g. "Ddl 391"
    text: str | None = None  # plain-text body
    signatories: list[str] = Field(default_factory=list)  # senators' names (first = proposer)
    signatory_uris: list[str] = Field(default_factory=list)  # senato.it profile URLs, aligned with signatories ("" if unknown)
    parent_number: str | None = None  # numero dell'emendamento padre (subemendamento "X/N" -> "X")


_RE_DATE = re.compile(r'FRBRdate date="(\d{4}-\d{2}-\d{2})" name="presentazione"')
_RE_BILL = re.compile(r'activeRef[^>]*showAs="(Ddl [^"]+)"')
_RE_BODY = re.compile(r"<an:amendmentBody>([\s\S]*?)</an:amendmentBody>")
_RE_DOC = re.compile(r"<(?:an:)?doc\b[^>]*>.*?</(?:an:)?doc>", re.S)
_RE_NUMERIC_ENTITY = re.compile(r"&#x[0-9a-fA-F]+;")
_RE_TAG = re.compile(r"<[^>]+>")
_RE_ACTIVE_REF = re.compile(r"<(?:an:)?(?:activeRef|original)\b[^>]*>")
_RE_AFFECTED_DOC = re.compile(r"<(?:an:)?affectedDocument\b([^>]*)>")
_RE_DOC_NUMBER = re.compile(r"<(?:an:)?docNumber\b[^>]*>([\s\S]*?)</(?:an:)?docNumber>")
_RE_ATTR_HREF_FRAGMENT = re.compile(r' href="#([^"]+)"')
_RE_EMEND_HREF = re.compile(r"/Emend/[^/]+/[^/]+/[^/]+/([^/]+)")
_RE_PROPOSTA_MODIFICA = re.compile(r"proposta di modifica\s+n\.?\s*(\S+)", re.I)
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


def extract_parent_number(xml: str) -> str | None:
    # parent of a "proposta di modifica" (subemendamento, numero "X/N"): the preface's
    # <an:affectedDocument> points at an <an:activeRef>/<an:original> whose href is the parent
    # Emend work URI (.../Emend/<ramo>/<atto>/<sede>/<numero>); fallback: <an:docNumber>
    # "Proposta di modifica n. X/N"
    refs: dict[str, str] = {}
    for m in _RE_ACTIVE_REF.finditer(xml):
        id_m = _RE_ATTR_ID.search(m.group(0))
        href_m = _RE_ATTR_HREF.search(m.group(0))
        if id_m and href_m:
            refs[id_m.group(1)] = href_m.group(1)
    for m in _RE_AFFECTED_DOC.finditer(xml):
        ref_m = _RE_ATTR_HREF_FRAGMENT.search(m.group(1))
        href = refs.get(ref_m.group(1)) if ref_m else None
        if href:
            em = _RE_EMEND_HREF.search(href)
            if em:
                return em.group(1)
    for m in _RE_DOC_NUMBER.finditer(xml):
        text = decode_entities(re.sub(r"\s+", " ", _RE_TAG.sub(" ", m.group(1))).strip())
        pm = _RE_PROPOSTA_MODIFICA.search(text)
        if pm and "/" in pm.group(1):
            return pm.group(1).rsplit("/", 1)[0]
    return None


def parent_number_from_numero(numero: str, tipo: str | None) -> str | None:
    """Numero dell'emendamento padre da "X/N"; None per gli altri. Gli o.d.g. (tipo G) hanno
    numeri con "/" nativi (G/912/1/5), quindi non sono subemendamenti."""
    if tipo == "G":
        return None
    i = numero.rfind("/")
    return numero[:i] if i > 0 else None


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


def parse(xml: str, legislature: int) -> ParsedAkn:
    names, uris = extract_signatories(xml, legislature)
    return ParsedAkn(
        date=extract_date(xml),
        bill=extract_bill(xml),
        text=extract_text(xml),
        signatories=names,
        signatory_uris=uris,
        parent_number=extract_parent_number(xml),
    )


def senato_key(
    atto_uri: str, tipo: str | None, committee_seat: bool, numero: str, legislature: int
) -> str:
    """Natural key of a Senate row, with the same normalization as the 0004 SQL backfill:
    senato:<legislatura>:<oggettotrattazione id>:<tipo>:<A|C>:<numero normalized upper>."""
    atto_id = atto_uri.rsplit("/", 1)[-1]
    num = re.sub(r"\s+", " ", numero.strip()).upper()
    return (
        f"senato:{legislature}:{atto_id}:{tipo or '?'}:"
        f"{'C' if committee_seat else 'A'}:{num}"
    )


def query_parent_ids(
    legislature: int, act_uri: str, committee_seat: bool, numbers: list[str]
) -> str:
    """SPARQL: ids of the parent amendments = emendamenti of the same atto + sede with numero in numbers."""
    values = ", ".join(f'"{n.replace(chr(34), "")}"' for n in numbers)
    return (
        "PREFIX osr: <http://dati.senato.it/osr/>\n"
        "SELECT ?eme ?number ?flag WHERE {\n"
        "  ?eme a osr:Emendamento ;\n"
        f"       osr:legislatura {legislature} ;\n"
        f"       osr:oggetto <{act_uri}> ;\n"
        "       osr:numero ?number ;\n"
        "       osr:flagCommissione ?flag .\n"
        f'  FILTER(STR(?flag) = "{1 if committee_seat else 0}")\n'
        f"  FILTER(?number IN ({values}))\n"
        "}"
    )


async def resolve_parents_sparql(amendments: list["Amendment"]) -> int:
    """Sets emendamento_padre_id on subemendamenti (numero_padre set, padre not yet resolved) via
    dati.senato.it SPARQL, one query per (legislatura, atto, sede) group. Returns how many were resolved.
    Uses its own httpx client (curl-like UA, required by CloudFront): never the Supabase client,
    so no Supabase credential leaves the Supabase host."""
    import httpx

    groups: dict[tuple[int, str, bool], list["Amendment"]] = {}
    for a in amendments:
        if a.numero_padre and not a.emendamento_padre_id and a.act_uri:
            groups.setdefault((a.legislature, a.act_uri, a.committee_seat), []).append(a)
    resolved = 0
    async with httpx.AsyncClient(
        timeout=60.0,
        headers={"Accept": "application/sparql-results+json", "User-Agent": "curl/8.5.0"},
    ) as client:
        for (legislature, act_uri, committee_seat), items in groups.items():
            numbers = sorted({a.numero_padre for a in items if a.numero_padre})
            resp = await client.get(
                sparql_url(query_parent_ids(legislature, act_uri, committee_seat, numbers))
            )
            resp.raise_for_status()
            by_number = {
                b["number"]["value"]: b["eme"]["value"]
                for b in resp.json()["results"]["bindings"]
            }
            for a in items:
                pid = by_number.get(a.numero_padre or "")
                if pid:
                    a.emendamento_padre_id = pid
                    resolved += 1
    return resolved


def senato_row(a: "Amendment") -> dict:
    """supabase_row(a) plus chiave_naturale, ordine_versione (numeric URI tail), camera, testo_hash."""
    from ..workflows.supabase import amendment_row

    row = amendment_row(a)
    row["chiave_naturale"] = senato_key(
        a.act_uri or "", a.type, a.committee_seat, a.number, a.legislature
    )
    row["ordine_versione"] = int(a.id.rsplit("/", 1)[-1])
    row["camera"] = "senato"
    row["testo_hash"] = testo_hash(a.text)
    return row


async def save_senato_amendments(client: httpx.AsyncClient, amendments: list["Amendment"]) -> int:
    """Upsert Senate amendments (on_conflict=id) in chunks; after each chunk call
    ricalcola_versioni for the chunk's natural keys so versione/corrente/superato_da
    are computed by the DB function, never by Python."""
    await resolve_parents_sparql(amendments)
    rows = [senato_row(a) for a in amendments]
    total = 0
    for i in range(0, len(rows), source_supabase.SAVE_CHUNK):
        chunk = rows[i : i + source_supabase.SAVE_CHUNK]
        await source_supabase.upsert(client, "emendamenti", chunk, "id")
        await source_supabase.rpc(
            client,
            "ricalcola_versioni",
            {"p_chiavi": sorted({r["chiave_naturale"] for r in chunk})},
        )
        total += len(chunk)
    return total
