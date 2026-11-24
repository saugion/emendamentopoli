// Persistenza su Supabase tramite API REST (zero dipendenze extra).
// Configurazione via variabili d'ambiente:
//   SUPABASE_URL=https://<project>.supabase.co
//   SUPABASE_KEY=sb_publishable_...  (anon/publishable key)

import type { Emendamento, Valutazione } from "./types.ts";

const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_KEY = process.env.SUPABASE_KEY;

export function supabaseConfigurato(): boolean {
  return Boolean(SUPABASE_URL && SUPABASE_KEY);
}

async function rest(
  path: string,
  options: { method: string; body?: unknown; prefer?: string },
): Promise<unknown> {
  const res = await fetch(`${SUPABASE_URL}${path}`, {
    headers: {
      apikey: SUPABASE_KEY as string,
      Authorization: `Bearer ${SUPABASE_KEY as string}`,
      "Content-Type": "application/json",
      ...(options.prefer ? { Prefer: options.prefer } : {}),
    },
    method: options.method,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = JSON.stringify(await res.json());
    } catch {
      detail = await res.text();
    }
    throw new Error(`Supabase ${res.status} su ${path}: ${detail}`);
  }
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

/** Salva (upsert) gli emendamenti e le relative valutazioni. */
export async function salvaSuSupabase(
  emendamenti: Emendamento[],
  valutazioni: Valutazione[],
): Promise<void> {
  // 1. upsert degli emendamenti
  const righe = emendamenti.map((e) => ({
    id: e.id,
    legislatura: e.legislatura,
    numero: e.numero,
    tipo: e.tipo,
    sede_commissione: e.sedeCommissione,
    atto_uri: e.attoUri,
    ddl: e.ddl ?? null,
    url_testo: e.urlTesto,
    url_testo_xml: e.urlTestoXml,
    data: e.data ?? null,
    testo: e.testo ?? null,
    firmatari: e.firmatari && e.firmatari.length > 0 ? e.firmatari.join(", ") : null,
    firmatari_uri:
      e.firmatari_uri && e.firmatari_uri.length > 0 ? e.firmatari_uri.join(", ") : null,
  }));
  await rest("/rest/v1/emendamenti?on_conflict=id", {
    method: "POST",
    prefer: "resolution=merge-duplicates",
    body: righe,
  });

  // 2. una valutazione per emendamento valutato
  const valRows = valutazioni.map((v) => ({
    emendamento_id: v.emendamentoId,
    score_totale: v.scoreTotale,
    riepilogo: v.riepilogo,
    modello: v.modello,
    ts: v.timestamp,
  }));
  const inserted = (await rest("/rest/v1/valutazioni", {
    method: "POST",
    prefer: "return=representation",
    body: valRows,
  })) as Array<{ id: number }>;

  // 3. punteggi dettagliati per criterio
  const punteggiRows = valutazioni.flatMap((v, i) =>
    v.punteggi.map((p) => ({
      valutazione_id: inserted[i].id,
      criterio: p.criterio,
      punteggio: p.punteggio,
      motivazione: p.motivazione,
    })),
  );
  if (punteggiRows.length > 0) {
    await rest("/rest/v1/punteggi", { method: "POST", body: punteggiRows });
  }
}
