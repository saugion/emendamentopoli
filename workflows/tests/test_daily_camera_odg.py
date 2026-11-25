import json
from pathlib import Path

import httpx
import pytest
import respx

import mistralai.workflows as workflows
from amendments_worker.sources import common
from amendments_worker.workflows import daily_camera_odg as mod
from amendments_worker.workflows.daily_amendments import DailyAmendmentsWorkflow
from amendments_worker.workflows.evaluate_missing import EvaluateMissingWorkflow

from fakes import FakeSupabase

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(common, "CAMERA_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(common, "CAMERA_BACKOFF_S", (0.0, 0.0, 0.0, 0.0))
    monkeypatch.setattr(mod, "CAMERA_ODG_RECENT_DAYS", 0)


def _items(*specs: tuple[str, str]) -> list[mod.CameraOdgItem]:
    """specs: (identifier, sicross route name); the SPARQL fixture provides the other fields."""
    data = json.loads((FIX / "camera_odg_sparql_page.json").read_text(encoding="utf-8"))
    base = mod.parse_odg_bindings(data["results"]["bindings"])
    items = []
    for i, (identifier, route) in enumerate(specs):
        item = base[i % len(base)].model_copy(
            update={
                "identifier": identifier,
                "relation": f"https://documenti.camera.it/sicross/{route}",
            }
        )
        items.append(item)
    return items


def _wire_db(monkeypatch, fake: FakeSupabase):
    async def _cursor_get(nome: str):
        return fake.cursori.get(nome)

    async def _cursor_set(nome: str, valore: str):
        fake.cursori[nome] = valore

    async def _current_rows(keys: list[str]):
        return {
            r["chiave_naturale"]: r
            for r in fake.emendamenti.values()
            if r.get("chiave_naturale") in set(keys) and r.get("corrente")
        }

    async def _atti_flags(legislature: int):
        return dict(fake.atti)

    async def _save_rows(rows: list[dict]):
        fake.upsert_emendamenti(rows)
        fake.rpc_ricalcola_versioni(sorted({r["chiave_naturale"] for r in rows}))

    monkeypatch.setattr(mod, "db_cursor_get", _cursor_get)
    monkeypatch.setattr(mod, "db_cursor_set", _cursor_set)
    monkeypatch.setattr(mod, "db_current_rows", _current_rows)
    monkeypatch.setattr(mod, "db_atti_flags", _atti_flags)
    monkeypatch.setattr(mod, "db_save_odg_rows", _save_rows)


def _wire_eval(monkeypatch, fake: FakeSupabase):
    """Fake evaluate_and_save mirroring the real gate + reuse-by-testo_hash logic."""
    from amendments_worker.workflows.evaluation import EvalOutcome

    async def _evaluate_and_save(amendment_id: str) -> EvalOutcome:
        row = fake.emendamenti[amendment_id]
        if any(v["emendamento_id"] == amendment_id for v in fake.valutazioni.values()):
            return EvalOutcome(amendment_id=amendment_id, status="skipped", reason="already_evaluated")
        if not row.get("corrente"):
            return EvalOutcome(amendment_id=amendment_id, status="skipped", reason="not_current")
        if row.get("rilevante_spesa") is not True:
            return EvalOutcome(amendment_id=amendment_id, status="skipped", reason="not_relevant")
        if not row.get("testo"):
            return EvalOutcome(amendment_id=amendment_id, status="skipped", reason="no_text")
        h = row.get("testo_hash")
        src = next(
            (
                v
                for v in fake.valutazioni.values()
                if v.get("testo_hash") == h and v.get("rubrica_versione") == 2
            ),
            None,
        )
        vid = fake._next_valutazione_id
        fake._next_valutazione_id += 1
        v = {
            "id": vid,
            "emendamento_id": amendment_id,
            "rubrica_versione": 2,
            "testo_hash": h,
            "score_totale": src.get("score_totale") if src else 7.0,
            "copiata_da": src["id"] if src else None,
        }
        fake.valutazioni[vid] = v
        return EvalOutcome(amendment_id=amendment_id, status="reused" if src else "evaluated")

    monkeypatch.setattr(mod, "evaluate_and_save", _evaluate_and_save)


def _sicross_router(router, routes: dict[str, str]):
    for route, fixture in routes.items():
        router.get(f"https://documenti.camera.it/sicross/{route}").mock(
            return_value=httpx.Response(200, content=(FIX / fixture).read_bytes())
        )


def _patch_pages(monkeypatch, pages: list[list[mod.CameraOdgItem]]):
    calls = {"i": 0}

    async def _fetch(legislature: int, since: str, date_from, date_to):
        page = pages[min(calls["i"], len(pages) - 1)]
        calls["i"] += 1
        return page

    monkeypatch.setattr(mod, "fetch_camera_odg_page", _fetch)
    return calls


def test_camera_odg_workflow_is_discoverable():
    from amendments_worker.workflows.camera_atti import CameraAttiWorkflow
    from amendments_worker.workflows.daily_camera_emendamenti import DailyCameraEmendamentiWorkflow
    from amendments_worker.workflows.maintenance import MaintenanceWorkflow

    classes = workflows.discover_all_workflows_in_package("amendments_worker.workflows")
    assert DailyAmendmentsWorkflow in classes
    assert EvaluateMissingWorkflow in classes
    assert mod.DailyCameraOdgWorkflow in classes
    assert CameraAttiWorkflow in classes
    assert MaintenanceWorkflow in classes
    assert DailyCameraEmendamentiWorkflow in classes


async def _run(monkeypatch, fake: FakeSupabase, items, routes, **input_kw):
    _wire_db(monkeypatch, fake)
    _wire_eval(monkeypatch, fake)
    _patch_pages(monkeypatch, [items])
    with respx.mock(assert_all_called=False) as router:
        _sicross_router(router, routes)
        result = await workflows.execute_workflow(
            mod.DailyCameraOdgWorkflow, params=mod.DailyCameraOdgInput(**input_kw)
        )
    return result if isinstance(result, dict) else result.model_dump()


async def test_incremental_run_saves_versions_and_cursor(monkeypatch):
    fake = FakeSupabase()
    items = _items(("9/03076/003", "003"), ("9/03076/001", "001"))
    routes = {"003": "camera_sicross_9_03076_003.html", "001": "camera_sicross_9_03076_001.html"}
    d = await _run(monkeypatch, fake, items, routes)
    assert d["mode"] == "incremental"
    assert d["fetched"] == 2
    assert d["saved_rows"] == 3  # 9/03076/003 has 2 versions, 9/03076/001 has 1
    assert d["skipped_unchanged"] == 0
    assert d["evaluated"] == 1  # only 9/03076/003#v2 is current AND money-relevant
    assert d["cursor"] == max(i.modified for i in items)
    assert fake.cursori["camera_odg_modified"] == d["cursor"]
    assert len(fake.emendamenti) == 3
    v2 = fake.emendamenti["camera:19:odg:9/03076/003#v2"]
    assert v2["corrente"] is True
    assert fake.emendamenti["camera:19:odg:9/03076/003#v1"]["corrente"] is False


async def test_two_runs_zero_extra_rows_and_evaluations(monkeypatch):
    fake = FakeSupabase()
    items = _items(("9/03076/003", "003"), ("9/03076/001", "001"))
    routes = {"003": "camera_sicross_9_03076_003.html", "001": "camera_sicross_9_03076_001.html"}
    d1 = await _run(monkeypatch, fake, items, routes)
    sizes1 = (len(fake.emendamenti), len(fake.valutazioni), len(fake.punteggi))
    d2 = await _run(monkeypatch, fake, items, routes)
    assert d2["fetched"] == 0
    assert d2["skipped_unchanged"] == 2
    assert d2["saved_rows"] == 0
    assert d2["evaluated"] == 0
    assert (len(fake.emendamenti), len(fake.valutazioni), len(fake.punteggi)) == sizes1
    assert d1["saved_rows"] == 3


async def test_metadata_change_triggers_refetch(monkeypatch):
    fake = FakeSupabase()
    items = _items(("9/03076/003", "003"), ("9/03076/001", "001"))
    routes = {"003": "camera_sicross_9_03076_003.html", "001": "camera_sicross_9_03076_001.html"}
    await _run(monkeypatch, fake, items, routes)
    changed = [items[0].model_copy(update={"concluso": items[0].concluso + 1}), items[1]]
    d2 = await _run(monkeypatch, fake, changed, routes)
    assert d2["fetched"] == 1
    assert d2["skipped_unchanged"] == 1


async def test_recent_window_refetches(monkeypatch):
    monkeypatch.setattr(mod, "CAMERA_ODG_RECENT_DAYS", 14)
    fake = FakeSupabase()
    items = _items(("9/03076/003", "003"), ("9/03076/001", "001"))
    routes = {"003": "camera_sicross_9_03076_003.html", "001": "camera_sicross_9_03076_001.html"}
    await _run(monkeypatch, fake, items, routes)
    sizes1 = len(fake.emendamenti)
    d2 = await _run(monkeypatch, fake, items, routes)
    assert d2["fetched"] == 2  # the recent-window pass force-refetches
    assert d2["evaluated"] == 0
    assert len(fake.emendamenti) == sizes1  # same rows upserted again


async def test_max_items_stops_and_sets_cursor(monkeypatch):
    fake = FakeSupabase()
    items = _items(
        ("9/03076/003", "003"), ("9/03076/001", "001"), ("9/03076/011", "011")
    )
    routes = {
        "003": "camera_sicross_9_03076_003.html",
        "001": "camera_sicross_9_03076_001.html",
        "011": "camera_sicross_9_03076_011.html",
    }
    d = await _run(monkeypatch, fake, items, routes, max_items=2)
    assert d["stopped_by_max_items"] is True
    assert d["fetched"] == 2
    assert d["cursor"] == items[1].modified


async def test_reuse_by_text_hash(monkeypatch):
    fake = FakeSupabase()
    # two distinct ODG whose sicross page is the same fixture -> identical testo_hash
    items = _items(("9/03076/003", "003"), ("9/03076/009", "003"))
    routes = {"003": "camera_sicross_9_03076_003.html"}
    d = await _run(monkeypatch, fake, items, routes)
    assert d["fetched"] == 2
    assert d["evaluated"] == 1
    assert d["reused_evaluations"] == 1
    vals = list(fake.valutazioni.values())
    assert len(vals) == 2
    copied = [v for v in vals if v.get("copiata_da") is not None]
    assert len(copied) == 1
    assert copied[0]["copiata_da"] == [v for v in vals if v.get("copiata_da") is None][0]["id"]
    assert copied[0]["testo_hash"] == [v for v in vals if v.get("copiata_da") is None][0]["testo_hash"]


async def test_db_cursor_get_paginates_on_nome_not_id(monkeypatch):
    # ingest_cursori has no id column (PK is nome): ordering by the default key="id"
    # makes PostgREST return 400, so the cursor read must keyset-paginate on nome.
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[{"nome": "camera_odg_modified", "valore": "2026-11-25T00:00:00Z"}])

    with respx.mock(assert_all_called=False) as router:
        router.get("https://example.supabase.co/rest/v1/ingest_cursori").mock(side_effect=handler)
        valore = await mod.db_cursor_get("camera_odg_modified")
    assert valore == "2026-11-25T00:00:00Z"
    assert all(r.url.params["order"] == "nome.asc" for r in requests)
    assert all("id" not in r.url.params for r in requests)
