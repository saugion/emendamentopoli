import mistralai.workflows as workflows

from emendamenti_worker.workflows import valuta_mancanti as vm
from emendamenti_worker.workflows.valutazione import Punteggio, Valutazione

MAN: list[vm.Emendamento] = []
VALUTATI: list[Valutazione] = []


def _eme(n: int) -> vm.Emendamento:
    return vm.Emendamento(
        id=f"http://dati.senato.it/emendamento/{n}",
        legislatura=19,
        numero=f"1.{n}",
        tipo="E",
        sede_commissione=False,
        atto_uri="http://dati.senato.it/osr/oggetto/1",
        url_testo=f"http://www.senato.it/emend/{n}",
        url_testo_xml=f"http://www.senato.it/emend/{n}.xml",
        data="2026-11-20",
        ddl="Ddl 391",
        testo="testo",
    )


async def _senza_valutazione(limite: int) -> list[vm.Emendamento]:
    return [_eme(1), _eme(2)]


async def _valuta(eme: vm.Emendamento) -> Valutazione:
    if eme.id.endswith("/2"):
        raise RuntimeError("LLM non disponibile")
    return Valutazione(
        emendamento_id=eme.id,
        punteggi=[Punteggio(criterio="ambiente", punteggio=5, motivazione="x")],
        score_totale=5.0,
        riepilogo="r",
        modello="test",
        ts="2026-11-24T00:00:00+00:00",
    )


async def _salva(valutazioni: list[Valutazione]) -> int:
    VALUTATI.extend(valutazioni)
    return len(valutazioni)


async def test_backfill_mancanti(monkeypatch) -> None:
    VALUTATI.clear()
    monkeypatch.setattr(vm, "emendamenti_senza_valutazione", _senza_valutazione)
    monkeypatch.setattr(vm, "valuta_emendamento", _valuta)
    monkeypatch.setattr(vm, "salva_valutazioni", _salva)
    result = await workflows.execute_workflow(
        vm.ValutaEmendamentiMancantiWorkflow,
        params=vm.ValutaMancantiInput(limite=10),
    )
    d = result if isinstance(result, dict) else result.model_dump()
    assert d["trovati"] == 2
    assert d["valutati"] == 1
    assert d["ids"] == ["http://dati.senato.it/emendamento/1"]
    assert len(d["errori"]) == 1
    assert [v.emendamento_id for v in VALUTATI] == ["http://dati.senato.it/emendamento/1"]
