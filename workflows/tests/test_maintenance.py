import mistralai.workflows as workflows
from amendments_worker.workflows import maintenance as mod
from amendments_worker.workflows.maintenance import (
    MaintenanceWorkflow,
    compute_maintenance_updates,
    row_atto_flags,
)


def test_maintenance_workflow_is_discoverable():
    classes = workflows.discover_all_workflows_in_package("amendments_worker.workflows")
    assert MaintenanceWorkflow in classes


def test_compute_maintenance_updates_senato_level_b_only():
    rows = [
        {
            "id": "http://dati.senato.it/emendamento/1",
            "legislatura": 19,
            "camera": "senato",
            "tipo": "E",
            "numero": "1.1",
            "atto_uri": "http://dati.senato.it/oggettotrattazione/1",
            "testo": "Stanzia 5 milioni di euro",
            "testo_hash": None,
            "rilevante_spesa": None,
            "rilevanza_motivo": [],
        },
        {
            "id": "http://dati.senato.it/emendamento/2",
            "legislatura": 19,
            "camera": "senato",
            "tipo": "E",
            "numero": "1.2",
            "atto_uri": "http://dati.senato.it/oggettotrattazione/1",
            "testo": "a istituire un tavolo tecnico",
            "testo_hash": None,
            "rilevante_spesa": None,
            "rilevanza_motivo": [],
        },
    ]
    updates = compute_maintenance_updates(rows, {}, "R1")
    assert updates[0]["rilevante_spesa"] is True
    assert updates[0]["rilevanza_motivo"] == ["testo_spesa_forte"]
    assert updates[0]["testo_hash"] is not None
    assert updates[1]["rilevante_spesa"] is False
    assert updates[1]["rilevanza_motivo"] == []


def test_row_atto_flags_camera_odg():
    atti = {
        "http://dati.camera.it/ocd/attocamera.rdf/ac19_3133": {
            "uri": "http://dati.camera.it/ocd/attocamera.rdf/ac19_3133",
            "is_dl_conversione": True,
            "is_bilancio": False,
            "is_commissione_bilancio_referente": False,
        }
    }
    row = {
        "camera": "camera",
        "atto_uri": "http://dati.camera.it/ocd/attocamera.rdf/ac19_3133",
        "numero": "9/03133/001",
        "rilevanza_motivo": [],
    }
    flags = row_atto_flags(row, atti)
    assert flags == {"atto_dl": True}
    # DOC.VIII internal budget ODG: flag from the identifier even without stored motivo
    row2 = {"camera": "camera", "atto_uri": None, "numero": "9/DOC.VIII,N.4/067", "rilevanza_motivo": []}
    assert row_atto_flags(row2, atti) == {"atto_bilancio_interno": True}
    # kept from the stored motivo (computed at ingest from dc:title, not stored)
    row3 = {"camera": "camera", "atto_uri": None, "numero": "9/03076/003", "rilevanza_motivo": ["atto_bilancio_interno"]}
    assert row_atto_flags(row3, atti) == {"atto_bilancio_interno": True}


async def test_workflow_pages_until_short_page(monkeypatch):
    pages = [
        [{"id": f"r{i:05d}", "legislatura": 19, "camera": "senato", "tipo": "E", "numero": "1.1",
          "atto_uri": None, "testo": "stanziamento di euro", "testo_hash": None,
          "rilevante_spesa": None, "rilevanza_motivo": []} for i in range(1000)],
        [{"id": "r99999", "legislatura": 19, "camera": "senato", "tipo": "E", "numero": "1.2",
          "atto_uri": None, "testo": "tavolo tecnico", "testo_hash": None,
          "rilevante_spesa": None, "rilevanza_motivo": []}],
    ]
    written: list[dict] = []
    calls = {"i": 0}

    async def _read(after_id, only_missing):
        page = pages[min(calls["i"], len(pages) - 1)]
        calls["i"] += 1
        return page

    async def _atti():
        return {}

    async def _write(rows):
        written.extend(rows)

    monkeypatch.setattr(mod, "db_maintenance_read", _read)
    monkeypatch.setattr(mod, "db_atti_flags_all", _atti)
    monkeypatch.setattr(mod, "db_maintenance_write", _write)
    result = await workflows.execute_workflow(
        MaintenanceWorkflow, params=mod.MaintenanceInput(only_missing=True, regola="R1")
    )
    d = result if isinstance(result, dict) else result.model_dump()
    assert d["read"] == 1001
    assert d["updated"] == 1001
    assert d["errors"] == []
    assert len(written) == 1001
    assert written[0]["rilevante_spesa"] is True
    assert written[-1]["rilevante_spesa"] is False
