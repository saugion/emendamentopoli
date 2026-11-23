# emendamentopoli

Valutazione IA dell'utilitÃ  pubblica degli emendamenti parlamentari italiani.

**Stato**: prototipo (solo Senato; Camera in roadmap).

## Come funziona

1. **Ingestione**: scarica i metadati degli emendamenti dall'endpoint SPARQL
   di [dati.senato.it](https://dati.senato.it) (Open Data, licenza CC BY 3.0).
2. **Testi**: recupera il testo Akoma Ntoso di ogni emendamento da senato.it
   e ne estrae data di presentazione, atto di riferimento e testo plain-text.
3. **Valutazione**: un LLM (Mistral) valuta ogni emendamento su una rubrica
   di interesse pubblico con punteggi 0-10 e motivazioni obbligatorie:
   - trasparenza (peso 0.3) â trasparenza democratica, legalitÃ , accountability
   - salute (0.25) â impatto su salute pubblica
   - ambiente (0.25) â impatto su ambiente e clima
   - concretezza (0.2) â efficacia tecnica, non genericitÃ 
4. **Score composito**: media pesata dei criteri.

Senza `MISTRAL_API_KEY` gira in modalitÃ  euristica (lessicale), solo sviluppo.

## Uso

```bash
MISTRAL_API_KEY=... npm run demo -- --limit 10
npm run demo -- --limit 10 --offset 50
```

Zero dipendenze runtime: richiede Node 22+ (TypeScript nativo).

## File

- `src/types.ts` â tipi + rubrica e pesi
- `src/senato.ts` â client SPARQL + parser Akoma Ntoso
- `src/evaluate.ts` â valutazione LLM (con fallback euristico)
- `src/main.ts` â CLI demo

## Roadmap

- [ ] Persistenza (PostgreSQL) per evitare rivalutazioni
- [ ] Coda e rate limiting per i batch (BullMQ)
- [ ] Ingestione Camera (scraping fascicoli; nessun dataset equivalente)
- [ ] API REST pubblica (Fastify) + web app Next.js
- [ ] Audit: campioni rivalutati con piÃ¹ modelli, feedback utenti
- [ ] Disclaimer e pesi pubblicati (mitigazione del bias politico)

## Note legali

Dati Senato: CC BY 3.0, citare la fonte "Senato della Repubblica â dati.senato.it".
Gli score sono valutazioni algoritmiche di supporto, non giudizi istituzionali.
Verificare i termini per lo scraping dei contenuti di camera.it prima di estendere.
