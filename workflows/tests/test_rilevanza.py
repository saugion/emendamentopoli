from pathlib import Path

import pytest

from amendments_worker.sources.camera_odg import parse_sicross
from amendments_worker.sources.rilevanza import calcola_rilevanza, segnali_testo

FIX = Path(__file__).parent / "fixtures"

STRONG_POSITIVES = [
    "autorizza la spesa di 5 milioni di euro per l'anno 2026",
    "è stanziato 1.500.000 euro a decorrere dal 2027",
    "per un importo pari a 2,5 milioni di euro",
    "a valere sulle risorse del Fondo per lo sviluppo e la coesione",
    "incremento della dotazione del fondo per le politiche sociali",
    "ai relativi oneri si provvede mediante corrispondente riduzione del fondo speciale di parte corrente",
    "dall'attuazione della presente disposizione non devono derivare nuovi o maggiori oneri per la finanza pubblica",
    "a prevedere adeguate risorse finanziarie per il trasporto pubblico locale",
    "ad aumentare lo stanziamento destinato alla sanità territoriale",
    "a stanziare risorse aggiuntive per la ricostruzione",
    "compatibilmente con i vincoli di finanza pubblica",
    "attraverso lo stanziamento di risorse pubbliche adeguate",
    "riconoscere un credito d'imposta pari al 50 per cento",
    "nell'ambito delle risorse del PNRR",
    "il cofinanziamento regionale è pari al 20 per cento",
    "sul capitolo 2345 dello stato di previsione del Ministero",
    "con apposito capitolo di spesa",
    "è autorizzata la spesa di 300.000 euro annui",
    "€ 10.000 per ciascun beneficiario",
    "euro 2.000.000 per l'anno 2025",
    "a incrementare il Fondo sanitario nazionale",
    "nella prossima legge di bilancio",
    "rifinanziamento del fondo di garanzia per le PMI",
    "a carico del bilancio dello Stato",
    "riduzione dell'autorizzazione di spesa di cui all'articolo 1, comma 200",
    "un contenimento della spesa pubblica",
    "fondi per la non autosufficienza",
    "dotazioni finanziarie del Ministero",
    "oneri a carico della finanza pubblica",
    "stanziamenti per il diritto allo studio",
    "un miliardo di euro per il 2026",
    "fondo nazionale per le politiche migratorie",
]

WEAK_ONLY = [
    "a sostenere il reddito delle famiglie con contributi straordinari",
    "prevedere incentivi per l'acquisto di veicoli elettrici",
    "introdurre agevolazioni per i giovani agricoltori",
    "estendere il bonus psicologo",
    "riconoscere un ristoro alle imprese danneggiate",
    "ridurre l'aliquota IVA sui prodotti per l'infanzia",
    "garantire nuove assunzioni di personale sanitario",
    "aumentare le retribuzioni del personale docente",
    "favorire gli investimenti nelle aree interne",
    "detrazioni per le spese mediche",
    "esenzione dal ticket per i minori",
    "indennità di disoccupazione per i lavoratori stagionali",
]

NEGATIVES = [
    "a istituire un tavolo tecnico presso il Ministero",
    "a garantire la trasparenza delle procedure di nomina",
    "a valutare l'opportunità di adottare iniziative normative volte a semplificare le procedure",
    "a promuovere la cultura della legalità nelle scuole",
    "il fondamentale ruolo delle associazioni di volontariato",
    "la Fondazione Latina 2032 è istituita con sede a Latina",
    "approfondire a fondo le cause del dissesto idrogeologico",
    "sul fondo della questione il Governo si è impegnato",
    "rafforzare il coordinamento tra le forze di polizia",
    "a monitorare l'attuazione della direttiva europea",
    "il lupo è classificato come specie protetta",
    "a riferire alle Camere entro sei mesi",
    "rivedere la disciplina della responsabilità penale colposa",
    "assicurare il rispetto del principio di sussidiarietà",
    "a tutelare la libertà di stampa",
    "a garantire la parità di genere negli organi collegiali",
    "a sostenere la candidatura UNESCO del sito",
    "a semplificare le procedure di valutazione di impatto ambientale",
    "il ministro competente ha avviato un'interlocuzione con le regioni",
    "favorire la digitalizzazione dei procedimenti amministrativi",
    "ad adottare linee guida per la formazione del personale",
    "il provvedimento in esame interviene in materia di giustizia",
    "promuovere campagne di sensibilizzazione sul cyberbullismo",
    "a rafforzare la cooperazione internazionale in materia di sicurezza",
    "a vigilare sul rispetto dei diritti dei detenuti",
    "le audizioni svolte in Commissione hanno evidenziato criticità",
    "a chiarire in via interpretativa l'ambito di applicazione della norma",
    "ad avviare un confronto con le parti sociali",
    "a garantire la continuità del servizio di trasporto",
    "a modificare il regolamento della Camera",
    "la stanza del Presidente è aperta al pubblico",
    "il fondale marino del golfo",
]


@pytest.mark.parametrize("text", STRONG_POSITIVES)
def test_strong_positives(text):
    strong, _ = segnali_testo(text)
    assert strong is True, text


@pytest.mark.parametrize("text", WEAK_ONLY)
def test_weak_only(text):
    strong, weak = segnali_testo(text)
    assert strong is False and weak is True, text


@pytest.mark.parametrize("text", NEGATIVES)
def test_negatives(text):
    assert segnali_testo(text) == (False, False), text


def test_fixture_signals():
    expected = {
        "camera_sicross_9_03076_003.html": (True, True),
        "camera_sicross_9_01038-A_007.html": (True, True),
        "camera_sicross_9_03076_011.html": (False, True),
        "camera_sicross_9_03076_001.html": (False, False),
        "camera_sicross_9_02097-A_005.html": (False, False),
        "camera_sicross_0_01956_001_07.html": (False, False),
        "camera_sicross_9_DOC_018-BIS_N_52_001.html": (False, False),
        "camera_sicross_9_DOC_VIII_N_4_067.html": (False, False),
    }
    for fname, exp in expected.items():
        page = parse_sicross((FIX / fname).read_text(encoding="utf-8"))
        assert page is not None
        testo = page.versions[-1].testo
        assert segnali_testo(testo) == exp, fname


def test_rule_r1():
    assert calcola_rilevanza("contributi straordinari", {"atto_dl": True}) == (
        True,
        ["atto_dl", "testo_spesa_debole"],
    )
    assert calcola_rilevanza("contributi straordinari", {}) == (False, ["testo_spesa_debole"])
    assert calcola_rilevanza("a istituire un tavolo tecnico", {"atto_bilancio": True}) == (
        True,
        ["atto_bilancio"],
    )
    assert calcola_rilevanza(None, {}) == (None, [])


def test_rule_r2_r3():
    assert calcola_rilevanza("contributi straordinari", {}, regola="R2")[0] is True
    assert calcola_rilevanza("a istituire un tavolo tecnico", {}, regola="R3")[0] is True
    assert calcola_rilevanza(None, {"atto_dl": True})[0] is False  # Level A alone, R1, no text
    assert calcola_rilevanza(None, {"atto_bilancio": True}) == (True, ["atto_bilancio"])
