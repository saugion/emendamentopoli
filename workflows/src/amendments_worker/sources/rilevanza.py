"""Money-relevance pre-filter (rule R1/R2/R3): lexical signals on the folded text
plus atto-level flags from the atti table. Only rows with rilevante_spesa = true
(and corrente, and testo not null) are sent to the LLM.

Level A (atto-level): atto_dl, atto_bilancio, atto_bilancio_interno,
atto_commissione_bilancio. Level B (text): testo_spesa_forte / testo_spesa_debole.
"""

import re

from .common import fold

RILEVANZA_REGOLA = "R1"

STRONG = [
    r"\b\d[\d.]*(?:,\d+)?\s*(?:euro|eur|€)(?!\w)",
    r"€\s*\d",
    r"\beuro\s+\d",
    r"\b(?:milion[ei]|miliard[oi])\s+di\s+euro\b",
    r"\bstanziament[oi]\b",
    r"\bstanzia(?:re|no|to|ti|ta|te|ndo)?\b",
    r"\b(?:risorse|dotazion[ei])\s+(?:finanziari[ae]|economich[ae]|economic[ao]|aggiuntiv[ae]|pubblich[ae]|adeguat[ae]|necessari[ae]|dedicat[ae]|stanziat[ae])\b",
    r"\bfond[oi]\s+(?:per|di|nazional[ei]|special[ei]|unic[oi]|social[ei]|perequativ[oi]|rotativ[oi]|sanitari[oi])\b",
    r"\bcopertura\s+finanziaria\b",
    r"\bnuovi\s+o\s+maggiori\s+oneri\b",
    r"\boner[ei]\s+(?:finanziari|a\s+carico\s+(?:del|della|dello|dei|delle)\s+(?:bilancio|finanza|stato|erario))",
    r"\bfinanza\s+pubblica\b",
    r"\bbilancio\s+dello\s+stato\b",
    r"\bautorizzazion[ei]\s+di\s+spesa\b",
    r"\bspesa\s+pubblica\b",
    r"\bcredit[oi]\s+d'\s?imposta\b",
    r"\bpnrr\b",
    r"\bcofinanziament[oi]\b",
    r"\bcapitol[oi]\s+(?:di\s+(?:spesa|bilancio)|\d+)\b",
    r"\b(?:incremento|aumento|riduzione|rifinanziamento)\s+(?:del|della|dei|delle)\s+(?:fondo|fondi|dotazione|dotazioni|risorse|stanziamento|stanziamenti|spesa)\b",
    r"\bfondo\s+sanitario\s+nazionale\b",
    r"\blegge\s+di\s+bilancio\b",
]
WEAK = [
    r"\bspes[ae]\b",
    r"\brisorse\b",
    r"\bfinanziament[oi]\b",
    r"\bcontribut[oi]\b",
    r"\bincentiv[oi]\b",
    r"\bagevolazion[ei]\b",
    r"\bbonus\b",
    r"\bvoucher\b",
    r"\bsussidi[oi]?\b",
    r"\bristor[oi]\b",
    r"\bindennita\b",
    r"\brimbors[oi]\b",
    r"\bdetrazion[ei]\b",
    r"\bdeduzion[ei]\b",
    r"\besenzion[ei]\b",
    r"\b(?:aliquot[ae]|accis[ae]|iva|irpef|ires|irap|imu|tribut[oi]|impost[ae])\b",
    r"\bassunzion[ei]\b",
    r"\bremunerazion[ei]\b|\bretribuzion[ei]\b|\bstipend[io]\b",
    r"\binvestiment[oi]\b",
    r"\boner[ei]\b",
]
RE_STRONG = [re.compile(p) for p in STRONG]
RE_WEAK = [re.compile(p) for p in WEAK]

LEVEL_A_CODES = ("atto_dl", "atto_bilancio", "atto_bilancio_interno", "atto_commissione_bilancio")


def segnali_testo(text: str | None) -> tuple[bool, bool]:
    """(strong, weak) on fold(text); (False, False) for None/empty."""
    if not text:
        return False, False
    f = fold(text)
    return any(r.search(f) for r in RE_STRONG), any(r.search(f) for r in RE_WEAK)


def calcola_rilevanza(
    testo: str | None, atto_flags: dict[str, bool], regola: str = RILEVANZA_REGOLA
) -> tuple[bool | None, list[str]]:
    """atto_flags keys: atto_dl, atto_bilancio, atto_bilancio_interno, atto_commissione_bilancio.
    Returns (rilevante_spesa, motivi). None only when testo is null and no Level A code is true."""
    strong, weak = segnali_testo(testo)
    motivi = sorted(
        [k for k in LEVEL_A_CODES if atto_flags.get(k)]
        + (["testo_spesa_forte"] if strong else [])
        + (["testo_spesa_debole"] if weak else [])
    )
    if testo is None and not any(atto_flags.get(k) for k in LEVEL_A_CODES):
        return None, motivi
    if regola == "R2":
        relevant = bool(
            strong or weak or atto_flags.get("atto_bilancio") or atto_flags.get("atto_bilancio_interno")
        )
    elif regola == "R3":
        relevant = True  # testo is not None here
    else:  # R1
        relevant = bool(
            strong
            or atto_flags.get("atto_bilancio")
            or atto_flags.get("atto_bilancio_interno")
            or (weak and (atto_flags.get("atto_dl") or atto_flags.get("atto_commissione_bilancio")))
        )
    return relevant, motivi
