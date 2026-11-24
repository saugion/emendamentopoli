"""Modelli condivisi dei workflow."""

from pydantic import BaseModel

CHUNK_SALVATAGGIO = 500


class Emendamento(BaseModel):
    id: str  # URI dati.senato.it
    legislatura: int
    numero: str  # es. "1.30"
    tipo: str  # E = emendamento, G = ordine del giorno
    sede_commissione: bool
    atto_uri: str
    url_testo: str
    url_testo_xml: str
    data: str | None = None  # data di presentazione (YYYY-MM-DD), dal testo AKN
    ddl: str | None = None  # es. "Ddl 391"
    testo: str | None = None  # testo plain-text
