"""Shared workflow models."""

from pydantic import BaseModel, Field

SAVE_CHUNK = 500


class Amendment(BaseModel):
    id: str  # dati.senato.it URI
    legislature: int
    number: str  # e.g. "1.30"
    type: str  # E = amendment, G = order of the day
    committee_seat: bool
    act_uri: str
    text_url: str
    text_xml_url: str
    date: str | None = None  # submission date (YYYY-MM-DD), from the AKN text
    bill: str | None = None  # e.g. "Ddl 391"
    text: str | None = None  # plain-text body
    signatories: list[str] = Field(default_factory=list)  # senators' surnames (first = proposer)
    signatory_uris: list[str] = Field(default_factory=list)  # senato.it profile URLs, aligned with signatories ("" if unknown)
