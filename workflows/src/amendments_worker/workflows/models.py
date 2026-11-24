"""Shared workflow models."""

from typing import Literal

from pydantic import BaseModel, Field

SAVE_CHUNK = 500


class Amendment(BaseModel):
    id: str  # Senate: dati.senato.it URI; Camera: "<natural key>#v<n>" or "<natural key>#<YYYYMMDD>"
    legislature: int
    number: str  # e.g. "1.30", "9/03076/003", "1.7."
    type: str  # E = amendment, G = order of the day (Senate also has Q, C)
    committee_seat: bool
    act_uri: str | None = None
    text_url: str
    text_xml_url: str | None = None
    date: str | None = None  # submission/publication date (YYYY-MM-DD)
    bill: str | None = None  # e.g. "Ddl 391", "C. 3076"
    text: str | None = None  # plain-text body
    signatories: list[str] = Field(default_factory=list)
    signatory_uris: list[str] = Field(default_factory=list)  # profile URLs aligned with signatories ("" if unknown)
    chamber: Literal["senato", "camera"] = "senato"
    natural_key: str | None = None
    version_order: int = 0
    version_label: str | None = None
    text_hash: str | None = None
    source_uri: str | None = None
    signatory_ids: list[str] = Field(default_factory=list)  # ocd deputato URIs aligned with signatories (Camera)
    outcome: str | None = None
    government_opinion: str | None = None
    iter_phases: list[dict[str, str]] = Field(default_factory=list)
    eurovoc: list[str] = Field(default_factory=list)
    source_modified: str | None = None
    metadata_hash: str | None = None
    text_stage: str | None = None
    identical_group: str | None = None
    relevant_spending: bool | None = None
    relevance_reasons: list[str] = Field(default_factory=list)
