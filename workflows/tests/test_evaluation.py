import pytest

from amendments_worker.workflows.models import Amendment
from amendments_worker.workflows.evaluation import (
    RUBRIC,
    Score,
    Evaluation,
    EvaluationJSON,
    evaluation_prompt,
    composite_score,
)
from amendments_worker.workflows.supabase import evaluation_row, score_rows


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


async def test_save_evaluations_maps_scores_by_amendment_id(monkeypatch):
    import json

    import httpx
    import respx

    from amendments_worker.workflows import supabase as supabase_mod

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")

    def _eval(amendment_id: str, criterion: str) -> Evaluation:
        return Evaluation(
            amendment_id=amendment_id,
            scores=[Score(criterion=criterion, score=5, rationale="r")],
            total_score=5.0,
            summary="s",
            model="m",
            ts="2026-01-10T00:00:00+00:00",
        )

    chunk = [_eval("A", "salute"), _eval("B", "ambiente")]
    with respx.mock(assert_all_called=False) as router:
        val_route = router.post("https://example.supabase.co/rest/v1/valutazioni").mock(
            return_value=httpx.Response(201, json=[{"id": 7, "emendamento_id": "B"}])
        )
        score_route = router.post("https://example.supabase.co/rest/v1/punteggi").mock(
            return_value=httpx.Response(201, json=[])
        )
        total = await supabase_mod.insert_evaluations(chunk)
    assert total == 1  # A was ignored as a duplicate
    assert "on_conflict=emendamento_id,rubrica_versione" in str(val_route.calls[0].request.url)
    assert (
        val_route.calls[0].request.headers["Prefer"]
        == "resolution=ignore-duplicates,return=representation"
    )
    score_body = json.loads(score_route.calls[0].request.content)
    assert score_body == [
        {"valutazione_id": 7, "criterio": "ambiente", "punteggio": 5, "motivazione": "r"}
    ]


V2_PAYLOAD = (
    '{"punteggi":{"trasparenza":{"punteggio":8,"motivazione":"a"},'
    '"salute":{"punteggio":6,"motivazione":"b"},'
    '"ambiente":{"punteggio":2,"motivazione":"c"},'
    '"concretezza":{"punteggio":9,"motivazione":"d"}},"riepilogo":"ok",'
    '"impatto_finanziario":{"punteggio":12,"motivazione":"stanzia risorse"},'
    '"alloca_risorse":true,"importo_stimato_eur":-5000000}'
)


def test_parse_evaluation_json_v2():
    from amendments_worker.workflows.evaluation import build_evaluation

    parsed = EvaluationJSON.model_validate_json(V2_PAYLOAD)
    assert parsed.financial_impact is not None and parsed.financial_impact.score == 12
    assert parsed.allocates_resources is True
    assert parsed.estimated_amount_eur == -5000000
    v = build_evaluation(_eme(), parsed)
    assert v.rubric_version == 2
    assert v.financial_impact == 10.0  # clamped to [0, 10]
    assert v.estimated_amount_eur == 5000000.0  # stored as absolute value
    assert v.allocates_resources is True
    assert v.financial_rationale == "stanzia risorse"
    assert v.text_hash is not None
    # composite score unchanged and independent of impatto_finanziario
    assert v.total_score == composite_score(v.scores)


def test_build_evaluation_requires_financial_fields():
    from amendments_worker.workflows.evaluation import build_evaluation

    v1 = EvaluationJSON.model_validate_json(
        '{"punteggi":{"trasparenza":{"punteggio":8,"motivazione":"a"}},"riepilogo":"ok"}'
    )
    with pytest.raises(ValueError, match="impatto_finanziario"):
        build_evaluation(_eme(), v1)


def test_prompt_is_chamber_aware():
    eme = _eme().model_copy(update={"chamber": "camera", "bill": "C. 3076", "type": "G"})
    p = evaluation_prompt(eme)
    assert "(Camera," in p
    assert "La Camera e' l'organo" in p
    assert "Ordine del giorno 1.30" in p
    assert "C. 3076" in p
    assert "impatto_finanziario" in p and "alloca_risorse" in p and "importo_stimato_eur" in p


def test_evaluation_row_has_financial_keys():
    from amendments_worker.workflows.evaluation import build_evaluation
    from amendments_worker.workflows.supabase import evaluation_row

    parsed = EvaluationJSON.model_validate_json(V2_PAYLOAD)
    row = evaluation_row(build_evaluation(_eme(), parsed))
    assert row["impatto_finanziario"] == 10.0
    assert row["alloca_risorse"] is True
    assert row["importo_stimato_eur"] == 5000000.0
    assert row["motivazione_finanziaria"] == "stanzia risorse"
    assert row["rubrica_versione"] == 2


async def test_evaluate_repairs_malformed_json_once():
    from amendments_worker.workflows import evaluation as ev

    bad = '{"punteggi": "non-oggetto", "riepilogo": {"testo": "no"}}'
    calls = []

    async def fake_complete(messages):
        calls.append(messages)
        return bad if len(calls) == 1 else V2_PAYLOAD

    v = await ev._evaluate(_eme(), complete=fake_complete)
    assert len(calls) == 2
    assert calls[1][-2] == {"role": "assistant", "content": bad}
    assert calls[1][-1]["role"] == "user" and "schema JSON" in calls[1][-1]["content"]
    assert v.rubric_version == 2
    assert v.financial_impact == 10.0


async def test_evaluate_repairs_missing_financial_fields():
    from amendments_worker.workflows import evaluation as ev

    v1_only = (
        '{"punteggi":{"trasparenza":{"punteggio":8,"motivazione":"a"},'
        '"salute":{"punteggio":6,"motivazione":"b"},'
        '"ambiente":{"punteggio":2,"motivazione":"c"},'
        '"concretezza":{"punteggio":9,"motivazione":"d"}},"riepilogo":"ok"}'
    )

    async def fake_complete(messages):
        return v1_only if len(messages) == 1 else V2_PAYLOAD

    v = await ev._evaluate(_eme(), complete=fake_complete)
    assert v.financial_impact == 10.0
    assert v.allocates_resources is True


async def test_evaluate_raises_when_repair_still_invalid():
    from amendments_worker.workflows import evaluation as ev

    async def fake_complete(messages):
        return '{"punteggi": {}}'

    with pytest.raises(ValueError):
        await ev._evaluate(_eme(), complete=fake_complete)
