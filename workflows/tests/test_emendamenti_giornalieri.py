import mistralai.workflows as workflows

from emendamenti_worker.workflows import emendamenti_giornalieri as mod

DATA = "2026-01-10"
SALVATI: list[mod.Emendamento] = []


def _eme(n: int) -> mod.Emendamento:
    return mod.Emendamento(
        id=f"http://dati.senato.it/emendamento/{n}",
        legislatura=19,
        numero=f"1.{n}",
        tipo="E",
        sede_commissione=False,
        atto_uri="http://dati.senato.it/osr/oggetto/1",
        url_testo=f"http://www.senato.it/emend/{n}",
        url_testo_xml=f"http://www.senato.it/emend/{n}.xml",
    )


async def _data_ieri() -> str:
    return DATA


async def _scarica(legislatura: int, limite: int) -> list[mod.Emendamento]:
    return [_eme(1), _eme(2), _eme(3)]


async def _arricchisci(eme: mod.Emendamento) -> mod.Emendamento:
    if eme.id.endswith("/2"):
        raise RuntimeError("AKN non raggiungibile")
    eme.data = DATA if eme.id.endswith("/1") else "2026-01-09"
    eme.ddl = "Ddl 391"
    eme.testo = "testo"
    return eme


async def _salva(emendamenti: list[mod.Emendamento]) -> int:
    SALVATI.extend(emendamenti)
    return len(emendamenti)


def _patch(monkeypatch) -> None:
    monkeypatch.setattr(mod, "data_ieri", _data_ieri)
    monkeypatch.setattr(mod, "scarica_metadati_recenti", _scarica)
    monkeypatch.setattr(mod, "arricchisci_emendamento", _arricchisci)
    monkeypatch.setattr(mod, "salva_emendamenti", _salva)


async def test_flusso_giornaliero(monkeypatch) -> None:
    SALVATI.clear()
    _patch(monkeypatch)
    result = await workflows.execute_workflow(
        mod.EmendamentiGiornalieriWorkflow,
        params=mod.EmendamentiGiornalieriInput(legislatura=19, limite=10),
    )
    d = result if isinstance(result, dict) else result.model_dump()
    assert d["data"] == DATA
    assert d["metadati_scaricati"] == 3
    assert d["analizzati"] == 2
    assert d["trovati"] == 1
    assert d["salvati"] == 1
    assert d["ids"] == ["http://dati.senato.it/emendamento/1"]
    assert len(d["errori"]) == 1
    assert len(SALVATI) == 1


async def test_data_esplicita_e_nessun_risultato(monkeypatch) -> None:
    SALVATI.clear()
    _patch(monkeypatch)
    result = await workflows.execute_workflow(
        mod.EmendamentiGiornalieriWorkflow,
        params=mod.EmendamentiGiornalieriInput(data="2020-01-01", legislatura=19, limite=10),
    )
    d = result if isinstance(result, dict) else result.model_dump()
    assert d["data"] == "2020-01-01"
    assert d["trovati"] == 0
    assert d["salvati"] == 0
    assert SALVATI == []
