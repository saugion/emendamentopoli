import json
from pathlib import Path

import pytest

from amendments_worker.sources import common
from amendments_worker.sources.camera_odg import (
    camera_odg_rows,
    odg_atto,
    odg_outcome,
    parse_odg_bindings,
    parse_sicross,
    query_camera_odg,
)

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_sleeps(monkeypatch):
    monkeypatch.setattr(common, "CAMERA_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(common, "CAMERA_BACKOFF_S", (0.0, 0.0, 0.0, 0.0))


def _fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _item(identifier: str | None = None):
    data = json.loads((FIX / "camera_odg_sparql_page.json").read_text(encoding="utf-8"))
    items = parse_odg_bindings(data["results"]["bindings"])
    item = items[0]
    if identifier is not None:
        item = item.model_copy(update={"identifier": identifier})
    return item


def test_query_has_ramo_filter_s_exclusion_and_distinct():
    q = query_camera_odg(19, "2026-11-24T00:00:00Z")
    assert 'ocd:ramo "Camera dei Deputati"' in q
    assert 'FILTER(!STRENDS(STR(?aic), "_S"))' in q
    assert q.startswith("PREFIX")
    assert "SELECT DISTINCT ?aic" in q
    assert "GROUP_CONCAT(DISTINCT" in q
    assert '"2026-11-24T00:00:00Z"^^xsd:dateTime' in q
    assert "OFFSET" not in q
    assert "LIMIT 1000" in q
    assert "ORDER BY ?modified ?aic" in q
    assert "repubblica_19" in q


def test_query_backfill_window():
    q = query_camera_odg(19, "2000-01-01T00:00:00Z", "20240701", "20240731")
    assert 'SUBSTR(STR(?data), 1, 8) >= "20240701" && SUBSTR(STR(?data), 1, 8) <= "20240731"' in q


def test_duplicate_bindings_one_item():
    data = json.loads((FIX / "camera_odg_sparql_page.json").read_text(encoding="utf-8"))
    bindings = data["results"]["bindings"]
    bindings.append(dict(bindings[0]))
    items = parse_odg_bindings(bindings)
    assert len(items) == 11
    assert items[0].identifier == "9/00362-A/009"
    assert len(items[0].altri) == 3
    assert items[0].subjects


def test_parse_sicross_riformulazione_two_versions():
    page = parse_sicross(_fixture("camera_sicross_9_03076_003.html"))
    assert page is not None
    assert len(page.versions) == 2
    v1, v2 = page.versions
    assert len(v1.testo) == 2042
    assert len(v2.testo) == 2107
    assert "compatibilmente con i vincoli di finanza pubblica" in v2.testo
    assert "compatibilmente con i vincoli di finanza pubblica" not in v1.testo
    assert "Tucci" not in v1.testo and "Tucci" not in v2.testo
    assert "9/3076" not in v1.testo and "9/3076" not in v2.testo
    assert v2.etichetta == "Testo Allegato A del 21 Ottobre 2026 (Testo modificato nel corso della seduta)"
    assert common.testo_hash(v1.testo) == "72f06cc3f74da88f6cfaae310f76a61e1f451c6bd6f942360c011cdac824634a"
    assert common.testo_hash(v2.testo) == "6ea8f18889a0770d81ceeedfb3b5536533aa6b9c6f2a7fc077da1ad9a7e96d7b"


def test_parse_sicross_signatories_order_and_no_government():
    page = parse_sicross(_fixture("camera_sicross_9_03076_003.html"))
    assert page is not None
    ids = [p.id_persona for p in page.persons]
    assert ids == ["307726", "307394", "308846", "307435", "308844", "308841", "308845", "308818"]
    assert page.persons[0].nome == "TUCCI RICCARDO"
    assert "301573" not in ids  # the Government's sottosegretario in div.iter must not appear


@pytest.mark.parametrize(
    "fixture,expected",
    [
        ("camera_sicross_9_03076_003.html", ("accolto", "accolto")),
        ("camera_sicross_9_03076_001.html", ("respinto", "non_accolto")),
        ("camera_sicross_9_03076_011.html", ("respinto", "non_accolto")),
        ("camera_sicross_9_02097-A_005.html", ("accolto_come_raccomandazione", "accolto_come_raccomandazione")),
        ("camera_sicross_9_01038-A_007.html", ("accolto", "accolto")),
        ("camera_sicross_0_01956_001_07.html", ("approvato", "accolto")),
        ("camera_sicross_9_DOC_VIII_N_4_067.html", ("approvato", "accolto")),
        ("camera_sicross_9_DOC_018-BIS_N_52_001.html", ("in_corso", None)),
    ],
)
def test_parse_sicross_outcomes(fixture, expected):
    page = parse_sicross(_fixture(fixture))
    assert page is not None
    assert odg_outcome(page.fasi, page.stato_iter) == expected


def test_parse_sicross_ritirato_synthetic():
    raw = _fixture("camera_sicross_9_03076_001.html")
    page = parse_sicross(raw.replace("<p>RESPINTO IL 21/10/2026</p>", "<p>RITIRATO IL 21/10/2026</p>"))
    assert page is not None
    esito, _ = odg_outcome(page.fasi, page.stato_iter)
    assert esito == "ritirato"


def test_unknown_fase_is_logged_not_raised(caplog):
    raw = _fixture("camera_sicross_9_03076_001.html").replace("CONCLUSO", "ARCHIVIATO")
    page = parse_sicross(raw)
    assert page is not None
    with caplog.at_level("WARNING"):
        odg_outcome(page.fasi, page.stato_iter)
    assert "unknown fase" in caplog.text


def test_parse_sicross_commissione():
    page = parse_sicross(_fixture("camera_sicross_0_01956_001_07.html"))
    assert page is not None
    assert page.commissione == ("3507", "VII COMMISSIONE (CULTURA, SCIENZA E ISTRUZIONE)")
    assert [p.id_persona for p in page.persons] == ["307424"]
    assert page.versions[0].testo.startswith("La VII Commissione,")


def test_parse_sicross_not_found():
    assert parse_sicross(_fixture("camera_sicross_not_found.html")) is None


def test_parse_sicross_in_corso():
    page = parse_sicross(_fixture("camera_sicross_9_DOC_018-BIS_N_52_001.html"))
    assert page is not None
    assert page.fasi == []
    assert page.stato_iter == "IN CORSO"
    assert odg_outcome(page.fasi, page.stato_iter) == ("in_corso", None)


def test_odg_atto_mapping():
    assert odg_atto("9/01038-A/007") == (
        "http://dati.camera.it/ocd/attocamera.rdf/ac19_1038",
        "C. 1038",
        "A",
    )
    assert odg_atto("9/01532-bis-A/081") == (
        "http://dati.camera.it/ocd/attocamera.rdf/ac19_1532-bis",
        "C. 1532-bis",
        "A",
    )
    assert odg_atto("9/00536-B/008") == (
        "http://dati.camera.it/ocd/attocamera.rdf/ac19_536",
        "C. 536",
        "B",
    )
    assert odg_atto("0/01956/001/07") == (
        "http://dati.camera.it/ocd/attocamera.rdf/ac19_1956",
        "C. 1956",
        None,
    )
    assert odg_atto("9/DOC.VIII,N.4/067") == (None, "Doc. VIII, n. 4", None)
    assert odg_atto("9/DOC.018-BIS,N.52/001") == (None, "Doc. 018-BIS, n. 52", None)


def test_profile_and_ocd_uris():
    item = _item("9/03076/003")
    page = parse_sicross(_fixture("camera_sicross_9_03076_003.html"))
    rows = camera_odg_rows(item, page)
    assert rows[0]["firmatari_uri"].split(", ")[0] == "https://www.camera.it/deputati/elenco/19-307726"
    assert rows[0]["firmatari_ocd"].split(", ")[0] == "http://dati.camera.it/ocd/deputato.rdf/d307726_19"


def test_rows_keys_and_versions():
    item = _item("9/03076/003")
    page = parse_sicross(_fixture("camera_sicross_9_03076_003.html"))
    rows = camera_odg_rows(item, page)
    assert [r["id"] for r in rows] == [
        "camera:19:odg:9/03076/003#v1",
        "camera:19:odg:9/03076/003#v2",
    ]
    assert [r["ordine_versione"] for r in rows] == [1, 2]
    assert rows[0]["chiave_naturale"] == rows[1]["chiave_naturale"] == "camera:19:odg:9/03076/003"
    for r in rows:
        assert "corrente" not in r and "versione" not in r and "superato_da" not in r
    assert rows[0]["data"] == "2026-10-21"
    assert rows[0]["url_testo"].startswith("https://")


def test_not_found_row_keeps_metadata():
    item = _item("9/03076/003")
    rows = camera_odg_rows(item, None)
    assert len(rows) == 1
    assert rows[0]["id"] == "camera:19:odg:9/03076/003#v1"
    assert rows[0]["testo"] is None
    assert rows[0]["metadati_hash"] is None
