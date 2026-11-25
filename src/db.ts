// Persistence on Supabase via REST API (zero extra dependencies).
// Configuration via environment variables:
//   SUPABASE_URL=https://<project>.supabase.co
//   SUPABASE_KEY=sb_publishable_...  (anon/publishable key)

import type { Amendment, Evaluation } from "./types.ts";

const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_KEY = process.env.SUPABASE_KEY;

export function isSupabaseConfigured(): boolean {
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
    throw new Error(`Supabase ${res.status} on ${path}: ${detail}`);
  }
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

/** Saves (upserts) amendments and their evaluations. */
export async function saveToSupabase(
  amendments: Amendment[],
  evaluations: Evaluation[],
): Promise<void> {
  // 1. upsert amendments
  const rows = amendments.map((a) => ({
    id: a.id,
    legislatura: a.legislature,
    numero: a.number,
    tipo: a.type,
    sede_commissione: a.committeeSeat,
    atto_uri: a.actUri,
    ddl: a.bill ?? null,
    url_testo: a.textUrl,
    url_testo_xml: a.textXmlUrl,
    data: a.date ?? null,
    testo: a.text ?? null,
    firmatari: a.signatories && a.signatories.length > 0 ? a.signatories.join(", ") : null,
    firmatari_uri:
      a.signatoryUris && a.signatoryUris.length > 0 ? a.signatoryUris.join(", ") : null,
    numero_padre: a.parentNumber ?? null,
    emendamento_padre_id: a.parentId ?? null,
  }));
  await rest("/rest/v1/emendamenti?on_conflict=id", {
    method: "POST",
    prefer: "resolution=merge-duplicates",
    body: rows,
  });

  // 2. one evaluation per evaluated amendment
  const valRows = evaluations.map((v) => ({
    emendamento_id: v.amendmentId,
    score_totale: v.totalScore,
    riepilogo: v.summary,
    modello: v.model,
    ts: v.timestamp,
  }));
  const inserted = (await rest("/rest/v1/valutazioni", {
    method: "POST",
    prefer: "return=representation",
    body: valRows,
  })) as Array<{ id: number }>;

  // 3. detailed scores per criterion
  const scoreRows = evaluations.flatMap((v, i) =>
    v.scores.map((s) => ({
      valutazione_id: inserted[i].id,
      criterio: s.criterion,
      punteggio: s.score,
      motivazione: s.rationale,
    })),
  );
  if (scoreRows.length > 0) {
    await rest("/rest/v1/punteggi", { method: "POST", body: scoreRows });
  }
}
