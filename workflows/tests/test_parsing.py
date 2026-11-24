from amendments_worker.workflows.daily_amendments import (
    Amendment,
    extract_date,
    extract_bill,
    extract_text,
    extract_signatories,
    senator_profile,
    query_recent_metadata,
    supabase_row,
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


def test_query_orders_by_id_desc_and_limits():
    q = query_recent_metadata(19, 500)
    assert "osr:legislatura 19" in q
    assert "ORDER BY DESC(?eme)" in q
    assert "LIMIT 500" in q


def test_extract_date_bill_text():
    assert extract_date(XML_AKN) == "2026-01-10"
    assert extract_bill(XML_AKN) == "Ddl 391"
    text = extract_text(XML_AKN)
    assert text is not None
    assert "articolo 3 & si aggiunge il comma 2." in text
    assert "<" not in text


def test_extract_missing_fields():
    assert extract_date("<an:bill/>") is None
    assert extract_bill("<an:bill/>") is None
    assert extract_text("<an:bill/>") is None


def test_supabase_row():
    a = Amendment(
        id="http://dati.senato.it/emendamento/1",
        legislature=19,
        number="1.30",
        type="E",
        committee_seat=True,
        act_uri="http://dati.senato.it/osr/oggetto/1",
        text_url="http://www.senato.it/emend/1",
        text_xml_url="http://www.senato.it/emend/1.xml",
        date="2026-01-10",
        bill="Ddl 391",
        text="text",
    )
    row = supabase_row(a)
    assert row["id"] == a.id
    assert row["sede_commissione"] is True
    assert row["data"] == "2026-01-10"
    assert row["firmatari"] is None
    assert row["firmatari_uri"] is None
    assert set(row) == {
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
        "firmatari",
        "firmatari_uri",
    }


def test_supabase_row_with_signatories():
    a = Amendment(
        id="http://dati.senato.it/emendamento/2",
        legislature=19,
        number="1.31",
        type="E",
        committee_seat=False,
        act_uri="http://dati.senato.it/osr/oggetto/1",
        text_url="http://www.senato.it/emend/2",
        text_xml_url="http://www.senato.it/emend/2.xml",
        signatories=["Rossi", "Bianchi"],
        signatory_uris=[
            "https://www.senato.it/leg/19/BGT/Schede/Attsen/00000407.htm",
            "",
        ],
    )
    row = supabase_row(a)
    assert row["firmatari"] == "Rossi, Bianchi"
    assert row["firmatari_uri"] == (
        "https://www.senato.it/leg/19/BGT/Schede/Attsen/00000407.htm, "
    )


XML_AKN_SIGNATORIES = """<?xml version="1.0" encoding="iso-8859-1"?>
<an:akomaNtoso>
  <an:bill>
    <an:preface>
      <an:docProponent refersTo="#p1"><an:person>ROSSI Mario</an:person></an:docProponent>
      <an:docProponent refersTo="#p2">BIANCHI Anna &amp; VERDI</an:docProponent>
      <an:docProponent refersTo="#gov">Governo</an:docProponent>
      <an:docProponent refersTo="#p1">ROSSI Mario</an:docProponent>
      <an:docProponent>NO_REF</an:docProponent>
    </an:preface>
    <an:references>
      <an:TLCPerson id="p1" href="http://dati.senato.it/senatore/407" />
      <an:TLCPerson id="p2" href="https://dati.senato.it/senatore/29521" />
      <an:TLCPerson id="gov" href="http://dati.senato.it/senatore/0" />
    </an:references>
  </an:bill>
</an:akomaNtoso>
"""


def test_extract_signatories():
    names, uris = extract_signatories(XML_AKN_SIGNATORIES, 19)
    # the duplicate proponent is skipped; the one without refersTo gets an empty uri
    assert names == ["ROSSI Mario", "BIANCHI Anna & VERDI", "Governo", "NO_REF"]
    assert uris == [
        "https://www.senato.it/leg/19/BGT/Schede/Attsen/00000407.htm",
        "https://www.senato.it/leg/19/BGT/Schede/Attsen/00029521.htm",
        "",  # senatore/0 = "Il Relatore": no fake profile link
        "",
    ]


def test_extract_signatories_empty():
    assert extract_signatories("<an:bill/>", 19) == ([], [])


def test_senator_profile():
    assert senator_profile("http://dati.senato.it/senatore/407", 19) == (
        "https://www.senato.it/leg/19/BGT/Schede/Attsen/00000407.htm"
    )
    assert senator_profile("https://dati.senato.it/29521", 18) == (
        "https://www.senato.it/leg/18/BGT/Schede/Attsen/00029521.htm"
    )
    assert senator_profile("http://dati.senato.it/senatore/0", 19) == ""
    assert senator_profile("http://dati.senato.it/senatore/000", 19) == ""
    assert senator_profile("http://example.com/x", 19) == "https://example.com/x"
