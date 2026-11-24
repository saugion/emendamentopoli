import pytest

from amendments_worker.workflows.models import Amendment
from amendments_worker.workflows.evaluation import (
    RUBRIC,
    Score,
    Evaluation,
    EvaluationJSON,
    evaluation_prompt,
    evaluation_row,
    score_rows,
    composite_score,
)


def _eme() -> Amendment:
    return Amendment(
        id="http://dati.senato.it/emendamento/1",
        legislature=19,
        number="1.30",
        type="E",
        committee_seat=True,
        act_uri="http://dati.senato.it/osr/oggetto/1",
        text_url="http://www.senato.it/emend/1",
        text_xml_url="http://www.senato.it/emend/1.xml",
        date="2026-01-10",
        bill="Ddl 391",
        text="amendment text",
    )


def test_prompt_contains_key_fields():
    p = evaluation_prompt(_eme())
    assert "Emendamento 1.30" in p
    assert "Ddl 391" in p
    assert "2026-01-10" in p
    assert "Commissione" in p
    assert "amendment text" in p
    assert "trasparenza" in p and "concretezza" in p


def test_composite_score_rubric_weights():
    scores = [
        Score(criterion="trasparenza", score=10, rationale=""),
        Score(criterion="salute", score=0, rationale=""),
        Score(criterion="ambiente", score=4, rationale=""),
        Score(criterion="concretezza", score=5, rationale=""),
    ]
    expected = round(0.3 * 10 + 0.25 * 0 + 0.25 * 4 + 0.2 * 5, 1)
    assert expected == 5.0
    assert composite_score(scores) == expected


def test_parse_evaluation_json():
    raw = (
        '{"punteggi":{"trasparenza":{"punteggio":8,"motivazione":"a"},'
        '"salute":{"punteggio":6,"motivazione":"b"},'
        '"ambiente":{"punteggio":2,"motivazione":"c"},'
        '"concretezza":{"punteggio":9,"motivazione":"d"}},"riepilogo":"ok"}'
    )
    parsed = EvaluationJSON.model_validate_json(raw)
    assert set(parsed.scores) == set(RUBRIC)
    assert parsed.summary == "ok"


def test_parse_evaluation_json_invalid():
    with pytest.raises(Exception):
        EvaluationJSON.model_validate_json('{"punteggi": {}}')


def test_supabase_rows():
    v = Evaluation(
        amendment_id="http://dati.senato.it/emendamento/1",
        scores=[Score(criterion="salute", score=7, rationale="x")],
        total_score=7.0,
        summary="r",
        model="m",
        ts="2026-01-10T00:00:00+00:00",
    )
    row = evaluation_row(v)
    assert row["emendamento_id"] == v.amendment_id
    assert row["score_totale"] == 7.0
    rows = score_rows(42, v)
    assert rows == [
        {
            "valutazione_id": 42,
            "criterio": "salute",
            "punteggio": 7,
            "motivazione": "x",
        }
    ]
