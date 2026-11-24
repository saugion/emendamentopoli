import json

import httpx
import respx

from amendments_worker.sources import supabase
from amendments_worker.sources.supabase import rpc, select_all, upsert

BASE = "https://example.supabase.co"


def _env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", BASE)
    monkeypatch.setenv("SUPABASE_KEY", "test-key")


async def test_select_all_paginates_beyond_1000(monkeypatch):
    _env(monkeypatch)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        params = request.url.params
        gt = params.get("id")
        start = 0 if gt is None else int(gt.removeprefix("gt.r")) + 1
        limit = int(params["limit"])
        rows = [{"id": f"r{i:05d}"} for i in range(start, min(start + limit, 2345))]
        return httpx.Response(200, json=rows)

    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/rest/v1/emendamenti").mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            rows = await select_all(client, "emendamenti", "id")
    assert len(rows) == 2345
    assert len(requests) == 3
    assert requests[1].url.params["id"] == "gt.r00999"
    assert requests[2].url.params["id"] == "gt.r01999"
    assert all(r.url.params["order"] == "id.asc" for r in requests)


async def test_select_all_single_short_page(monkeypatch):
    _env(monkeypatch)
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{BASE}/rest/v1/emendamenti").mock(
            return_value=httpx.Response(200, json=[{"id": f"r{i}"} for i in range(10)])
        )
        async with httpx.AsyncClient() as client:
            rows = await select_all(client, "emendamenti", "id")
    assert len(rows) == 10
    assert route.call_count == 1


async def test_upsert_chunks_and_prefer_header(monkeypatch):
    _env(monkeypatch)
    with respx.mock(assert_all_called=False) as router:
        route = router.post(f"{BASE}/rest/v1/emendamenti").mock(
            return_value=httpx.Response(201, json=[])
        )
        rows = [{"id": f"r{i}"} for i in range(1200)]
        async with httpx.AsyncClient() as client:
            out = await upsert(client, "emendamenti", rows, "id")
    assert out == []
    assert route.call_count == 3
    sizes = [len(json.loads(call.request.content)) for call in route.calls]
    assert sizes == [500, 500, 200]
    assert all(
        call.request.headers["Prefer"] == "resolution=merge-duplicates,return=minimal"
        for call in route.calls
    )
    assert all(call.request.url.params["on_conflict"] == "id" for call in route.calls)


async def test_upsert_ignore_duplicates_returns_rows(monkeypatch):
    _env(monkeypatch)
    payload = [{"id": 7, "emendamento_id": "B"}]
    with respx.mock(assert_all_called=False) as router:
        route = router.post(f"{BASE}/rest/v1/valutazioni").mock(
            return_value=httpx.Response(201, json=payload)
        )
        async with httpx.AsyncClient() as client:
            out = await upsert(
                client,
                "valutazioni",
                [{"emendamento_id": "A"}, {"emendamento_id": "B"}],
                "emendamento_id,rubrica_versione",
                ignore_duplicates=True,
                return_rows=True,
            )
    assert out == payload
    call = route.calls[0]
    assert call.request.headers["Prefer"] == "resolution=ignore-duplicates,return=representation"
    assert call.request.url.params["on_conflict"] == "emendamento_id,rubrica_versione"


async def test_rpc_posts_payload(monkeypatch):
    _env(monkeypatch)
    with respx.mock(assert_all_called=False) as router:
        route = router.post(f"{BASE}/rest/v1/rpc/ricalcola_versioni").mock(
            return_value=httpx.Response(200, json=3)
        )
        async with httpx.AsyncClient() as client:
            result = await rpc(client, "ricalcola_versioni", {"p_chiavi": ["a"]})
    assert result == 3
    assert json.loads(route.calls[0].request.content) == {"p_chiavi": ["a"]}
