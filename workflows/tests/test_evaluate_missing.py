import mistralai.workflows as workflows

from amendments_worker.workflows import evaluate_missing as em
from amendments_worker.workflows.evaluation import Score, Evaluation

MISSING: list[em.Amendment] = []
EVALUATED: list[Evaluation] = []


def _eme(n: int) -> em.Amendment:
    return em.Amendment(
        id=f"http://dati.senato.it/emendamento/{n}",
        legislature=19,
        number=f"1.{n}",
        type="E",
        committee_seat=False,
        act_uri="http://dati.senato.it/osr/oggetto/1",
        text_url=f"http://www.senato.it/emend/{n}",
        text_xml_url=f"http://www.senato.it/emend/{n}.xml",
        date="2026-11-20",
        bill="Ddl 391",
        text="text",
    )


async def _without_evaluation(limit: int) -> list[em.Amendment]:
    return [_eme(1), _eme(2)]


async def _evaluate(eme: em.Amendment) -> Evaluation:
    if eme.id.endswith("/2"):
        raise RuntimeError("LLM unavailable")
    return Evaluation(
        amendment_id=eme.id,
        scores=[Score(criterion="ambiente", score=5, rationale="x")],
        total_score=5.0,
        summary="r",
        model="test",
        ts="2026-11-24T00:00:00+00:00",
    )


async def _save(evaluations: list[Evaluation]) -> int:
    EVALUATED.extend(evaluations)
    return len(evaluations)


async def test_backfill_missing(monkeypatch) -> None:
    EVALUATED.clear()
    monkeypatch.setattr(em, "amendments_without_evaluation", _without_evaluation)
    monkeypatch.setattr(em, "evaluate_amendment", _evaluate)
    monkeypatch.setattr(em, "save_evaluations", _save)
    result = await workflows.execute_workflow(
        em.EvaluateMissingWorkflow,
        params=em.EvaluateMissingInput(limit=10),
    )
    d = result if isinstance(result, dict) else result.model_dump()
    assert d["found"] == 2
    assert d["evaluated"] == 1
    assert d["ids"] == ["http://dati.senato.it/emendamento/1"]
    assert len(d["errors"]) == 1
    assert [v.amendment_id for v in EVALUATED] == ["http://dati.senato.it/emendamento/1"]


async def test_missing_query_uses_anti_join(monkeypatch):
    import httpx
    import respx

    from amendments_worker.workflows import supabase as supabase_mod

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    with respx.mock(assert_all_called=False) as router:
        route = router.get("https://example.supabase.co/rest/v1/emendamenti").mock(
            return_value=httpx.Response(200, json=[])
        )
        await supabase_mod.amendments_without_evaluation(500)
    params = route.calls[0].request.url.params
    assert params["valutazioni"] == "is.null"
    assert params["corrente"] == "is.true"
    assert params["testo"] == "not.is.null"
    assert "valutazioni()" in params["select"]
    assert "valutazioni.id" not in params
