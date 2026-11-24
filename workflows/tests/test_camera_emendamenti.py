import logging
from pathlib import Path

from amendments_worker.sources.camera_emendamenti import (
    DetailPage,
    DetailVersion,
    eme_rows,
    extract_list_urls,
    list_url_parts,
    parse_detail,
    parse_list,
)

FIX = Path(__file__).parent / "fixtures"

LIST_3133 = (
    "https://documenti.camera.it/apps/emendamenti/getProposteEmendative.aspx"
    "?contenitorePortante=leg.19.eme.ac.3133&tipoSeduta=0&sedeEsame=null"
    "&urnTestoRiferimento=urn:leg:19:3133:null:A:ass:null:null&tipoListaEmendamenti=1"
)
LIST_3133_COM = (
    "https://documenti.camera.it/apps/emendamenti/getProposteEmendative.aspx"
    "?contenitorePortante=leg.19.eme.ac.3133&tipoSeduta=1&sedeEsame=referente"
    "&urnTestoRiferimento=urn:leg:19:3133:null:null:com:08:referente&tipoListaEmendamenti=1"
)


def _fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8-sig")


def test_extract_list_links():
    html_snippet = (
        f'<a href="{LIST_3133.replace("&", "&amp;")}">Assemblea</a>'
        f'<a href="{LIST_3133_COM.replace("&", "&amp;")}">Commissione</a>'
        '<a href="https://documenti.camera.it/apps/emendamenti/getProposteEmendativeSeduta.aspx?x=1">seduta</a>'
    )
    urls = extract_list_urls(html_snippet)
    assert urls == sorted([LIST_3133, LIST_3133_COM])


def test_sede_token():
    assert list_url_parts(LIST_3133) == ("3133", "ass")
    assert list_url_parts(LIST_3133_COM) == ("3133", "com08ref")
    com06 = LIST_3133_COM.replace("com:08:referente", "com:06:referente")
    assert list_url_parts(com06) == ("3133", "com06ref")


def test_parse_list_trimmed_commission():
    rows = parse_list(_fixture("camera_eme_list_3133_com08_trimmed.html"))
    assert len(rows) == 12
    assert [(r.id, r.position, r.esito) for r in rows if r.esito] == [
        ("1.25.", "20261104", "inammissibile"),
        ("1.2.", "20261117", "approvato"),
        ("1.3.", "20261117", "approvato"),
        ("1.4.", "20261117", "approvato"),
        ("4.104.", "20261117", "approvato"),
    ]
    by_id_pos = {(r.id, r.position): r for r in rows}
    assert by_id_pos[("1.2.", "20261104")].identici == ["1.3.", "1.4."]
    assert by_id_pos[("4.104.", "20261117")].identici == ["4.105.", "4.106."]
    first = rows[0]
    assert first.primo_id_persona == "307459"
    assert first.primo_nome == "Fontana Ilaria"


def test_parse_list_trimmed_assemblea():
    rows = parse_list(_fixture("camera_eme_list_3133_ass_trimmed.html"))
    assert len(rows) == 8
    by_id = {r.id: r for r in rows}
    assert by_id["2.300."].primo_id_persona is None  # plain text, no link
    assert by_id["1.015."].esito == "inammissibile"
    assert by_id["1.7."].identici == ["1.8."]


def test_parse_list_empty():
    assert parse_list(_fixture("camera_eme_list_empty_3151_ass.html")) == []


def test_parse_detail_single():
    d = parse_detail((FIX / "camera_eme_detail_3133_ass_1.7.html").read_text(encoding="utf-8"))
    assert (d.header_numero, d.header_sede, d.header_testo) == ("1.7.", "Assemblea", "3133-A")
    assert len(d.versioni) == 1
    v = d.versioni[0]
    assert v.data == "20261118"
    assert v.esito is None
    assert v.proponenti == [("307822", "L'Abbate Patty")]
    assert v.identici == ["1.8."]
    assert v.testo.startswith("Al comma 1, lettera a), dopo il numero 1.2), aggiungere il seguente:")


def test_parse_detail_two_versions():
    d = parse_detail((FIX / "camera_eme_detail_3133_com_1.2.html").read_text(encoding="utf-8"))
    assert (d.header_numero, d.header_sede, d.header_testo) == ("1.2.", "VIII Commissione in sede referente", "3133")
    assert [v.data for v in d.versioni] == ["20261104", "20261117"]
    old, new = d.versioni
    assert old.corrente_html is False and old.nuova_formulazione is False and old.esito is None
    assert len(old.proponenti) == 3 and old.identici == ["1.3.", "1.4."]
    assert old.testo.startswith("Al comma 1, alla lettera a), premettere la seguente:")
    assert new.corrente_html is True and new.nuova_formulazione is True and new.esito == "approvato"
    assert len(new.proponenti) == 4 and new.proponenti[0][0] == "307892"
    assert new.identici == ["1.3.", "1.4."]
    assert new.testo.startswith("Dopo il comma 1 inserire i seguenti:")


def test_esito_mapping_unknown_class_logged(caplog):
    page = _fixture("camera_eme_list_3133_ass_trimmed.html")
    broken = page.replace('esitoInammissibile', 'esitoSconosciuto', 1)
    with caplog.at_level(logging.WARNING):
        rows = parse_list(broken)
    assert any(r.esito is None for r in rows)
    assert "unknown esito class" in caplog.text


def test_identici_group():
    d = parse_detail((FIX / "camera_eme_detail_3133_com_1.2.html").read_text(encoding="utf-8"))
    # list rows for 1.2./1.3./1.4. share the same identici group
    rows = parse_list(_fixture("camera_eme_list_3133_com08_trimmed.html"))
    by_id = {r.id: r for r in rows}

    class _LR:
        pass

    groups = set()
    for pid in ("1.2.", "1.3.", "1.4."):
        lr = by_id[pid]
        for r in eme_rows("3133", "com08ref", lr, d, LIST_3133_COM):
            if r["numero"] == "1.2.":
                groups.add(r["gruppo_identici"])
    assert groups == {"camera:19:em:AC3133:com08ref:ident:1.2.|1.3.|1.4."}


def test_rows_keys():
    rows = parse_list(_fixture("camera_eme_list_3133_com08_trimmed.html"))
    lr = next(r for r in rows if r.id == "1.2.")
    d = parse_detail((FIX / "camera_eme_detail_3133_com_1.2.html").read_text(encoding="utf-8"))
    out = eme_rows("3133", "com08ref", lr, d, LIST_3133_COM)
    assert [r["id"] for r in out] == [
        "camera:19:em:AC3133:com08ref:1.2.#20261104",
        "camera:19:em:AC3133:com08ref:1.2.#20261117",
    ]
    assert [r["ordine_versione"] for r in out] == [20261104, 20261117]
    assert out[0]["chiave_naturale"] == "camera:19:em:AC3133:com08ref:1.2."
    assert out[1]["esito"] == "approvato"
    assert out[1]["versione_etichetta"] == "nuova formulazione"
    assert out[0]["versione_etichetta"] is None
