"""Camera ODG source: SPARQL metadata from dati.camera.it + sicross text pages from documenti.camera.it.

SPARQL gives only metadata; the text (with every version, the signatories, the Governo's
parere and the esito) is read per item from the sicross page at dc:relation (stile=6).
Pagination is an inclusive keyset on ods:modified (OFFSET is forbidden: Virtuoso SR353
past 10,000 sorted rows).
"""

import hashlib
import html
import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel

from .common import (
    CAMERA_SPARQL_TIMEOUT,
    SPARQL_CAMERA_ENDPOINT,
    html_to_text,
    polite_get,
    testo_hash,
    yyyymmdd_to_iso,
)

logger = logging.getLogger(__name__)

CAMERA_ODG_TYPES = (
    "ODG - ORDINE DEL GIORNO IN ASSEMBLEA",
    "ODG - ORDINE DEL GIORNO IN COMMISSIONE",
    "ORDINE DEL GIORNO IN ASSEMBLEA",
    "ORDINE DEL GIORNO IN COMMISSIONE",
)
CAMERA_RAMO = "Camera dei Deputati"
CAMERA_SPARQL_PAGE = 1000

RE_ODG_PDL = re.compile(
    r"^(?P<sede>[90])/(?P<num>\d{5})(?P<suf>(?:-(?:bis|ter|quater|quinquies))?)"
    r"(?:-(?P<stage>[A-Z]+(?:-[A-Z]+)?))?/(?P<prog>\d{3})(?:/(?P<comm>\d{2}))?$"
)
RE_ODG_DOC = re.compile(r"^9/DOC\.(?P<doc>[^,]+),N\.(?P<n>[^/]+)/(?P<prog>\d{3})$")
RE_DEPUTATO_ID = re.compile(r"/d(\d+)_\d+$")

RE_NOT_FOUND = re.compile(r"FILE NON TROVATO")
RE_PRESENTATORI = re.compile(r'<div class="presentatori">(.*?)</blockquote>\s*</div>', re.S)
RE_PERSON_LINK = re.compile(
    r'href="[^"]*schedaDeputato&(?:amp;)?idPersona=(\d+)&(?:amp;)?idLegislatura=(\d+)\"><span class="nominativo">([^<]+)</span>'
)
RE_FASI_BLOCK = re.compile(r"<h6>Fasi iter: </h6>\s*<div>\s*<blockquote>(.*?)</blockquote>", re.S)
RE_FASE = re.compile(r"<p>([A-Z][A-Z' ]+?) IL (\d{2})/(\d{2})/(\d{4})</p>")
RE_STATO_ITER = re.compile(r'<h6 style="display:inline;">Stato iter: </h6>\s*(?:<strong>)?([^<]+?)(?:</strong>)?\s*<br', re.S)
RE_TESTO_BLOCK = re.compile(
    r'<div class="testoAtto(?: history)?" id="tab(\d+)">(.*?)(?=<div class="testoAtto|<h6>Classificazione|</body>)', re.S
)
RE_OPTION = re.compile(r'<option value="(\d+)"[^>]*>([^<]*)</option>')
RE_CENTRA = re.compile(r'<div[^>]*class="centra">.*?</div>\s*</div>', re.S)
RE_COMMISSIONE = re.compile(
    r'<span class="label">Commissione: </span><a href="[^"]*shadow_organo_parlamentare=(\d+)">([^<]+)</a>'
)
RE_SEDUTA = re.compile(r'<span class="seduta">(\d+)</span>')
RE_TAG = re.compile(r"<[^>]+>")
RE_STRONG_NUM = re.compile(r"<strong>\s*\d+\.?\s*</strong>")

KNOWN_FASI = {
    "ATTO MODIFICATO IN CORSO DI SEDUTA",
    "ACCOLTO",
    "NON ACCOLTO",
    "ACCOLTO COME RACCOMANDAZIONE",
    "PROPOSTA RIFORMULAZIONE",
    "PARERE GOVERNO",
    "PARERE COLLEGIO QUESTORI",
    "DISCUSSIONE",
    "RINUNCIA ALLA VOTAZIONE",
    "RESPINTO",
    "APPROVATO",
    "CONCLUSO",
    "RITIRATO",
    "INAMMISSIBILE",
    "DECADUTO",
    "ASSORBITO",
    "PRECLUSO",
}


class CameraOdgItem(BaseModel):
    aic: str
    identifier: str
    tipo: str
    titolo: str
    data: str  # raw dc:date (YYYYMMDD or YYYYMMDD-YYYYMMDD)
    concluso: int
    modified: str
    relation: str
    primo: str
    seduta: str | None
    altri: list[str]
    subjects: list[str]


class SicrossVersion(BaseModel):
    versione: int
    etichetta: str | None
    testo: str


class SicrossPerson(BaseModel):
    id_persona: str
    legislatura: int
    nome: str


class SicrossPage(BaseModel):
    persons: list[SicrossPerson]
    fasi: list[dict[str, str]]          # [{"fase": "PARERE GOVERNO", "data": "2026-10-21"}]
    stato_iter: str | None
    versions: list[SicrossVersion]      # sorted by versione ascending; last = current
    commissione: tuple[str, str] | None # (organo id, label) for ODG in Commissione
    seduta: str | None


def query_camera_odg(
    legislature: int, since: str, date_from: str | None = None, date_to: str | None = None
) -> str:
    """since: ISO UTC 'YYYY-MM-DDTHH:MM:SSZ'. date_from/date_to: 'YYYYMMDD' (both or neither)."""
    tipos = ", ".join(f'"{t}"' for t in CAMERA_ODG_TYPES)
    window = ""
    if date_from and date_to:
        window = (
            f'  FILTER(SUBSTR(STR(?data), 1, 8) >= "{date_from}" && SUBSTR(STR(?data), 1, 8) <= "{date_to}")\n'
        )
    return (
        "PREFIX ocd: <http://dati.camera.it/ocd/>\n"
        "PREFIX dc: <http://purl.org/dc/elements/1.1/>\n"
        "PREFIX dcterms: <http://purl.org/dc/terms/>\n"
        "PREFIX ods: <http://lod.xdams.org/ontologies/ods/>\n"
        "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
        "SELECT DISTINCT ?aic ?identifier ?tipo ?titolo ?data ?concluso ?modified ?relation ?primo\n"
        "  (SAMPLE(?seduta) AS ?seduta1)\n"
        '  (GROUP_CONCAT(DISTINCT STR(?altro); separator=" ") AS ?altri)\n'
        '  (GROUP_CONCAT(DISTINCT STR(?subject); separator=" ") AS ?subjects)\n'
        "WHERE {\n"
        "  ?aic a ocd:aic ;\n"
        f"       ocd:rif_leg <http://dati.camera.it/ocd/legislatura.rdf/repubblica_{legislature}> ;\n"
        f'       ocd:ramo "{CAMERA_RAMO}" ;\n'
        "       dc:type ?tipo ;\n"
        "       dc:identifier ?identifier ;\n"
        "       dc:title ?titolo ;\n"
        "       dc:date ?data ;\n"
        "       ocd:concluso ?concluso ;\n"
        "       ods:modified ?modified ;\n"
        "       dc:relation ?relation ;\n"
        "       ocd:primo_firmatario ?primo .\n"
        f"  FILTER(?tipo IN ({tipos}))\n"
        '  FILTER(!STRENDS(STR(?aic), "_S"))\n'
        f'  FILTER(?modified >= "{since}"^^xsd:dateTime)\n'
        f"{window}"
        "  OPTIONAL { ?aic ocd:rif_seduta ?seduta }\n"
        "  OPTIONAL { ?aic ocd:altro_firmatario ?altro }\n"
        "  OPTIONAL { ?aic dcterms:subject ?subject }\n"
        "}\n"
        "GROUP BY ?aic ?identifier ?tipo ?titolo ?data ?concluso ?modified ?relation ?primo\n"
        "ORDER BY ?modified ?aic\n"
        f"LIMIT {CAMERA_SPARQL_PAGE}"
    )


def parse_odg_bindings(bindings: list[dict]) -> list[CameraOdgItem]:
    """One CameraOdgItem per distinct aic (first occurrence wins; later duplicates are dropped)."""
    items: list[CameraOdgItem] = []
    seen: set[str] = set()
    for b in bindings:
        aic = b["aic"]["value"]
        if aic in seen:
            continue
        seen.add(aic)
        items.append(
            CameraOdgItem(
                aic=aic,
                identifier=b["identifier"]["value"],
                tipo=b["tipo"]["value"],
                titolo=b["titolo"]["value"],
                data=b["data"]["value"],
                concluso=int(b["concluso"]["value"]),
                modified=b["modified"]["value"],
                relation=b["relation"]["value"],
                primo=b["primo"]["value"],
                seduta=b["seduta1"]["value"] if "seduta1" in b else None,
                altri=b["altri"]["value"].split() if b.get("altri") else [],
                subjects=b["subjects"]["value"].split() if b.get("subjects") else [],
            )
        )
    return items


async def iterate_odg(
    client: httpx.AsyncClient,
    legislature: int,
    since: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> AsyncIterator[list[CameraOdgItem]]:
    """Inclusive keyset on ods:modified (>= last seen value) + dedupe by URI; yields pages of new items."""
    cursor = since
    seen: set[str] = set()
    while True:
        q = query_camera_odg(legislature, cursor, date_from, date_to)
        resp = await polite_get(
            client,
            SPARQL_CAMERA_ENDPOINT,
            params={"query": q, "format": "application/sparql-results+json"},
            headers={"Accept": "application/sparql-results+json"},
            timeout=CAMERA_SPARQL_TIMEOUT,
        )
        page = parse_odg_bindings(resp.json()["results"]["bindings"])
        new = [i for i in page if i.aic not in seen]
        seen |= {i.aic for i in new}
        yield new
        if len(page) < CAMERA_SPARQL_PAGE:
            return
        if not new:
            raise RuntimeError("keyset stalled: >= 1000 ODG share ods:modified " + cursor)
        cursor = page[-1].modified


def metadati_hash(item: CameraOdgItem) -> str:
    """sha256 hex of "\\x1f".join([identifier, tipo, titolo, data, concluded, seduta or "",
    " ".join(sorted(altri)), " ".join(sorted(subjects))])."""
    return hashlib.sha256(
        "\x1f".join(
            [
                item.identifier,
                item.tipo,
                item.titolo,
                item.data,
                str(item.concluso),
                item.seduta or "",
                " ".join(sorted(item.altri)),
                " ".join(sorted(item.subjects)),
            ]
        ).encode("utf-8")
    ).hexdigest()


def odg_atto(identifier: str, legislature: int = 19) -> tuple[str | None, str | None, str | None]:
    """Returns (atto_uri, ddl display, fase_testo).

    9/01038-A/007        -> ("http://dati.camera.it/ocd/attocamera.rdf/ac19_1038", "C. 1038", "A")
    9/01532-bis-A/081    -> (".../ac19_1532-bis", "C. 1532-bis", "A")
    9/00536-B/008        -> (".../ac19_536", "C. 536", "B")
    0/01956/001/07       -> (".../ac19_1956", "C. 1956", None)
    9/DOC.VIII,N.4/067   -> (None, "Doc. VIII, n. 4", None)
    9/DOC.018-BIS,N.52/001 -> (None, "Doc. 018-BIS, n. 52", None)
    anything else        -> (None, None, None) and a logger.warning
    """
    m = RE_ODG_PDL.match(identifier)
    if m:
        numero_atto = str(int(m.group("num"))) + m.group("suf")
        atto_uri = f"http://dati.camera.it/ocd/attocamera.rdf/ac{legislature}_{numero_atto}"
        return atto_uri, f"C. {numero_atto}", m.group("stage")
    m = RE_ODG_DOC.match(identifier)
    if m:
        return None, f"Doc. {m.group('doc')}, n. {m.group('n')}", None
    logger.warning("odg_atto: unmapped identifier %r", identifier)
    return None, None, None


def strip_trailer(block: str) -> str:
    """Removes the trailing '<br>9/3076/<strong>3</strong>. (Testo modificato…) Tucci, Aiello…' line of an ODG text."""
    cut = block.rfind("<br")
    if cut == -1:
        return block
    tail = block[cut:]
    if not RE_STRONG_NUM.search(tail):
        return block
    end = tail.find("</p>")
    return block[:cut] + (tail[end:] if end != -1 else "")


def parse_sicross(page: str) -> SicrossPage | None:
    """None when the page is the 'FILE NON TROVATO' stub (no div.testoAtto)."""
    if RE_NOT_FOUND.search(page) and "testoAtto" not in page:
        return None
    persons: list[SicrossPerson] = []
    pres = RE_PRESENTATORI.search(page)
    if pres:
        for m in RE_PERSON_LINK.finditer(pres.group(1)):
            if m.group(1) not in {p.id_persona for p in persons}:
                persons.append(
                    SicrossPerson(
                        id_persona=m.group(1),
                        legislatura=int(m.group(2)),
                        nome=html.unescape(m.group(3)).strip(),
                    )
                )
    fb = RE_FASI_BLOCK.search(page)
    fasi = (
        [
            {"fase": m.group(1).strip(), "data": f"{m.group(4)}-{m.group(3)}-{m.group(2)}"}
            for m in RE_FASE.finditer(fb.group(1))
        ]
        if fb
        else []
    )
    st = RE_STATO_ITER.search(page)
    labels = {int(v): html.unescape(lbl).strip() for v, lbl in RE_OPTION.findall(page)}
    blocks = RE_TESTO_BLOCK.findall(page)
    n = len(blocks)
    versions = []
    for tab, body in blocks:
        tab_n = int(tab)
        body = strip_trailer(RE_CENTRA.sub("", body, count=1))
        label = re.sub(r"\s+", " ", labels.get(tab_n, "")).strip() or None
        versions.append(SicrossVersion(versione=n - tab_n + 1, etichetta=label, testo=html_to_text(body)))
    versions.sort(key=lambda v: v.versione)
    comm = RE_COMMISSIONE.search(page)
    sed = RE_SEDUTA.search(page)
    return SicrossPage(
        persons=persons,
        fasi=fasi,
        stato_iter=st.group(1).strip() if st else None,
        versions=versions,
        commissione=comm.groups() if comm else None,
        seduta=sed.group(1) if sed else None,
    )


def odg_outcome(fasi: list[dict[str, str]], stato_iter: str | None) -> tuple[str, str | None]:
    """Esito (first matching rule wins) and parere del Governo / Collegio Questori."""
    for f in fasi:
        if f["fase"] not in KNOWN_FASI:
            logger.warning("unknown fase %r", f["fase"])
    F = [f["fase"] for f in fasi]
    if "APPROVATO" in F:
        esito = "approvato"
    elif "RESPINTO" in F:
        esito = "respinto"
    elif "RITIRATO" in F:
        esito = "ritirato"
    elif "INAMMISSIBILE" in F:
        esito = "inammissibile"
    elif "DECADUTO" in F:
        esito = "decaduto"
    elif "ASSORBITO" in F:
        esito = "assorbito"
    elif "PRECLUSO" in F:
        esito = "precluso"
    elif "ACCOLTO COME RACCOMANDAZIONE" in F and "ACCOLTO" not in F:
        esito = "accolto_come_raccomandazione"
    elif "ACCOLTO" in F:
        esito = "accolto"
    elif not F or (stato_iter or "").upper() == "IN CORSO":
        esito = "in_corso"
    else:
        esito = "altro"
    parere_map = {
        "ACCOLTO": "accolto",
        "NON ACCOLTO": "non_accolto",
        "ACCOLTO COME RACCOMANDAZIONE": "accolto_come_raccomandazione",
        "PROPOSTA RIFORMULAZIONE": "proposta_riformulazione",
    }
    parere: str | None = None
    for i, fase in enumerate(F):
        if fase in ("PARERE GOVERNO", "PARERE COLLEGIO QUESTORI"):
            if i > 0:
                parere = parere_map.get(F[i - 1])
            break
    return esito, parere


def camera_odg_rows(
    item: CameraOdgItem, page: SicrossPage | None, legislature: int = 19
) -> list[dict]:
    """One row per text version of the ODG (or a single metadata-only #v1 row when page is None)."""
    chiave = f"camera:{legislature}:odg:{item.identifier.strip().upper()}"
    atto_uri, ddl, fase_testo = odg_atto(item.identifier.strip(), legislature)
    sede_commissione = "COMMISSIONE" in item.tipo
    data = yyyymmdd_to_iso(item.data)
    url_testo = item.relation.replace("http://", "https://", 1)
    if page is not None:
        esito, parere = odg_outcome(page.fasi, page.stato_iter)
        it = page.fasi
        mh = metadati_hash(item)
        sparql_ids = {m.group(1) for u in [item.primo, *item.altri] if (m := RE_DEPUTATO_ID.search(u))}
        if {p.id_persona for p in page.persons} != sparql_ids:
            logger.warning(
                "signatory set mismatch between SPARQL and sicross for %s", item.identifier
            )
        firmatari = ", ".join(p.nome for p in page.persons) or None
        firmatari_uri = (
            ", ".join(
                f"https://www.camera.it/deputati/elenco/{legislature}-{p.id_persona}"
                for p in page.persons
            )
            or None
        )
        firmatari_ocd = (
            ", ".join(
                f"http://dati.camera.it/ocd/deputato.rdf/d{p.id_persona}_{legislature}"
                for p in page.persons
            )
            or None
        )
        versions = page.versions
    else:
        esito, parere, it = None, None, []
        mh = None
        ids = [m.group(1) for u in [item.primo, *item.altri] if (m := RE_DEPUTATO_ID.search(u))]
        firmatari = None
        firmatari_uri = None
        firmatari_ocd = (
            ", ".join(f"http://dati.camera.it/ocd/deputato.rdf/d{i}_{legislature}" for i in ids)
            or None
        )
        versions = [SicrossVersion(versione=1, etichetta=None, testo="")]
    rows = []
    for v in versions:
        rows.append(
            {
                "id": f"{chiave}#v{v.versione}",
                "chiave_naturale": chiave,
                "ordine_versione": v.versione,
                "legislatura": legislature,
                "numero": item.identifier.strip(),
                "tipo": "G",
                "sede_commissione": sede_commissione,
                "atto_uri": atto_uri,
                "ddl": ddl,
                "url_testo": url_testo,
                "url_testo_xml": None,
                "data": data,
                "testo": v.testo or None,
                "firmatari": firmatari,
                "firmatari_uri": firmatari_uri,
                "firmatari_ocd": firmatari_ocd,
                "camera": "camera",
                "fonte_uri": item.aic,
                "esito": esito,
                "parere_governo": parere,
                "iter": it,
                "eurovoc": sorted(set(item.subjects)) or [],
                "fonte_modificata": item.modified,
                "metadati_hash": mh,
                "fase_testo": fase_testo,
                "versione_etichetta": v.etichetta,
                "testo_hash": testo_hash(v.testo) if v.testo else None,
                "aggiornato_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return rows
