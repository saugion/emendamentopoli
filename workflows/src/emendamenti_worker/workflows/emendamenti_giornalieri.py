"""Workflow notturno: scarica gli emendamenti del giorno prima e li salva su Supabase.

Sorgente: endpoint SPARQL di dati.senato.it (Open Data, licenza CC BY 3.0,
citazione "Senato della Repubblica - dati.senato.it"). La classe osr:Emendamento
non espone la data di presentazione, quindi si prendono gli emendamenti piu' recenti
(id numerico decrescente, che segue l'ordine di presentazione) e la data viene letta
dal testo Akoma Ntoso di ciascuno.
"""

import asyncio
import re
from datetime import timedelta
from urllib.parse import quote

from pydantic import BaseModel, Field

import mistralai.workflows as workflows

from .modelli import Emendamento
from .valutazione import salva_valutazioni, valuta_emendamento

SPARQL_ENDPOINT = "https://dati.senato.it/sparql"
UA = "curl/8.5.0"  # CloudFront accetta solo user-agent curl-like su questi path
FUSO_ORARIO = "Europe/Rome"


class EmendamentiGiornalieriInput(BaseModel):
    data: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Giorno da scaricare (YYYY-MM-DD); default: ieri, fuso Europe/Rome",
    )
    legislatura: int = 19
    limite: int = Field(
        default=1000,
        ge=1,
        le=20000,
        description="Quanti emendamenti recenti considerare al massimo",
    )


class EmendamentiGiornalieriOutput(BaseModel):
    data: str
    legislatura: int
    metadati_scaricati: int
    analizzati: int
    trovati: int
    salvati: int
    valutati: int
    ids: list[str]
    errori: list[str]
    errori_valutazione: list[str]


def query_metadati_recenti(legislatura: int, limite: int) -> str:
    return (
        "PREFIX osr: <http://dati.senato.it/osr/>\n"
        "SELECT ?eme ?numero ?tipo ?flagCom ?oggetto ?urlTesto ?urlTestoXml WHERE {\n"
        "  ?eme a osr:Emendamento ;\n"
        f"       osr:legislatura {legislatura} ;\n"
        "       osr:numero ?numero ;\n"
        "       osr:tipo ?tipo ;\n"
        "       osr:flagCommissione ?flagCom ;\n"
        "       osr:oggetto ?oggetto ;\n"
        "       osr:URLTesto ?urlTesto ;\n"
        "       osr:URLTestoXml ?urlTestoXml .\n"
        "}\n"
        # id a 7 cifre in legislatura XIX: l'ordine lessicografico DESC e' anche numerico DESC
        "ORDER BY DESC(?eme)\n"
        f"LIMIT {limite}"
    )


_RE_DATA = re.compile(r'FRBRdate date="(\d{4}-\d{2}-\d{2})" name="presentazione"')
_RE_DDL = re.compile(r'activeRef[^>]*showAs="(Ddl [^"]+)"')
_RE_BODY = re.compile(r"<an:amendmentBody>([\s\S]*?)</an:amendmentBody>")
_RE_DOC = re.compile(r"<(?:an:)?doc\b[^>]*>.*?</(?:an:)?doc>", re.S)
_RE_ENTITA_NUMERICA = re.compile(r"&#x[0-9a-fA-F]+;")
_RE_TAG = re.compile(r"<[^>]+>")
_ENTITA_NOMINATE = {
    "&#039;": "'",
    "&apos;": "'",
    "&quot;": '"',
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
}


def estrai_data(xml: str) -> str | None:
    m = _RE_DATA.search(xml)
    return m.group(1) if m else None


def estrai_ddl(xml: str) -> str | None:
    m = _RE_DDL.search(xml)
    return m.group(1) if m else None


def estrai_testo(xml: str) -> str | None:
    m = _RE_BODY.search(xml)
    if not m:
        return None
    s = _RE_DOC.sub("", m.group(1))
    s = _RE_ENTITA_NUMERICA.sub(" ", s)
    s = _RE_TAG.sub(" ", s)
    s = s.replace("&nbsp;", " ")
    s = re.sub(r"\s+", " ", s).strip()
    for entita, valore in _ENTITA_NOMINATE.items():
        s = s.replace(entita, valore)
    return s


def riga_supabase(e: Emendamento) -> dict:
    return {
        "id": e.id,
        "legislatura": e.legislatura,
        "numero": e.numero,
        "tipo": e.tipo,
        "sede_commissione": e.sede_commissione,
        "atto_uri": e.atto_uri,
        "ddl": e.ddl,
        "url_testo": e.url_testo,
        "url_testo_xml": e.url_testo_xml,
        "data": e.data,
        "testo": e.testo,
    }


@workflows.activity(start_to_close_timeout=timedelta(seconds=30), retry_policy_max_attempts=1)
async def data_ieri() -> str:
    from datetime import datetime, timedelta as delta
    from zoneinfo import ZoneInfo

    return (datetime.now(ZoneInfo(FUSO_ORARIO)) - delta(days=1)).date().isoformat()


@workflows.activity(start_to_close_timeout=timedelta(seconds=120), retry_policy_max_attempts=3)
async def scarica_metadati_recenti(legislatura: int, limite: int) -> list[Emendamento]:
    import httpx

    url = (
        SPARQL_ENDPOINT
        + "?query="
        + quote(query_metadati_recenti(legislatura, limite))
        + "&format=application%2Fsparql-results%2Bjson"
    )
    async with httpx.AsyncClient(
        timeout=90.0,
        headers={"Accept": "application/sparql-results+json", "User-Agent": UA},
    ) as client:
        resp = await client.get(url)
        if not resp.is_success:
            raise RuntimeError(f"SPARQL {resp.status_code}: {resp.text[:500]}")
        bindings = resp.json()["results"]["bindings"]
    emendamenti = [
        Emendamento(
            id=b["eme"]["value"],
            legislatura=legislatura,
            numero=b["numero"]["value"],
            tipo=b["tipo"]["value"],
            sede_commissione=b["flagCom"]["value"] == "1",
            atto_uri=b["oggetto"]["value"],
            url_testo=b["urlTesto"]["value"],
            url_testo_xml=b["urlTestoXml"]["value"],
        )
        for b in bindings
    ]
    emendamenti.sort(key=lambda e: int(e.id.rsplit("/", 1)[-1]), reverse=True)
    return emendamenti


@workflows.activity(start_to_close_timeout=timedelta(seconds=60), retry_policy_max_attempts=3)
async def arricchisci_emendamento(eme: Emendamento) -> Emendamento:
    import httpx

    url = eme.url_testo_xml
    if url.startswith("http:"):
        url = "https:" + url[len("http:") :]
    async with httpx.AsyncClient(timeout=45.0, headers={"User-Agent": UA}) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        # il XML e' iso-8859-1: decodifica byte->latin1
        xml = resp.content.decode("windows-1252")
    eme.data = estrai_data(xml)
    eme.ddl = estrai_ddl(xml)
    eme.testo = estrai_testo(xml)
    return eme


@workflows.activity(start_to_close_timeout=timedelta(seconds=180), retry_policy_max_attempts=2)
async def salva_emendamenti(emendamenti: list[Emendamento]) -> int:
    import os

    import httpx

    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_KEY"]
    righe = [riga_supabase(e) for e in emendamenti]
    async with httpx.AsyncClient(timeout=150.0) as client:
        resp = await client.post(
            f"{base}/rest/v1/emendamenti?on_conflict=id",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates",
            },
            json=righe,
        )
        resp.raise_for_status()
    return len(righe)


@workflows.workflow.define(
    name="emendamenti-giornalieri",
    workflow_display_name="Emendamenti del giorno prima (Senato)",
    workflow_description=(
        "Scarica da dati.senato.it (SPARQL, CC BY 3.0) gli emendamenti presentati nel giorno "
        "indicato (default: ieri, fuso Europe/Rome), recupera i testi Akoma Ntoso, salva "
        "gli emendamenti su Supabase (upsert per id) e li valuta con Mistral (valutazioni + punteggi)."
    ),
    execution_timeout=timedelta(hours=2),
)
class EmendamentiGiornalieriWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: EmendamentiGiornalieriInput) -> EmendamentiGiornalieriOutput:
        data = input.data if input.data else await data_ieri()
        metadati = await scarica_metadati_recenti(input.legislatura, input.limite)
        risultati = await asyncio.gather(
            *[arricchisci_emendamento(m) for m in metadati],
            return_exceptions=True,
        )
        errori = [
            f"{m.id}: {type(r).__name__}: {r}"
            for m, r in zip(metadati, risultati)
            if isinstance(r, BaseException)
        ]
        analizzati = [r for r in risultati if not isinstance(r, BaseException)]
        del_giorno = [e for e in analizzati if e.data == data]
        salvati = await salva_emendamenti(del_giorno) if del_giorno else 0
        ris_val = await asyncio.gather(
            *[valuta_emendamento(e) for e in del_giorno],
            return_exceptions=True,
        )
        errori_valutazione = [
            f"{e.id}: {type(r).__name__}: {r}"
            for e, r in zip(del_giorno, ris_val)
            if isinstance(r, BaseException)
        ]
        valutazioni = [r for r in ris_val if not isinstance(r, BaseException)]
        valutati = await salva_valutazioni(valutazioni) if valutazioni else 0
        return EmendamentiGiornalieriOutput(
            data=data,
            legislatura=input.legislatura,
            metadati_scaricati=len(metadati),
            analizzati=len(analizzati),
            trovati=len(del_giorno),
            salvati=salvati,
            valutati=valutati,
            ids=[e.id for e in del_giorno],
            errori=errori,
            errori_valutazione=errori_valutazione,
        )
