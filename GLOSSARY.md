# emendamentopoli

Domain language for the pipeline that ingests Senate amendments, evaluates their public interest, and publishes the results in a web app.

## Language

**Emendamento**: a proposed amendment (tipo E) or order of the day (tipo G) to a bill in the Senate, identified by its dati.senato.it URI.
_Avoid_: atto, documento

**Firmatario**: a senator who signs an emendamento; the first signatory is the proposer.
_Avoid_: autore, profilo

**Valutazione**: the assessment of an emendamento's public interest, with one punteggio per rubric criterion and a weighted total.
_Avoid_: score, review

**Punteggio**: the 0-10 value for one rubric criterion, always with a rationale.
_Avoid_: voto, rating

**Rubrica**: the four public-interest criteria with their weights — trasparenza 0.3, salute 0.25, ambiente 0.25, concretezza 0.2.
_Avoid_: griglia, criteria set

**Periodo di ingestione**: the inclusive [date, end_date] range of submission dates covered by one ingest run.
_Avoid_: batch window, range

**Testo Akoma Ntoso**: the XML representation of an emendamento's text, from which date, bill, plain text and firmatari are extracted.
_Avoid_: XML file, documento
