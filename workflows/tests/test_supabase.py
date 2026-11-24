import asyncio
import json

import httpx
import pytest

from amendments_worker.workflows import supabase
from amendments_worker.workflows.evaluation import Evaluation, Score
from amendments_worker.workflows.models import Amendment


class FakeResp:
    def __init__(self, payload=None, status=200):
        self.status_code = status
        self.text = "" if payload is None else json.dumps(payload)
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)


class FakeClient:
    def __init__(self, calls, responses, timeout=None, headers=None):
        self._calls = calls
        self._responses = responses
        self.headers = headers

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        self._calls.append(("GET", url, self.headers, None))
        return self._responses.pop(0)

    async def post(self, url, json=None):
        self._calls.append(("POST", url, self.headers, json))
        return self._responses.pop(0)


@pytest.fixture
def transport(monkeypatch):
    calls: list = []
    responses: list = []

    def factory(timeout=None, headers=None):
        return FakeClient(calls, responses, timeout=timeout, headers=headers)

    monkeypatch.setattr(supabase.httpx, "AsyncClient", factory)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co/")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    return calls, responses


def _eme(n: int) -> Amendment:
    return Amendment(
        id=f"http://dati.senato.it/emendamento/{n}",
        legislature=19,
        number=f"1.{n}",
        type="E",
        committee_seat=False,
        act_uri="http://dati.senato.it/osr/oggetto/1",
        text_url=f"http://www.senato.it/emend/{n}",
        text_xml_url=f"http://www.senato.it/emend/{n}.xml",
    )


def _evaluation() -> Evaluation:
    return Evaluation(
        amendment_id="http://dati.senato.it/emendamento/1",
        scores=[
            Score(criterion="salute", score=7, rationale="x"),
            Score(criterion="ambiente", score=3, rationale="y"),
        ],
        total_score=5.0,
        summary="r",
        model="m",
        ts="2026-01-10T00:00:00+00:00",
    )


async def test_saved_amendments_map_url_headers_and_mapping(transport):
    calls, responses = transport
    responses.append(FakeResp([{"id": "x", "data": "2026-01-10", "firmatari": "Rossi"}]))
    result = await supabase.saved_amendments_map(19)
    method, url, headers, _ = calls[0]
    assert method == "GET"
    assert url == (
        "https://example.supabase.co/rest/v1/emendamenti"
        "?select=id,data,firmatari&legislatura=eq.19&limit=100000"
    )
    assert headers["apikey"] == "test-key"
    assert headers["Authorization"] == "Bearer test-key"
    assert result == {"x": {"data": "2026-01-10", "firmatari": "Rossi"}}


async def test_upsert_amendments_chunks_with_prefer(transport, monkeypatch):
    calls, responses = transport
    monkeypatch.setattr(supabase, "SAVE_CHUNK", 2)
    responses.extend([FakeResp(None), FakeResp(None)])
    total = await supabase.upsert_amendments([_eme(1), _eme(2), _eme(3)])
    assert total == 3
    assert len(calls) == 2
    first, second = calls
    assert first[1].endswith("/rest/v1/emendamenti?on_conflict=id")
    assert first[2]["Prefer"] == "resolution=merge-duplicates"
    assert len(first[3]) == 2 and len(second[3]) == 1
    assert first[3][0]["id"] == "http://dati.senato.it/emendamento/1"


async def test_insert_evaluations_posts_valutazioni_then_punteggi(transport):
    calls, responses = transport
    responses.extend([FakeResp([{"id": 42}]), FakeResp(None)])
    total = await supabase.insert_evaluations([_evaluation()])
    assert total == 1
    val_call, score_call = calls
    assert val_call[1].endswith("/rest/v1/valutazioni")
    assert val_call[2]["Prefer"] == "return=representation"
    assert val_call[3][0]["emendamento_id"] == "http://dati.senato.it/emendamento/1"
    assert score_call[1].endswith("/rest/v1/punteggi")
    assert "Prefer" not in score_call[2]
    assert score_call[3] == [
        {"valutazione_id": 42, "criterio": "salute", "punteggio": 7, "motivazione": "x"},
        {"valutazione_id": 42, "criterio": "ambiente", "punteggio": 3, "motivazione": "y"},
    ]


async def test_amendments_without_evaluation_uses_aliases(transport):
    calls, responses = transport
    responses.append(
        FakeResp(
            [
                {
                    "id": "x",
                    "legislature": 19,
                    "number": "1.1",
                    "type": "E",
                    "committee_seat": False,
                    "act_uri": "http://dati.senato.it/osr/oggetto/1",
                    "bill": "Ddl 391",
                    "text_url": "u",
                    "text_xml_url": "ux",
                    "date": "2026-01-10",
                    "text": "t",
                }
            ]
        )
    )
    result = await supabase.amendments_without_evaluation(500)
    _, url, _, _ = calls[0]
    assert "legislature:legislatura" in url
    assert "valutazioni.id=is.null" in url
    assert "limit=500" in url
    assert result[0].id == "x" and result[0].legislature == 19 and result[0].bill == "Ddl 391"


async def test_get_retries_on_retryable_status(transport, monkeypatch):
    calls, responses = transport

    async def _nosleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    responses.extend([FakeResp(status=403), FakeResp([{"id": "x", "data": None, "firmatari": None}])])
    result = await supabase.saved_amendments_map(19)
    assert len(calls) == 2
    assert result == {"x": {"data": None, "firmatari": None}}
