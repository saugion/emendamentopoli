"""Pure Akoma Ntoso parser: XML text -> ParsedAkn (no fetching, no IO).

Shared contract with src/akn.ts: tests/fixtures/akn/*.xml + *.expected.json are
read by both the Python and the TypeScript test suites, so the two adapters are
pinned to the same behaviour.
"""

import re

from pydantic import BaseModel, Field


class ParsedAkn(BaseModel):
    date: str | None = None  # submission date (YYYY-MM-DD)
    bill: str | None = None  # e.g. "Ddl 391"
    text: str | None = None  # plain-text body
    signatories: list[str] = Field(default_factory=list)  # senators' names (first = proposer)
    signatory_uris: list[str] = Field(default_factory=list)  # senato.it profile URLs, aligned with signatories ("" if unknown)


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


def parse(xml: str, legislature: int) -> ParsedAkn:
    names, uris = extract_signatories(xml, legislature)
    return ParsedAkn(
        date=extract_date(xml),
        bill=extract_bill(xml),
        text=extract_text(xml),
        signatories=names,
        signatory_uris=uris,
    )
