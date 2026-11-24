import mistralai.workflows as workflows

from emendamenti_worker.workflows import emendamenti_giornalieri as mod
from emendamenti_worker.workflows.valutazione import Punteggio, Valutazione

DATA = "2026-01-10"
SALVATI: list[mod.Emendamento] = []
VALUTATI: list[Valutazione] = []


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


async def _valuta(eme: mod.Emendamento) -> Valutazione:
    return Valutazione(
        emendamento_id=eme.id,
        punteggi=[Punteggio(criterio="salute", punteggio=8, motivazione="ok")],
        score_totale=8.0,
        riepilogo="riepilogo",
        modello="test",
        ts="2026-01-10T00:00:00+00:00",
    )


async def _salva_valutazioni(valutazioni: list[Valutazione]) -> int:
    VALUTATI.extend(valutazioni)
    return len(valutazioni)


def _patch(monkeypatch) -> None:
    monkeypatch.setattr(mod, "data_ieri", _data_ieri)
    monkeypatch.setattr(mod, "scarica_metadati_recenti", _scarica)
    monkeypatch.setattr(mod, "arricchisci_emendamento", _arricchisci)
    monkeypatch.setattr(mod, "salva_emendamenti", _salva)
    monkeypatch.setattr(mod, "valuta_emendamento", _valuta)
    monkeypatch.setattr(mod, "salva_valutazioni", _salva_valutazioni)


async def test_flusso_giornaliero(monkeypatch) -> None:
    SALVATI.clear()
    VALUTATI.clear()
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
    assert d["valutati"] == 1
    assert d["ids"] == ["http://dati.senato.it/emendamento/1"]
    assert len(d["errori"]) == 1
    assert d["errori_valutazione"] == []
    assert len(SALVATI) == 1
    assert [v.emendamento_id for v in VALUTATI] == ["http://dati.senato.it/emendamento/1"]


async def test_data_esplicita_e_nessun_risultato(monkeypatch) -> None:
    SALVATI.clear()
    VALUTATI.clear()
    _patch(monkeypatch)
    result = await workflows.execute_workflow(
        mod.EmendamentiGiornalieriWorkflow,
        params=mod.EmendamentiGiornalieriInput(data="2020-01-01", legislatura=19, limite=10),
    )
    d = result if isinstance(result, dict) else result.model_dump()
    assert d["data"] == "2020-01-01"
    assert d["trovati"] == 0
    assert d["salvati"] == 0
    assert d["valutati"] == 0
    assert SALVATI == []
    assert VALUTATI == []
