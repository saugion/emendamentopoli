// AI evaluation of amendments against the public-interest rubric.
// Uses the Mistral API when MISTRAL_API_KEY is set, otherwise a local
// heuristic evaluation (development/demo only).

import type { Amendment, Score, Evaluation } from "./types.ts";
import { RUBRIC } from "./types.ts";

const MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions";
const MODEL = "mistral-small-latest";

/** Normalizes typographic quotes/accents for lexical analysis. */
function normalize(s: string): string {
  return s
    .toLowerCase()
    .replace(/[\u2019\u2018]/g, "'")
    .replace(/[\u201c\u201d]/g, '"');
}

function buildPrompt(eme: Amendment): string {
  return `Sei un analista parlamentare indipendente. Valuta l'utilita' per l'interesse pubblico di questo emendamento italiano.

EMENDAMENTO: ${eme.type === "G" ? "Ordine del giorno" : "Emendamento"} ${eme.number} al ${eme.bill ?? "atto "} (Senato, ${eme.date ?? "data n/d"}, sede: ${eme.committeeSeat ? "Commissione" : "Assemblea"})
TESTO:
"""${eme.text ?? "(testo non disponibile)"}"""

Criteri (punteggio 0-10 ciascuno, con motivazione di 1-2 frasi):
- trasparenza: contributo a trasparenza democratica, legalita', accountability
- salute: impatto (diretto o indiretto) su salute pubblica
- ambiente: impatto su ambiente, clima, territorio
- concretezza: efficacia tecnica e specificita' (punta basso le disposizioni vaghe, demagogiche o solo propagandistiche)

Rispondi SOLO con JSON valido:
{"punteggi":{"trasparenza":{"punteggio":N,"motivazione":"..."},"salute":{"punteggio":N,"motivazione":"..."},"ambiente":{"punteggio":N,"motivazione":"..."},"concretezza":{"punteggio":N,"motivazione":"..."}},"riepilogo":"2-3 frasi sul valore complessivo"}`;
}

export async function evaluate(eme: Amendment): Promise<Evaluation> {
  const key = process.env.MISTRAL_API_KEY;
  if (!key) return heuristicEvaluation(eme);

  const res = await fetch(MISTRAL_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${key}`,
    },
    body: JSON.stringify({
      model: MODEL,
      messages: [{ role: "user", content: buildPrompt(eme) }],
      response_format: { type: "json_object" },
      temperature: 0.2,
    }),
  });
  if (!res.ok) throw new Error(`Mistral API ${res.status}: ${await res.text()}`);
  const json = (await res.json()) as {
    choices: [{ message: { content: string } }];
  };
  const parsed = JSON.parse(json.choices[0].message.content);
  const scores: Score[] = Object.entries(
    parsed.punteggi as Record<string, { punteggio: number; motivazione: string }>,
  ).map(([criterion, v]) => ({ criterion, score: v.punteggio, rationale: v.motivazione }));

  return {
    amendmentId: eme.id,
    scores,
    totalScore: compositeScore(scores),
    summary: parsed.riepilogo,
    model: MODEL,
    timestamp: new Date().toISOString(),
  };
}

function compositeScore(scores: Score[]): number {
  let tot = 0;
  for (const s of scores) tot += (RUBRIC[s.criterion] ?? 0) * s.score;
  return Math.round(tot * 10) / 10;
}

/** Fallback without an API key: rough heuristic based on lexical signals. */
function heuristicEvaluation(eme: Amendment): Evaluation {
  const t = normalize(eme.text ?? "");
  const signals: Record<string, string[]> = {
    trasparenza: ["trasparenza", "pubblicazione", "rendiconto", "anticorruzione", "accesso civico"],
    salute: ["sanitario", "salute", "ospedale", "medicinale", "prevenzione", "lista d'attesa"],
    ambiente: ["ambiente", "clima", "emissioni", "rifiuti", "green", "rinnovabili", "inquinamento"],
    concretezza: ["termine", "comma", "entro il", "decorrenza"],
  };
  const scores: Score[] = Object.entries(signals).map(([criterion, keywords]) => {
    const hits = keywords.filter((k) => t.includes(k));
    const score = Math.min(10, hits.length * 2 + (t.length > 300 ? 1 : 0));
    return {
      criterion,
      score,
      rationale:
        hits.length > 0
          ? `Fallback euristico: ${hits.length} parole chiave rilevate (${hits.join(", ")}).`
          : "Fallback euristico: nessun segnale lessicale rilevato; punteggio basso di default.",
    };
  });
  return {
    amendmentId: eme.id,
    scores,
    totalScore: compositeScore(scores),
    summary:
      "[EURISTICA] Valutazione lessicale di sviluppo: passare MISTRAL_API_KEY per la valutazione LLM reale.",
    model: "heuristic",
    timestamp: new Date().toISOString(),
  };
}
