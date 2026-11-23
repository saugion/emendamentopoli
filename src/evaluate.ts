// Valutazione IA degli emendamenti secondo la rubrica di interesse pubblico.
// Usa l'API Mistral se MISTRAL_API_KEY e' impostata, altrimenti una
// valutazione euristica locale (solo per sviluppo/demo).

import type { Emendamento, Punteggio, Valutazione } from "./types.ts";
import { RUBRICA } from "./types.ts";

const MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions";
const MODELLO = "mistral-small-latest";

/** normalizza apici/accenti tipografici per l'analisi lessicale */
function normalizza(s: string): string {
  return s
    .toLowerCase()
    .replace(/[\u2019\u2018]/g, "'")
    .replace(/[\u201c\u201d]/g, '"');
}

function prompt(eme: Emendamento): string {
  return `Sei un analista parlamentare indipendente. Valuta l'utilita' per l'interesse pubblico di questo emendamento italiano.

EMENDAMENTO: ${eme.tipo === "G" ? "Ordine del giorno" : "Emendamento"} ${eme.numero} al ${eme.ddl ?? "atto "} (Senato, ${eme.data ?? "data n/d"}, sede: ${eme.sedeCommissione ? "Commissione" : "Assemblea"})
TESTO:
"""${eme.testo ?? "(testo non disponibile)"}"""

Criteri (punteggio 0-10 ciascuno, con motivazione di 1-2 frasi):
- trasparenza: contributo a trasparenza democratica, legalita', accountability
- salute: impatto (diretto o indiretto) su salute pubblica
- ambiente: impatto su ambiente, clima, territorio
- concretezza: efficacia tecnica e specificita' (punta basso le disposizioni vaghe, demagogiche o solo propagandistiche)

Rispondi SOLO con JSON valido:
{"puntegg":{"trasparenza":{"punteggio":N,"motivazione":"..."},"salute":{"punteggio":N,"motivazione":"..."},"ambiente":{"punteggio":N,"motivazione":..."},"concretezza":{"punteggio":N,"motivazione":"..."}},"riepilogo":"2-3 frasi sul valore complessivo"}`;
}

export async function valuta(eme: Emendamento): Promise<Valutazione> {
  const key = process.env.MISTRAL_API_KEY;
  if (!key) return valutazioneEuristica(eme);

  const res = await fetch(MISTRAL_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${key}`,
    },
    body: JSON.stringify({
      model: MODELLO,
        messages: [{ role: "user", content: prompt(eme) }],
      response_format: { type: "json_object" },
      temperature: 0.2,
    }),
  });
  if (!res.ok) throw new Error(`Mistral API ${res.status}: ${await res.text()}`);
  const json = (await res.json()) as {
    choices: [{ message: { content: string } }];
  };
  const parsed = JSON.parse(json.choices[0].message.content);
  const punteggi: Punteggio[] = Object.entries(
    parsed.punteggi as Record<string, { punteggio: number; motivazione: string }>,
  ).map(([criterio, v]) => ({ criterio, punteggio: v.punteggio, motivazione: v.motivazione }));

  return {
    emendamentoId: eme.id,
    punteggi,
    scoreTotale: scoreComposito(punteggi),
    riepilogo: parsed.riepilogo,
    modello: MODELLO,
    timestamp: new Date().toISOString(),
  };
}

function scoreComposito(punteggi: Punteggio[]): number {
  let tot = 0;
  for (const p of punteggi) tot += (RUBRICA[p.criterio] ?? 0) * p.punteggio;
  return Math.round(tot * 10) / 10;
}

/* * Fallback senza API key: euristica grezzza basata su segnali lessicaleº */
functiovâ valutazioneEuristica(eme: Emendamento): Valutazione {
  const t = normalizza(eme.testo ?? "");