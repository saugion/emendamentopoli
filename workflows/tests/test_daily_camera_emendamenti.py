import json
from pathlib import Path

import httpx
import pytest
import respx

import mistralai.workflows as workflows
from amendments_worker.sources import common
from amendments_worker.workflows import daily_camera_emendamenti as mod
from amendments_worker.workflows.daily_camera_emendamenti import DailyCameraEmendamentiWorkflow

from fakes import FakeSupabase

FIX = Path(__file__).parent / "fixtures"

LIST_URL = (
    "https://documenti.camera.it/apps/emendamenti/getProposteEmendative.aspx"
    "?contenitorePortante=leg.19.eme.ac.3133&tipoSeduta=1&sedeEsame=referente"
    "&urnTestoRiferimento=urn:leg:19:3133:null:null:com:08:referente&tipoListaEmendamenti=1"
)
ATTO_PAGE = "https://www.camera.it/leg19/126?tab=1&leg=19&idDocumento=3133"
LAST_MODIFIED = "Thu, 12 Nov 2026 10:04:23 GMT"

ATTO_SNIPPET = (
    '<a href="' + LIST_URL.replace("&", "&amp;") + '">Emendamenti in VIII Commissione</a>'
)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(common, "CAMERA_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(common, "CAMERA_BACKOFF_S", (0.0, 0.0, 0.0, 0.0))


def _wire_db(monkeypatch, fake: FakeSupabase):
    async def _atti_for_discovery(cursor, limit):
        return []

    async def _upsert_liste(rows):
        fake.upsert("camera_liste_emendamenti", rows, "url")

    async def _lists_known():
        return len(fake.liste)

    async def _lists_to_check(atti, limit):
        if atti:
            return [u for u, r in fake.liste.items() if r.get("atto_numero") in atti][:limit]
        return list(fake.liste)[:limit]

    async def _list_state(url):
        return fake.liste.get(url)

    async def _list_touch(url):
        if url in fake.liste:
            fake.liste[url]["controllato_at"] = "now"

    async def _list_store(url, last_modified, righe):
        fake.liste.setdefault(url, {})["last_modified"] = last_modified
        fake.liste[url]["righe"] = righe

    async def _by_fonte(url):
        return {
            r["id"]: r for r in fake.emendamenti.values() if r.get("fonte_uri") == url
        }

    async def _save_rows(rows):
        fake.upsert_emendamenti(rows)
        fake.rpc_ricalcola_versioni(sorted({r["chiave_naturale"] for r in rows}))

    async def _atti_flags(legislature):
        return dict(fake.atti)

    monkeypatch.setattr(mod, "db_atti_for_discovery", _atti_for_discovery)
    monkeypatch.setattr(mod, "db_upsert_liste", _upsert_liste)
    monkeypatch.setattr(mod, "db_lists_known", _lists_known)
    monkeypatch.setattr(mod, "db_lists_to_check", _lists_to_check)
    monkeypatch.setattr(mod, "db_list_state", _list_state)
    monkeypatch.setattr(mod, "db_list_touch", _list_touch)
    monkeypatch.setattr(mod, "db_list_store_result", _list_store)
    monkeypatch.setattr(mod, "db_emendamenti_by_fonte", _by_fonte)
    monkeypatch.setattr(mod, "db_save_eme_rows", _save_rows)
    monkeypatch.setattr(mod, "db_atti_flags", _atti_flags)

    from amendments_worker.workflows.evaluation import EvalOutcome

    async def _evaluate_and_save(amendment_id: str) -> EvalOutcome:
        row = fake.emendamenti[amendment_id]
        if any(v["emendamento_id"] == amendment_id for v in fake.valutazioni.values()):
            return EvalOutcome(amendment_id=amendment_id, status="skipped", reason="already_evaluated")
        if not row.get("corrente") or row.get("rilevante_spesa") is not True or not row.get("testo"):
            return EvalOutcome(amendment_id=amendment_id, status="skipped", reason="not_relevant")
        vid = fake._next_valutazione_id
        fake._next_valutazione_id += 1
        fake.valutazioni[vid] = {
            "id": vid,
            "emendamento_id": amendment_id,
            "rubrica_versione": 2,
            "testo_hash": row.get("testo_hash"),
            "copiata_da": None,
        }
        return EvalOutcome(amendment_id=amendment_id, status="evaluated")

    monkeypatch.setattr(mod, "evaluate_and_save", _evaluate_and_save)


def _routes(router, list_responses):
    router.get(ATTO_PAGE).mock(
        return_value=httpx.Response(200, content=ATTO_SNIPPET.encode("utf-8"))
    )
    list_route = router.get(LIST_URL).mock(side_effect=list_responses)
    ass_detail = (FIX / "camera_eme_detail_3133_ass_1.7.html").read_bytes()
    com_detail = (FIX / "camera_eme_detail_3133_com_1.2.html").read_bytes()

    def detail_handler(request: httpx.Request) -> httpx.Response:
        pid = request.url.params.get("idPropostaEmendativa", "")
        if pid == "1.2.":
            return httpx.Response(200, content=com_detail)
        return httpx.Response(200, content=ass_detail.replace(b"1.7.", pid.encode("utf-8")))

    router.get(url__regex=r"getPropostaEmendativa\.aspx").mock(side_effect=detail_handler)
    return list_route


async def _run(monkeypatch, fake: FakeSupabase, list_responses):
    _wire_db(monkeypatch, fake)
    with respx.mock(assert_all_called=False) as router:
        _routes(router, list_responses)
        result = await workflows.execute_workflow(
            DailyCameraEmendamentiWorkflow,
            params=mod.DailyCameraEmendamentiInput(atti=["3133"], max_detail=5000),
        )
    return result if isinstance(result, dict) else result.model_dump()


def test_camera_emendamenti_workflow_is_discoverable():
    classes = workflows.discover_all_workflows_in_package("amendments_worker.workflows")
    assert DailyCameraEmendamentiWorkflow in classes


async def test_pilot_one_list_twice_idempotent(monkeypatch):
    fake = FakeSupabase()
    list_200 = httpx.Response(
        200,
        content=(FIX / "camera_eme_list_3133_com08_trimmed.html").read_bytes(),
        headers={"Last-Modified": LAST_MODIFIED},
    )
    d1 = await _run(monkeypatch, fake, [list_200])
    assert d1["atti_discovered"] == 0  # atti given explicitly: no cursor discovery
    assert d1["lists_known"] == 1
    assert d1["lists_checked"] == 1
    assert d1["details_fetched"] == 8  # 8 distinct proposals in the 12-row trimmed list
    assert d1["saved_rows"] == 9  # 1.2. has two versions
    sizes1 = (len(fake.emendamenti), len(fake.liste))
    # second run: the list answers 304 -> no detail requests, nothing changes
    d2 = await _run(monkeypatch, fake, [httpx.Response(304)])
    assert d2["lists_not_modified"] == 1
    assert d2["details_fetched"] == 0
    assert d2["saved_rows"] == 0
    assert (len(fake.emendamenti), len(fake.liste)) == sizes1
    # versions are computed: 1.2. has #v20261117 current and #v20261104 superseded
    v_new = fake.emendamenti["camera:19:em:AC3133:com08ref:1.2.#20261117"]
    v_old = fake.emendamenti["camera:19:em:AC3133:com08ref:1.2.#20261104"]
    assert v_new["corrente"] is True
    assert v_old["corrente"] is False
    assert v_old["superato_da"] == v_new["id"]


async def test_conditional_get_304_skips_list(monkeypatch):
    fake = FakeSupabase()
    fake.liste[LIST_URL] = {
        "url": LIST_URL,
        "legislatura": 19,
        "atto_numero": "3133",
        "sede": "com08ref",
        "last_modified": LAST_MODIFIED,
        "righe": 415,
    }

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(304)

    _wire_db(monkeypatch, fake)
    with respx.mock(assert_all_called=False) as router:
        router.get(LIST_URL).mock(side_effect=handler)
        router.get(url__regex=r"getPropostaEmendativa\.aspx").mock(
            return_value=httpx.Response(500)
        )
        result = await workflows.execute_workflow(
            DailyCameraEmendamentiWorkflow,
            params=mod.DailyCameraEmendamentiInput(atti=["3133"], max_atti=0, max_liste=10, max_detail=100),
        )
    d = result if isinstance(result, dict) else result.model_dump()
    assert d["lists_not_modified"] == 1
    assert d["details_fetched"] == 0
    assert captured and captured[0].headers.get("If-Modified-Since") == LAST_MODIFIED


async def test_discover_atto_lists_dedupes_urls_across_atti(monkeypatch):
    # the same list URL linked from two atto pages of one batch must be upserted
    # once: PostgREST 21000 "cannot affect row a second time" on duplicate keys
    captured: list[list[dict]] = []

    async def _upsert(rows):
        captured.append(rows)

    monkeypatch.setattr(mod, "db_upsert_liste", _upsert)
    page = ('<a href="' + LIST_URL.replace("&", "&amp;") + '">Emendamenti</a>').encode("utf-8")
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"www\.camera\.it/leg19/126").mock(
            return_value=httpx.Response(200, content=page)
        )
        result = await mod.discover_atto_lists(["3133", "3106"], 19)
    assert result.lists_found == 1
    assert len(captured) == 1
    assert [r["url"] for r in captured[0]] == [LIST_URL]
    assert captured[0][0]["atto_numero"] == "3133"
    assert captured[0][0]["sede"] == "com08ref"
