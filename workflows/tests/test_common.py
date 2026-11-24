import time

import httpx
import pytest
import respx

from amendments_worker.sources import common


@pytest.fixture(autouse=True)
def _no_sleeps(monkeypatch):
    monkeypatch.setattr(common, "CAMERA_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(common, "CAMERA_BACKOFF_S", (0.0, 0.0, 0.0, 0.0))


def test_testo_hash_normalization():
    assert common.testo_hash("La  Camera\u2019s\nTesto") == common.testo_hash("la camera's testo")
    assert common.testo_hash(None) is None
    assert common.testo_hash("") is None


def test_yyyymmdd_to_iso():
    assert common.yyyymmdd_to_iso("20261021") == "2026-10-21"
    assert common.yyyymmdd_to_iso("20230222-20230223") == "2023-02-22"
    assert common.yyyymmdd_to_iso("2026") is None


def test_fold():
    assert common.fold("Indennit\u00e0  E\u2019 ") == "indennita e'"


async def test_polite_get_retries_then_succeeds():
    with respx.mock(assert_all_called=False) as router:
        route = router.get("https://example.camera.it/x").mock(
            side_effect=[
                httpx.Response(429),
                httpx.Response(503),
                httpx.Response(200, text="ok"),
            ]
        )
        async with httpx.AsyncClient() as client:
            resp = await common.polite_get(client, "https://example.camera.it/x")
    assert resp.status_code == 200
    assert route.call_count == 3
    assert all(
        call.request.headers["User-Agent"] == common.CAMERA_USER_AGENT for call in route.calls
    )


async def test_polite_get_gives_up_after_5_attempts():
    with respx.mock(assert_all_called=False) as router:
        route = router.get("https://example.camera.it/x").mock(return_value=httpx.Response(503))
        with pytest.raises(RuntimeError, match="after 5 attempts"):
            async with httpx.AsyncClient() as client:
                await common.polite_get(client, "https://example.camera.it/x")
    assert route.call_count == 5


async def test_polite_get_304_is_success():
    with respx.mock(assert_all_called=False) as router:
        route = router.get("https://example.camera.it/x").mock(return_value=httpx.Response(304))
        async with httpx.AsyncClient() as client:
            resp = await common.polite_get(client, "https://example.camera.it/x")
    assert resp.status_code == 304
    assert route.call_count == 1


async def test_polite_get_404_raises_immediately():
    with respx.mock(assert_all_called=False) as router:
        route = router.get("https://example.camera.it/x").mock(return_value=httpx.Response(404))
        with pytest.raises(RuntimeError, match="-> 404"):
            async with httpx.AsyncClient() as client:
                await common.polite_get(client, "https://example.camera.it/x")
    assert route.call_count == 1


async def test_host_limiter_spacing(monkeypatch):
    monkeypatch.setattr(common, "CAMERA_MIN_INTERVAL_S", 0.05)
    limiter = common.HostLimiter()
    starts: list[float] = []
    for _ in range(3):
        await limiter.acquire("h")
        starts.append(time.monotonic())
    assert starts[1] - starts[0] >= 0.05
    assert starts[2] - starts[1] >= 0.05
