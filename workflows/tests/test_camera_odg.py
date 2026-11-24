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


