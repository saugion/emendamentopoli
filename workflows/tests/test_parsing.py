from emendamenti_worker.workflows.emendamenti_giornalieri import (
    Emendamento,
    estrai_data,
    estrai_ddl,
    estrai_testo,
    query_metadati_recenti,
    riga_supabase,
)

XML_AKN = """<?xml version="1.0" encoding="iso-8859-1"?>
<an:akomaNtoso>
  <an:bill>
    <an:meta><an:identification>
      <an:FRBRdate date="2026-01-10" name="presentazione" />
    </an:identification></an:meta>
    <an:preface><an:activeRef showAs="Ddl 391">http://dati.senato.it/.../391</an:activeRef></an:preface>
    <an:amendmentBody>
      <an:mod><an:quotedText>Si sostituisce l&#x2019;articolo 3 &amp; si aggiunge il comma 2.</an:quotedText></an:mod>
    </an:amendmentBody>
  </an:bill>
</an:akomaNtoso>
"""


def test_query_ordina_per_id_decrescente_e_limita():
    q = query_metadati_recenti(19, 500)
    assert "osr:legislatura 19" in q
    assert 'ORDER BY DESC(xsd:integer(STRAFTER(STR(?eme), "emendamento/")))' in q
    assert "LIMIT 500" in q


def test_estrai_data_ddl_testo():
    assert estrai_data(XML_AKN) == "2026-01-10"
    assert estrai_ddl(XML_AKN) == "Ddl 391"
    testo = estrai_testo(XML_AKN)
    assert testo is not None
    assert "articolo 3 & si aggiunge il comma 2." in testo
    assert "<" not in testo


def test_estrai_campi_mancanti():
    assert estrai_data("<an:bill/>") is None
    assert estrai_ddl("<an:bill/>") is None
    assert estrai_testo("<an:bill/>") is None


def test_riga_supabase():
    e = Emendamento(
        id="http://dati.senato.it/emendamento/1",
        legislatura=19,
        numero="1.30",
        tipo="E",
        sede_commissione=True,
        atto_uri="http://dati.senato.it/osr/oggetto/1",
        url_testo="http://www.senato.it/emend/1",
        url_testo_xml="http://www.senato.it/emend/1.xml",
        data="2026-01-10",
        ddl="Ddl 391",
        testo="testo",
    )
    riga = riga_supabase(e)
    assert riga["id"] == e.id
    assert riga["sede_commissione"] is True
    assert riga["data"] == "2026-01-10"
    assert set(riga) == {
        "id",
        "legislatura",
        "numero",
        "tipo",
        "sede_commissione",
        "atto_uri",
        "ddl",
        "url_testo",
        "url_testo_xml",
        "data",
        "testo",
    }
