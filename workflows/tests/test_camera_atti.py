from amendments_worker.sources.camera_atti import (
    atti_flags,
    clean_title,
    query_camera_atti,
)


def test_clean_title():
    raw_3174 = ' "Bilancio di previsione dello Stato per l&rsquo;anno finanziario 2027 e bilancio pluriennale per il triennio 2027-2029" (3174) '
    assert clean_title(raw_3174) == (
        '"Bilancio di previsione dello Stato per l\'anno finanziario 2027 '
        'e bilancio pluriennale per il triennio 2027-2029"'
    )
    raw_1627 = ' S. 926. - &quot;Bilancio di previsione dello Stato per l&rsquo;anno finanziario 2024 e bilancio pluriennale per il triennio 2024-2026&quot;&nbsp;&lt;em&gt;(approvato dal Senato)&lt;/em&gt; (1627) '
    cleaned = clean_title(raw_1627)
    assert cleaned.startswith('S. 926. - "Bilancio di previsione')
    assert cleaned.endswith("(approvato dal Senato)")


def test_flags():
    dl, bil, comm = atti_flags('"Conversione in legge del decreto-legge 8 ottobre 2026, n. 177, recante …"', "Governo", [])
    assert dl and not bil and not comm
    dl, _, _ = atti_flags('S. 564. - "Conversione in legge, con modificazioni, del decreto-legge 24 febbraio 2023, n. 20, …"', "Governo", [])
    assert dl
    dl, _, _ = atti_flags("Modifiche al regio decreto-legge 20 luglio 1934", "Parlamentare", [])
    assert not dl
    # bilancio: only Government initiatives
    _, bil, _ = atti_flags('"Bilancio di previsione dello Stato per l\'anno finanziario 2027 …"', "Governo", [])
    assert bil
    _, bil, _ = atti_flags('"Bilancio di previsione dello Stato per l\'anno finanziario 2027 …"', "Parlamentare", [])
    assert not bil
    _, bil, _ = atti_flags('CONSIGLIO REGIONALE DEL VENETO: "Modifiche … recante bilancio di previsione dello Stato …"', "Regioni", [])
    assert not bil
    # V Commissione referente by organo URI
    _, _, comm = atti_flags("Disposizioni varie", "Parlamentare", ["http://dati.camera.it/ocd/organo.rdf/o19_3505"])
    assert comm
    _, _, comm = atti_flags("Disposizioni varie", "Parlamentare", ["http://dati.camera.it/ocd/organo.rdf/o19_3508"])
    assert not comm


def test_atti_query_text():
    q = query_camera_atti(19, "2000-01-01T00:00:00Z")
    assert "SELECT DISTINCT ?atto" in q
    assert 'ocd:sede "Referente"' in q
    assert "ORDER BY ?modified ?atto" in q
    assert "OFFSET" not in q
    assert "LIMIT 1000" in q
    assert "repubblica_19" in q
