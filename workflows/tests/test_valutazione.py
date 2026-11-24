import pytest

from emendamenti_worker.workflows.modelli import Emendamento
from emendamenti_worker.workflows.valutazione import (
    RUBRICA,
    Punteggio,
    Valutazione,
    ValutazioneJSON,
    prompt_valutazione,
    riga_valutazione,
    righe_punteggi,
    score_composito,
)


def _eme() -> Emendamento:
    return Emendamento(
        id="http://dati.senato.it/emendamento/1",
        legislatura=19,
        numero="1.30",
        tipo="E",
        sede_commissione=True,
        atto_uri="http://dati.senato.it/osr/oggetto/1",
        url_testo="http://www.senato.it/emend/1",
        url_testo_xml="http://www.senato.it/emend/1.xml",
        data="2026-01-10",
        ddl="Ddl 391",
        testo="testo dell'emendamento",
    )


def test_prompt_contiene_campi_chiave():
    p = prompt_valutazione(_eme())
    assert "Emendamento 1.30" in p
    assert "Ddl 391" in p
    assert "2026-01-10" in p
    assert "Commissione" in p
    assert "testo dell'emendamento" in p
    assert "trasparenza" in p and "concretezza" in p


def test_score_composito_pesi_rubrica():
    punteggi = [
        Punteggio(criterio="trasparenza", punteggio=10, motivazione=""),
        Punteggio(criterio="salute", punteggio=0, motivazione=""),
        Punteggio(criterio="ambiente", punteggio=4, motivazione=""),
        Punteggio(criterio="concretezza", punteggio=5, motivazione=""),
    ]
    atteso = round(0.3 * 10 + 0.25 * 0 + 0.25 * 4 + 0.2 * 5, 1)
    assert atteso == 5.0
    assert score_composito(punteggi) == atteso


def test_parse_valutazione_json():
    raw = (
        '{"punteggi":{"trasparenza":{"punteggio":8,"motivazione":"a"},'
        '"salute":{"punteggio":6,"motivazione":"b"},'
        '"ambiente":{"punteggio":2,"motivazione":"c"},'
        '"concretezza":{"punteggio":9,"motivazione":"d"}},"riepilogo":"ok"}'
    )
    parsed = ValutazioneJSON.model_validate_json(raw)
    assert set(parsed.punteggi) == set(RUBRICA)
    assert parsed.riepilogo == "ok"


def test_parse_valutazione_json_invalido():
    with pytest.raises(Exception):
        ValutazioneJSON.model_validate_json('{"punteggi": {}}')


def test_righe_supabase():
    v = Valutazione(
        emendamento_id="http://dati.senato.it/emendamento/1",
        punteggi=[Punteggio(criterio="salute", punteggio=7, motivazione="x")],
        score_totale=7.0,
        riepilogo="r",
        modello="m",
        ts="2026-01-10T00:00:00+00:00",
    )
    riga = riga_valutazione(v)
    assert riga["emendamento_id"] == v.emendamento_id
    assert riga["score_totale"] == 7.0
    righe = righe_punteggi(42, v)
    assert righe == [
        {
            "valutazione_id": 42,
            "criterio": "salute",
            "punteggio": 7,
            "motivazione": "x",
        }
    ]
