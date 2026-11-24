import mistralai.workflows as workflows

from amendments_worker.workflows import daily_amendments as mod
from amendments_worker.workflows.evaluation import Score, Evaluation

DATE = "2026-01-10"
SAVED: list[mod.Amendment] = []
EVALUATED: list[Evaluation] = []


def _eme(n: int) -> mod.Amendment:
    return mod.Amendment(
        id=f"http://dati.senato.it/emendamento/{n}",
        legislature=19,
        number=f"1.{n}",
        type="E",
        committee_seat=False,
        act_uri="http://dati.senato.it/osr/oggetto/1",
        text_url=f"http://www.senato.it/emend/{n}",
        text_xml_url=f"http://www.senato.it/emend/{n}.xml",
    )


async def _yesterday() -> str:
    return DATE


async def _page(legislature: int, page_limit: int, offset: int) -> list[mod.Amendment]:
    return [] if offset else [_eme(1), _eme(2), _eme(3)]


async def _saved_map_empty(legislature: int) -> dict:
    return {}


def _saved_row(data: str | None, firmatari: str | None = "Rossi") -> dict:
    return {"data": data, "firmatari": firmatari}


async def _enrich(eme: mod.Amendment) -> mod.Amendment:
    if eme.id.endswith("/2"):
        raise RuntimeError("AKN unreachable")
    eme.date = DATE if eme.id.endswith("/1") else "2026-01-09"
    eme.bill = "Ddl 391"
    eme.text = "text"
    return eme


async def _save(amendments: list[mod.Amendment]) -> int:
    SAVED.extend(amendments)
    return len(amendments)


async def _evaluate(eme: mod.Amendment) -> Evaluation:
    return Evaluation(
        amendment_id=eme.id,
        scores=[Score(criterion="salute", score=8, rationale="ok")],
        total_score=8.0,
        summary="summary",
        model="test",
        ts="2026-01-10T00:00:00+00:00",
    )


async def _save_evaluations(evaluations: list[Evaluation]) -> int:
    EVALUATED.extend(evaluations)
    return len(evaluations)


def _patch(monkeypatch) -> None:
    monkeypatch.setattr(mod, "yesterday", _yesterday)
    monkeypatch.setattr(mod, "fetch_metadata_page", _page)
    monkeypatch.setattr(mod, "saved_amendments_map", _saved_map_empty)
    monkeypatch.setattr(mod, "enrich_amendment", _enrich)
    monkeypatch.setattr(mod, "save_amendments", _save)
    monkeypatch.setattr(mod, "evaluate_amendment", _evaluate)
    monkeypatch.setattr(mod, "save_evaluations", _save_evaluations)


async def test_daily_flow(monkeypatch) -> None:
    SAVED.clear()
    EVALUATED.clear()
    _patch(monkeypatch)
    result = await workflows.execute_workflow(
        mod.DailyAmendmentsWorkflow,
        params=mod.DailyAmendmentsInput(legislature=19, limit=10),
    )
    d = result if isinstance(result, dict) else result.model_dump()
    assert d["date"] == DATE
    assert d["metadata_fetched"] == 3
    assert d["analyzed"] == 2
    assert d["found"] == 1
    assert d["saved"] == 1
    assert d["evaluated"] == 1
    assert d["ids"] == ["http://dati.senato.it/emendamento/1"]
    assert len(d["errors"]) == 1
    assert d["evaluation_errors"] == []
    assert len(SAVED) == 1
    assert [v.amendment_id for v in EVALUATED] == ["http://dati.senato.it/emendamento/1"]


async def test_explicit_date_and_no_results(monkeypatch) -> None:
    SAVED.clear()
    EVALUATED.clear()
    _patch(monkeypatch)
    result = await workflows.execute_workflow(
        mod.DailyAmendmentsWorkflow,
        params=mod.DailyAmendmentsInput(date="2020-01-01", legislature=19, limit=10),
    )
    d = result if isinstance(result, dict) else result.model_dump()
    assert d["date"] == "2020-01-01"
    assert d["end_date"] == "2020-01-01"
    assert d["found"] == 0
    assert d["saved"] == 0
    assert d["evaluated"] == 0
    assert SAVED == []
    assert EVALUATED == []


async def test_multi_day_period(monkeypatch) -> None:
    SAVED.clear()
    EVALUATED.clear()

    async def _enrich_range(eme: mod.Amendment) -> mod.Amendment:
        n = int(eme.id.rsplit("/", 1)[-1])
        eme.date = {1: DATE, 2: "2026-01-11", 3: "2026-01-12"}[n]
        eme.bill = "Ddl 391"
        eme.text = "text"
        return eme

    monkeypatch.setattr(mod, "yesterday", _yesterday)
    monkeypatch.setattr(mod, "fetch_metadata_page", _page)
    monkeypatch.setattr(mod, "saved_amendments_map", _saved_map_empty)
    monkeypatch.setattr(mod, "enrich_amendment", _enrich_range)
    monkeypatch.setattr(mod, "save_amendments", _save)
    monkeypatch.setattr(mod, "evaluate_amendment", _evaluate)
    monkeypatch.setattr(mod, "save_evaluations", _save_evaluations)
    result = await workflows.execute_workflow(
        mod.DailyAmendmentsWorkflow,
        params=mod.DailyAmendmentsInput(
            date=DATE, end_date="2026-01-11", legislature=19, limit=10
        ),
    )
    d = result if isinstance(result, dict) else result.model_dump()
    assert d["date"] == DATE
    assert d["end_date"] == "2026-01-11"
    assert d["found"] == 2
    assert d["saved"] == 2
    assert d["evaluated"] == 2
    assert d["ids"] == [
        "http://dati.senato.it/emendamento/1",
        "http://dati.senato.it/emendamento/2",
    ]
    assert d["errors"] == []
    assert d["evaluation_errors"] == []


async def test_pagination_with_early_stop(monkeypatch) -> None:
    SAVED.clear()
    EVALUATED.clear()
    pages = [
        [_eme(1), _eme(2)],
        [_eme(3), _eme(4)],
        [_eme(5)],
    ]

    async def _pages(legislature: int, page_limit: int, offset: int) -> list[mod.Amendment]:
        return pages.pop(0) if pages else []

    async def _enrich_pages(eme: mod.Amendment) -> mod.Amendment:
        n = int(eme.id.rsplit("/", 1)[-1])
        eme.date = {1: "2026-01-12", 2: "2026-01-11", 3: DATE, 4: "2026-01-09", 5: "2026-01-08"}[n]
        eme.bill = "Ddl 391"
        eme.text = "text"
        return eme

    monkeypatch.setattr(mod, "yesterday", _yesterday)
    monkeypatch.setattr(mod, "fetch_metadata_page", _pages)
    monkeypatch.setattr(mod, "saved_amendments_map", _saved_map_empty)
    monkeypatch.setattr(mod, "enrich_amendment", _enrich_pages)
    monkeypatch.setattr(mod, "save_amendments", _save)
    monkeypatch.setattr(mod, "evaluate_amendment", _evaluate)
    monkeypatch.setattr(mod, "save_evaluations", _save_evaluations)
    result = await workflows.execute_workflow(
        mod.DailyAmendmentsWorkflow,
        params=mod.DailyAmendmentsInput(date=DATE, legislature=19, limit=10),
    )
    d = result if isinstance(result, dict) else result.model_dump()
    assert d["metadata_fetched"] == 4
    assert d["analyzed"] == 4
    assert d["found"] == 1
    assert d["ids"] == ["http://dati.senato.it/emendamento/3"]


async def test_skips_already_saved(monkeypatch) -> None:
    SAVED.clear()
    EVALUATED.clear()
    seen: list[str] = []

    async def _saved_map(legislature: int) -> dict:
        return {"http://dati.senato.it/emendamento/1": _saved_row(DATE)}

    async def _enrich_skip(eme: mod.Amendment) -> mod.Amendment:
        seen.append(eme.id)
        eme.date = "2026-01-09"
        eme.bill = "Ddl 391"
        eme.text = "text"
        return eme

    monkeypatch.setattr(mod, "yesterday", _yesterday)
    monkeypatch.setattr(mod, "saved_amendments_map", _saved_map)
    monkeypatch.setattr(mod, "fetch_metadata_page", _page)
    monkeypatch.setattr(mod, "enrich_amendment", _enrich_skip)
    monkeypatch.setattr(mod, "save_amendments", _save)
    monkeypatch.setattr(mod, "evaluate_amendment", _evaluate)
    monkeypatch.setattr(mod, "save_evaluations", _save_evaluations)
    result = await workflows.execute_workflow(
        mod.DailyAmendmentsWorkflow,
        params=mod.DailyAmendmentsInput(date=DATE, legislature=19, limit=10),
    )
    d = result if isinstance(result, dict) else result.model_dump()
    assert seen == [
        "http://dati.senato.it/emendamento/2",
        "http://dati.senato.it/emendamento/3",
    ]
    assert d["metadata_fetched"] == 3
    assert d["analyzed"] == 2
    assert d["found"] == 0
    assert d["saved"] == 0


async def test_backfills_missing_signatories(monkeypatch) -> None:
    SAVED.clear()
    EVALUATED.clear()
    seen: list[str] = []

    async def _saved_map(legislature: int) -> dict:
        # already saved, but without signatories (ingested before the feature)
        return {"http://dati.senato.it/emendamento/1": _saved_row(DATE, firmatari=None)}

    async def _enrich_backfill(eme: mod.Amendment) -> mod.Amendment:
        seen.append(eme.id)
        eme.date = DATE
        eme.bill = "Ddl 391"
        eme.text = "text"
        eme.signatories = ["Rossi"]
        eme.signatory_uris = ["https://www.senato.it/leg/19/BGT/Schede/Attsen/00000407.htm"]
        return eme

    monkeypatch.setattr(mod, "yesterday", _yesterday)
    monkeypatch.setattr(mod, "saved_amendments_map", _saved_map)
    monkeypatch.setattr(mod, "fetch_metadata_page", _page)
    monkeypatch.setattr(mod, "enrich_amendment", _enrich_backfill)
    monkeypatch.setattr(mod, "save_amendments", _save)
    monkeypatch.setattr(mod, "evaluate_amendment", _evaluate)
    monkeypatch.setattr(mod, "save_evaluations", _save_evaluations)
    result = await workflows.execute_workflow(
        mod.DailyAmendmentsWorkflow,
        params=mod.DailyAmendmentsInput(date=DATE, legislature=19, limit=10),
    )
    d = result if isinstance(result, dict) else result.model_dump()
    # eme/1 is re-enriched (missing firmatari) and saved again, but not re-evaluated
    assert "http://dati.senato.it/emendamento/1" in seen
    assert d["saved"] == 3
    assert d["evaluated"] == 2
    assert SAVED[0].signatories == ["Rossi"]
    assert [v.amendment_id for v in EVALUATED] == [
        "http://dati.senato.it/emendamento/2",
        "http://dati.senato.it/emendamento/3",
    ]


async def test_incremental_save_per_page(monkeypatch) -> None:
    SAVED.clear()
    EVALUATED.clear()
    save_calls: list[int] = []
    pages = [
        [_eme(1)],
        [_eme(2), _eme(3)],
    ]

    async def _pages(legislature: int, page_limit: int, offset: int) -> list[mod.Amendment]:
        return pages.pop(0) if pages else []

    async def _enrich_pages(eme: mod.Amendment) -> mod.Amendment:
        n = int(eme.id.rsplit("/", 1)[-1])
        eme.date = DATE if n < 3 else "2026-01-09"
        eme.bill = "Ddl 391"
        eme.text = "text"
        return eme

    async def _save_counted(amendments: list[mod.Amendment]) -> int:
        save_calls.append(len(amendments))
        return await _save(amendments)

    monkeypatch.setattr(mod, "yesterday", _yesterday)
    monkeypatch.setattr(mod, "saved_amendments_map", _saved_map_empty)
    monkeypatch.setattr(mod, "fetch_metadata_page", _pages)
    monkeypatch.setattr(mod, "enrich_amendment", _enrich_pages)
    monkeypatch.setattr(mod, "save_amendments", _save_counted)
    monkeypatch.setattr(mod, "evaluate_amendment", _evaluate)
    monkeypatch.setattr(mod, "save_evaluations", _save_evaluations)
    result = await workflows.execute_workflow(
        mod.DailyAmendmentsWorkflow,
        params=mod.DailyAmendmentsInput(date=DATE, legislature=19, limit=10),
    )
    d = result if isinstance(result, dict) else result.model_dump()
    assert save_calls == [1, 1]
    assert d["metadata_fetched"] == 3
    assert d["analyzed"] == 3
    assert d["found"] == 2
    assert d["saved"] == 2
    assert d["evaluated"] == 2
    assert d["ids"] == [
        "http://dati.senato.it/emendamento/1",
        "http://dati.senato.it/emendamento/2",
    ]
    assert len(SAVED) == 2
    assert len(EVALUATED) == 2


async def test_page_failure_is_not_fatal(monkeypatch) -> None:
    SAVED.clear()
    EVALUATED.clear()
    calls = 0

    async def _pages_fail(legislature: int, page_limit: int, offset: int) -> list[mod.Amendment]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return [_eme(1)]
        raise RuntimeError("SPARQL 403")

    async def _enrich_one(eme: mod.Amendment) -> mod.Amendment:
        eme.date = DATE
        eme.bill = "Ddl 391"
        eme.text = "text"
        return eme

    monkeypatch.setattr(mod, "yesterday", _yesterday)
    monkeypatch.setattr(mod, "saved_amendments_map", _saved_map_empty)
    monkeypatch.setattr(mod, "fetch_metadata_page", _pages_fail)
    monkeypatch.setattr(mod, "enrich_amendment", _enrich_one)
    monkeypatch.setattr(mod, "save_amendments", _save)
    monkeypatch.setattr(mod, "evaluate_amendment", _evaluate)
    monkeypatch.setattr(mod, "save_evaluations", _save_evaluations)
    result = await workflows.execute_workflow(
        mod.DailyAmendmentsWorkflow,
        params=mod.DailyAmendmentsInput(date=DATE, legislature=19, limit=10),
    )
    d = result if isinstance(result, dict) else result.model_dump()
    assert d["metadata_fetched"] == 1
    assert d["found"] == 1
    assert d["saved"] == 1
    assert d["ids"] == ["http://dati.senato.it/emendamento/1"]
    assert len(d["errors"]) == 1
    assert "fetch_metadata_page" in d["errors"][0]


def test_inconsistent_period() -> None:
    import pytest

    with pytest.raises(ValueError):
        mod.DailyAmendmentsInput(date="2026-11-30", end_date="2026-11-01")
