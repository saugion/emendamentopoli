import httpx
import respx

from amendments_worker.sources.senato import save_senato_amendments, senato_key, senato_row
from amendments_worker.workflows.models import Amendment

from fakes import FakeSupabase


def _eme(n: int, **kw) -> Amendment:
    return Amendment(
        id=f"http://dati.senato.it/emendamento/{n}",
        legislature=19,
        number=kw.pop("number", f"1.{n}"),
        type=kw.pop("type", "E"),
        committee_seat=kw.pop("committee_seat", False),
        act_uri=kw.pop("act_uri", "http://dati.senato.it/oggettotrattazione/1521998"),
        text_url=f"http://www.senato.it/emend/{n}",
        text_xml_url=f"http://www.senato.it/emend/{n}.xml",
        **kw,
    )


def test_senato_key_matches_sql_normalization():
    assert (
        senato_key(
            "http://dati.senato.it/oggettotrattazione/1521998", "G", False, " G4.300 ", 19
        )
        == "senato:19:1521998:G:A:G4.300"
    )
    assert (
        senato_key(
            "http://dati.senato.it/oggettotrattazione/1521998", "G", True, "Coord.  1", 19
        )
        == "senato:19:1521998:G:C:COORD. 1"
    )


def test_senato_row_has_natural_key_and_version():
    row = senato_row(_eme(1205693, number="G4.300", type="G", text="Testo due"))
    assert row["chiave_naturale"] == "senato:19:1521998:G:A:G4.300"
    assert row["ordine_versione"] == 1205693
    assert row["camera"] == "senato"
    assert row["testo_hash"] is not None
    # base columns unchanged
    assert row["id"] == "http://dati.senato.it/emendamento/1205693"
    assert row["numero"] == "G4.300"


def test_senato_testo2_marks_previous_non_current():
    fake = FakeSupabase()
    key = "senato:19:1521998:G:A:G4.300"
    fake.emendamenti["http://dati.senato.it/emendamento/1205528"] = {
        "id": "http://dati.senato.it/emendamento/1205528",
        "chiave_naturale": key,
        "ordine_versione": 1205528,
        "data": "2026-11-20",
    }
    fake.emendamenti["http://dati.senato.it/emendamento/1205693"] = {
        "id": "http://dati.senato.it/emendamento/1205693",
        "chiave_naturale": key,
        "ordine_versione": 1205693,
        "data": "2026-11-18",
    }
    fake.rpc_ricalcola_versioni([key])
    v1 = fake.emendamenti["http://dati.senato.it/emendamento/1205528"]
    v2 = fake.emendamenti["http://dati.senato.it/emendamento/1205693"]
    assert v2["corrente"] is True and v2["versione"] == 2 and v2["superato_da"] is None
    assert v1["corrente"] is False and v1["versione"] == 1
    assert v1["superato_da"] == "http://dati.senato.it/emendamento/1205693"


def test_senato_three_versions_8_200():
    fake = FakeSupabase()
    key = "senato:19:2086:G:A:8.200"
    for n, data in ((1204752, "2026-11-19"), (1205279, "2026-11-18"), (1205281, "2026-11-17")):
        fake.emendamenti[f"http://dati.senato.it/emendamento/{n}"] = {
            "id": f"http://dati.senato.it/emendamento/{n}",
            "chiave_naturale": key,
            "ordine_versione": n,
            "data": data,
        }
    fake.rpc_ricalcola_versioni([key])
    # current = highest numeric URI id, never the earliest date
    assert fake.emendamenti["http://dati.senato.it/emendamento/1205281"]["corrente"] is True
    assert fake.emendamenti["http://dati.senato.it/emendamento/1204752"]["corrente"] is False
    assert (
        fake.emendamenti["http://dati.senato.it/emendamento/1204752"]["superato_da"]
        == "http://dati.senato.it/emendamento/1205279"
    )


async def test_save_senato_amendments_upserts_and_calls_rpc(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    with respx.mock(assert_all_called=False) as router:
        post_route = router.post(
            url__regex=r"https://example\.supabase\.co/rest/v1/(emendamenti|rpc/ricalcola_versioni)"
        ).mock(return_value=httpx.Response(201, json=[]))
        async with httpx.AsyncClient() as client:
            total = await save_senato_amendments(client, [_eme(1205693, number="G4.300", type="G", text="t")])
    assert total == 1
    bodies = [
        c.request.content for c in post_route.calls
    ]
    import json

    upsert_body = json.loads(bodies[0])
    assert upsert_body[0]["chiave_naturale"] == "senato:19:1521998:G:A:G4.300"
    assert upsert_body[0]["ordine_versione"] == 1205693
    assert upsert_body[0]["camera"] == "senato"
    rpc_body = json.loads(bodies[1])
    assert rpc_body == {"p_chiavi": ["senato:19:1521998:G:A:G4.300"]}


def test_camera_riformulazione_marks_previous_non_current():
    fake = FakeSupabase()
    key = "camera:19:odg:9/03076/003"
    fake.emendamenti["camera:19:odg:9/03076/003#v1"] = {
        "id": "camera:19:odg:9/03076/003#v1",
        "chiave_naturale": key,
        "ordine_versione": 1,
    }
    fake.emendamenti["camera:19:odg:9/03076/003#v2"] = {
        "id": "camera:19:odg:9/03076/003#v2",
        "chiave_naturale": key,
        "ordine_versione": 2,
    }
    fake.rpc_ricalcola_versioni([key])
    v1 = fake.emendamenti["camera:19:odg:9/03076/003#v1"]
    v2 = fake.emendamenti["camera:19:odg:9/03076/003#v2"]
    assert v2["corrente"] is True and v2["versione"] == 2 and v2["superato_da"] is None
    assert v1["corrente"] is False and v1["versione"] == 1
    assert v1["superato_da"] == "camera:19:odg:9/03076/003#v2"


async def test_resolve_parents_sparql_links_subemendamento_to_parent():
    from amendments_worker.sources.senato import resolve_parents_sparql

    child = _eme(1529264, number="2.0.1000/14", numero_padre="2.0.1000")
    other = _eme(1529265, number="2.14")  # no padre: no SPARQL call needed for it
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__startswith="https://dati.senato.it/sparql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": {
                        "bindings": [
                            {
                                "eme": {"value": "http://dati.senato.it/emendamento/1529000"},
                                "number": {"value": "2.0.1000"},
                            }
                        ]
                    }
                },
            )
        )
        resolved = await resolve_parents_sparql([child, other])
    assert resolved == 1
    assert child.emendamento_padre_id == "http://dati.senato.it/emendamento/1529000"
    assert other.emendamento_padre_id is None
    assert len(route.calls) == 1
    req = route.calls[0].request
    assert req.headers["User-Agent"] == "curl/8.5.0"
    assert "Authorization" not in req.headers  # no Supabase credential leaks to dati.senato.it
    from urllib.parse import unquote

    assert 'FILTER(?number IN ("2.0.1000"))' in unquote(str(req.url))


async def test_save_senato_amendments_resolves_parent_before_upsert(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    child = _eme(1529264, number="2.0.1000/14", numero_padre="2.0.1000")
    with respx.mock(assert_all_called=False) as router:
        router.get(url__startswith="https://dati.senato.it/sparql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": {
                        "bindings": [
                            {
                                "eme": {"value": "http://dati.senato.it/emendamento/1529000"},
                                "number": {"value": "2.0.1000"},
                            }
                        ]
                    }
                },
            )
        )
        post_route = router.post(
            url__regex=r"https://example\.supabase\.co/rest/v1/(emendamenti|rpc/ricalcola_versioni)"
        ).mock(return_value=httpx.Response(201, json=[]))
        async with httpx.AsyncClient() as client:
            total = await save_senato_amendments(client, [child])
    assert total == 1
    import json

    upsert_body = json.loads(post_route.calls[0].request.content)
    assert upsert_body[0]["numero_padre"] == "2.0.1000"
    assert upsert_body[0]["emendamento_padre_id"] == "http://dati.senato.it/emendamento/1529000"
