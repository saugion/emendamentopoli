"""Camera emendamenti source: atto pages (www.camera.it) -> list pages -> detail pages
(documenti.camera.it/apps/emendamenti/).

List pages are XHTML with BOM and support conditional GET (If-Modified-Since -> 304);
detail pages have no Last-Modified, so each new/changed proposal is fetched once.
One list row = one <tr class="normale" id="tr.id.<id>.position.<YYYYMMDD>.<ord>">; a
detail page holds every version (sedutaNuova = current) of the proposal.
"""

import hashlib
import html
import logging
import re
import urllib.parse
from datetime import datetime, timezone

from pydantic import BaseModel

from .common import html_to_text, testo_hash, yyyymmdd_to_iso

logger = logging.getLogger(__name__)

ATTO_PAGE = "https://www.camera.it/leg19/126?tab=1&leg={legislature}&idDocumento={numero}"
RE_LIST_LINK = re.compile(r'href="(https://documenti\.camera\.it/apps/emendamenti/getProposteEmendative\.aspx\?[^"]+)"')
DETAIL_BASE = "https://documenti.camera.it/apps/emendamenti/"

RE_ROW = re.compile(r'<tr class="normale" id="tr\.id\.(?P<id>.+?)\.position\.(?P<pos>\d{8})\.(?P<ord>\d+)">(?P<body>.*?)</tr>', re.S)
RE_ROW_LINK = re.compile(r'href="(?P<href>[^"]*getPropostaEmendativa\.aspx\?[^"]*idPropostaEmendativa=(?P<id>[^&"]+)&amp;position=(?P<pos>\d{8}))"[^>]*title="pubblicata il (?P<d>\d{2})/(?P<m>\d{2})/(?P<y>\d{4})"')
RE_IDENTICI = re.compile(r'<span class="emendamentiIdentici">(.*?)</span>', re.S)
RE_ESITO_LIST = re.compile(r'<span class="esito(\w+)">')
RE_PERSONA = re.compile(r'idPersona=(\d+)&amp;webType=Normale">([^<]+)</a>')
RE_IDENT_TOKEN = re.compile(r"^(\S+?\.)(?:\s|$)")

RE_SEDUTA_BLOCK = re.compile(
    r'<div style="display:(?:block|none);" id="(?P<sid>[a-z.]+\.(?P<date>\d{8}))" class="(?P<cls>sedutaNuova|sedutaVecchia)">(?P<body>.*?)'
    r'(?=<div style="display:(?:block|none);" id="[a-z.]+\.\d{8}" class="seduta|</div></div></div></body>)',
    re.S,
)
RE_NUMERO = re.compile(r'<div class="numero"><strong>([^<]+)</strong>\s*(\(<em>nuova formulazione</em>\))?')
RE_ESITO_DET = re.compile(r'<div class="esito"><span class="(\w+)">')
RE_TESTO = re.compile(r'<div class="testo">(.*?)</div><div class="proponenti">', re.S)
RE_PROPONENTI = re.compile(r'<div class="proponenti">(.*?)</div>', re.S)
RE_PROP_IDENT = re.compile(r'<div class="proposteIdentiche">(.*?)</div>', re.S)
RE_HEADER = re.compile(r'<h4 class="lavori">\s*Proposta emendativa (?P<num>\S+?)\s*in (?P<sede>.+?) riferita al C\. <a[^>]*>(?P<testo>[^<]+)</a></h4>', re.S)

ESITO_MAP = {
    "approvato": "approvato",
    "inammissibile": "inammissibile",
    "precluso": "precluso",
    "irricevibile": "irricevibile",
    "ritirato": "ritirato",
    "riammesso": "riammesso",
}


def parse_identici(fragment: str) -> list[str]:
    """'(ident. 1.3. (nuova formulazione), 1.4. )' -> ['1.3.', '1.4.']"""
    txt = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ").split())
    txt = re.sub(r"^\(?\s*ident\.\s*", "", txt)
    txt = re.sub(r"\s*\)$", "", txt)
    out = []
    for part in txt.split(","):
        m = RE_IDENT_TOKEN.match(part.strip())
        if m:
            out.append(m.group(1))
    return out


class ListRow(BaseModel):
    id: str                 # "1.7."
    position: str           # "20261118"
    ordine: int
    publicada: str | None  # "2026-11-18"
    href: str | None        # raw href (unescape + remove whitespace before use)
    identici: list[str]
    esito: str | None       # lower-cased class suffix: "approvato", "inammissibile", ...
    primo_id_persona: str | None
    primo_nome: str | None  # "Fontana Ilaria" (nbsp -> space)


def parse_list(page: str) -> list[ListRow]:
    """One ListRow per RE_ROW match, fields from the regexes above."""
    rows: list[ListRow] = []
    for m in RE_ROW.finditer(page):
        body = m.group("body")
        link = RE_ROW_LINK.search(body)
        ident = RE_IDENTICI.search(body)
        esito = None
        esito_m = RE_ESITO_LIST.search(body)
        if esito_m:
            esito = ESITO_MAP.get(esito_m.group(1).lower())
            if esito is None:
                logger.warning("unknown esito class %r", esito_m.group(1))
        person = RE_PERSONA.search(body)
        rows.append(
            ListRow(
                id=m.group("id") + ".",
                position=m.group("pos"),
                ordine=int(m.group("ord")),
                publicada=(
                    f"{link.group('y')}-{link.group('m')}-{link.group('d')}" if link else None
                ),
                href=link.group("href") if link else None,
                identici=parse_identici(ident.group(1)) if ident else [],
                esito=esito,
                primo_id_persona=person.group(1) if person else None,
                primo_nome=person.group(2).replace("\xa0", " ").strip() if person else None,
            )
        )
    return rows


class DetailVersion(BaseModel):
    data: str                      # "20261117" (= position)
    corrente_html: bool            # class == "sedutaNuova"
    numero: str                    # "1.2."
    nuova_formulazione: bool
    esito: str | None              # "approvato" ...
    testo: str | None              # html_to_text of div.testo
    proponenti: list[tuple[str, str]]   # [(idPersona, "Santillo Agostino"), ...] in page order
    proponenti_testo: str          # plain text of div.proponenti (used when there are no links)
    identici: list[str]            # cleaned with RE_IDENT_TOKEN


class DetailPage(BaseModel):
    header_numero: str | None      # "1.2."
    header_sede: str | None        # "Assemblea" | "VIII Commissione in sede referente"
    header_testo: str | None       # "3133-A" | "3133"
    versioni: list[DetailVersion]  # sorted by data ascending


def parse_detail(page: str) -> DetailPage:
    h = RE_HEADER.search(page)
    versions: list[DetailVersion] = []
    for m in RE_SEDUTA_BLOCK.finditer(page):
        body = m.group("body")
        num = RE_NUMERO.search(body)
        esito = None
        esito_m = RE_ESITO_DET.search(body)
        if esito_m:
            esito = ESITO_MAP.get(esito_m.group(1).lower())
            if esito is None:
                logger.warning("unknown esito class %r", esito_m.group(1))
        testo_m = RE_TESTO.search(body)
        prop_m = RE_PROPONENTI.search(body)
        prop_frag = prop_m.group(1) if prop_m else ""
        prop_links = [
            (pid, nome.replace("\xa0", " ").strip())
            for pid, nome in RE_PERSONA.findall(prop_frag)
        ]
        ident_m = RE_PROP_IDENT.search(body)
        versions.append(
            DetailVersion(
                data=m.group("date"),
                corrente_html=m.group("cls") == "sedutaNuova",
                numero=num.group(1) if num else "",
                nuova_formulazione=bool(num and num.group(2)),
                esito=esito,
                testo=html_to_text(testo_m.group(1)) if testo_m else None,
                proponenti=prop_links,
                proponenti_testo=html_to_text(prop_frag),
                identici=parse_identici(ident_m.group(1)) if ident_m else [],
            )
        )
    versions.sort(key=lambda v: v.data)
    return DetailPage(
        header_numero=h.group("num") if h else None,
        header_sede=re.sub(r"\s+", " ", h.group("sede")).strip() if h else None,
        header_testo=h.group("testo") if h else None,
        versioni=versions,
    )


def extract_list_urls(page: str) -> list[str]:
    """The getProposteEmendative.aspx list URLs of an atto page (Seduta variants excluded)."""
    return sorted({html.unescape(m.group(1)) for m in RE_LIST_LINK.finditer(page)})


def list_url_parts(url: str) -> tuple[str, str] | None:
    """(atto_numero, sede token) of a list URL; None (with a warning) for anything else."""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    cp = q.get("contenitorePortante", [None])[0]
    urn = q.get("urnTestoRiferimento", [None])[0]
    if not cp or not urn or not cp.startswith("leg.19.eme.ac."):
        logger.warning("list URL without contenitorePortante/urnTestoRiferimento: %s", url)
        return None
    atto_numero = cp.removeprefix("leg.19.eme.ac.")
    parts = urn.split(":")
    if len(parts) >= 9 and parts[6] == "ass":
        return atto_numero, "ass"
    if len(parts) >= 9 and parts[6] == "com":
        return atto_numero, f"com{parts[7]}{parts[8][:3]}"
    logger.warning("list URL with unknown sede urn %r: %s", urn, url)
    return None


def detail_url(href: str) -> str:
    return DETAIL_BASE + re.sub(r"\s+", "", html.unescape(href))


def eme_rows(
    atto_numero: str,
    sede: str,
    list_row: ListRow,
    detail: DetailPage,
    list_url: str,
    legislature: int = 19,
) -> list[dict]:
    """One row per version of the proposal: id = <chiave>#<position YYYYMMDD>."""
    chiave = f"camera:{legislature}:em:AC{atto_numero}:{sede}:{list_row.id}"
    atto_uri = f"http://dati.camera.it/ocd/attocamera.rdf/ac{legislature}_{atto_numero}"
    ddl = f"C. {detail.header_testo}" if detail.header_testo else None
    url_testo = detail_url(list_row.href or "")
    mh = hashlib.sha256(
        "\x1f".join(
            [
                list_row.id,
                list_row.position,
                list_row.esito or "",
                " ".join(list_row.identici),
                list_row.primo_id_persona or "",
            ]
        ).encode("utf-8")
    ).hexdigest()
    rows = []
    for v in detail.versioni:
        esito = v.esito or (list_row.esito if v.data == list_row.position else None)
        gruppo = None
        if v.identici:
            gruppo = (
                f"camera:{legislature}:em:AC{atto_numero}:{sede}:ident:"
                + "|".join(sorted({list_row.id, *v.identici}))
            )
        names = [n for _, n in v.proponenti]
        rows.append(
            {
                "id": f"{chiave}#{v.data}",
                "chiave_naturale": chiave,
                "ordine_versione": int(v.data),
                "legislatura": legislature,
                "numero": list_row.id,
                "tipo": "E",
                "sede_commissione": sede != "ass",
                "atto_uri": atto_uri,
                "ddl": ddl,
                "url_testo": url_testo,
                "url_testo_xml": None,
                "data": yyyymmdd_to_iso(v.data),
                "testo": v.testo,
                "firmatari": ", ".join(names) if names else (v.proponenti_testo or None),
                "firmatari_uri": (
                    ", ".join(
                        f"https://www.camera.it/deputati/elenco/{legislature}-{pid}"
                        for pid, _ in v.proponenti
                    )
                    or None
                ),
                "firmatari_ocd": (
                    ", ".join(
                        f"http://dati.camera.it/ocd/deputato.rdf/d{pid}_{legislature}"
                        for pid, _ in v.proponenti
                    )
                    or None
                ),
                "camera": "camera",
                "fonte_uri": list_url,
                "esito": esito,
                "gruppo_identici": gruppo,
                "versione_etichetta": "nuova formulazione" if v.nuova_formulazione else None,
                "metadati_hash": mh,
                "testo_hash": testo_hash(v.testo),
                "aggiornato_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return rows
