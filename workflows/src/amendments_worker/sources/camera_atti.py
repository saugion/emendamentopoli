"""Camera bills (ocd:atto) source: SPARQL metadata from dati.camera.it -> atti table rows.

Titles are cleaned (entities unescaped up to 3 times, tags dropped, trailing "(3174)"
removed) and flagged: is_dl_conversione (decreto-legge conversions), is_bilancio
(Government budget laws), is_commissione_bilancio_referente (V Commissione, organo o19_3505).
"""
from __future__ import annotations


from __future__ import annotations

import html
import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from pydantic import BaseModel

from .common import (
    SPARQL_CAMERA_ENDPOINT,
    camera_sparql_timeout,
    polite_get,
    yyyymmdd_to_iso,
)

logger = logging.getLogger(__name__)

CAMERA_ATTI_PAGE = 1000
ORGANO_COMMISSIONE_BILANCIO = "http://dati.camera.it/ocd/organo.rdf/o19_3505"

RE_DL = re.compile(r"\bconversione in legge(?:,? con modificazioni,?)? del decreto-legge\b", re.I)
RE_BIL = re.compile(
    r'^(?:S\. \d+\. - )?"?(?:bilancio di previsione dello stato per l\'anno finanziario'
    r"|rendiconto generale dell'amministrazione dello stato"
    r"|disposizioni per l'assestamento del bilancio dello stato)",
    re.I,
)


class CameraAttoItem(BaseModel):
    uri: str
    numero: str
    titolo_raw: str
    data: str
    modified: str
    iniziativa: str | None
    natura: str | None
    organi_referenti: list[str]


def query_camera_atti(legislature: int, since: str) -> str:
    return (
        "PREFIX ocd: <http://dati.camera.it/ocd/>\n"
        "PREFIX dc: <http://purl.org/dc/elements/1.1/>\n"
        "PREFIX ods: <http://lod.xdams.org/ontologies/ods/>\n"
        "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
        "SELECT DISTINCT ?atto ?numero ?titolo ?data ?modified\n"
        "  (SAMPLE(?iniziativa) AS ?iniziativa1)\n"
        "  (SAMPLE(?natura) AS ?natura1)\n"
        '  (GROUP_CONCAT(DISTINCT STR(?organo); separator=" ") AS ?organi_referenti)\n'
        "WHERE {\n"
        "  ?atto a ocd:atto ;\n"
        f"        ocd:rif_leg <http://dati.camera.it/ocd/legislatura.rdf/repubblica_{legislature}> ;\n"
        "        dc:identifier ?numero ;\n"
        "        dc:title ?titolo ;\n"
        "        dc:date ?data ;\n"
        "        ods:modified ?modified .\n"
        f'  FILTER(?modified >= "{since}"^^xsd:dateTime)\n'
        "  OPTIONAL { ?atto ocd:iniziativa ?iniziativa }\n"
        "  OPTIONAL { ?atto ocd:rif_natura ?natura }\n"
        '  OPTIONAL { ?atto ocd:rif_assegnazione ?ass . ?ass ocd:sede "Referente" ; ocd:rif_organo ?organo }\n'
        "}\n"
        "GROUP BY ?atto ?numero ?titolo ?data ?modified\n"
        "ORDER BY ?modified ?atto\n"
        f"LIMIT {CAMERA_ATTI_PAGE}"
    )


def parse_atti_bindings(bindings: list[dict]) -> list[CameraAttoItem]:
    items: list[CameraAttoItem] = []
    seen: set[str] = set()
    for b in bindings:
        uri = b["atto"]["value"]
        if uri in seen:
            continue
        seen.add(uri)
        items.append(
            CameraAttoItem(
                uri=uri,
                numero=b["numero"]["value"],
                titolo_raw=b["titolo"]["value"],
                data=b["data"]["value"],
                modified=b["modified"]["value"],
                iniziativa=b["iniziativa1"]["value"] if "iniziativa1" in b else None,
                natura=b["natura1"]["value"] if "natura1" in b else None,
                organi_referenti=(
                    b["organi_referenti"]["value"].split() if b.get("organi_referenti") else []
                ),
            )
        )
    return items


async def iterate_atti(
    client: httpx.AsyncClient, legislature: int, since: str
) -> AsyncIterator[list[CameraAttoItem]]:
    """Inclusive keyset on ods:modified + dedupe by URI (same pattern as the ODG loop)."""
    cursor = since
    seen: set[str] = set()
    while True:
        q = query_camera_atti(legislature, cursor)
        resp = await polite_get(
            client,
            SPARQL_CAMERA_ENDPOINT,
            params={"query": q, "format": "application/sparql-results+json"},
            headers={"Accept": "application/sparql-results+json"},
            timeout=camera_sparql_timeout(),
        )
        page = parse_atti_bindings(resp.json()["results"]["bindings"])
        new = [i for i in page if i.uri not in seen]
        seen |= {i.uri for i in new}
        yield new
        if len(page) < CAMERA_ATTI_PAGE:
            return
        if not new:
            raise RuntimeError("keyset stalled: >= 1000 atti share ods:modified " + cursor)
        cursor = page[-1].modified


def clean_title(raw: str) -> str:
    s = raw
    for _ in range(3):                      # titles can be escaped twice
        u = html.unescape(s)
        if u == s:
            break
        s = u
    s = s.replace('\\"', '"')
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*\(\d+(?:-[a-z]+)?(?:-[A-Z]+)?\)\s*$", "", s)   # trailing "(3174)" / "(643-bis)"
    return s


def atti_flags(titolo: str, iniziativa: str | None, organi_referenti: list[str]) -> tuple[bool, bool, bool]:
    is_dl_conversione = bool(RE_DL.search(titolo))
    is_bilancio = bool(RE_BIL.search(titolo)) and iniziativa == "Governo"
    is_commissione_bilancio_referente = ORGANO_COMMISSIONE_BILANCIO in organi_referenti
    return is_dl_conversione, is_bilancio, is_commissione_bilancio_referente


def atti_row(item: CameraAttoItem, legislature: int = 19) -> dict:
    titolo = clean_title(item.titolo_raw)
    is_dl, is_bil, is_comm_bil = atti_flags(titolo, item.iniziativa, item.organi_referenti)
    natura = item.natura.rsplit("/", 1)[-1] if item.natura else None
    return {
        "uri": item.uri,
        "legislatura": legislature,
        "numero": item.numero,
        "titolo": titolo,
        "titolo_raw": item.titolo_raw,
        "data_presentazione": yyyymmdd_to_iso(item.data),
        "iniziativa": item.iniziativa,
        "natura": natura,
        "organi_referenti": sorted(set(item.organi_referenti)),
        "is_dl_conversione": is_dl,
        "is_bilancio": is_bil,
        "is_commissione_bilancio_referente": is_comm_bil,
        "url_scheda": f"https://www.camera.it/leg{legislature}/126?tab=1&leg={legislature}&idDocumento={item.numero}",
        "fonte_modificata": item.modified,
        "aggiornato_at": datetime.now(timezone.utc).isoformat(),
    }
