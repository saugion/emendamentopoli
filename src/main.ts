// Demo: downloads amendments from the Senate, sorts them chronologically
// and evaluates them against the public-interest rubric.
//
// Usage:
//   MISTRAL_API_KEY=... npm run demo -- --limit 5
//   node --experimental-strip-types src/main.ts --limit 5 --offset 50

import { fetchAmendments, enrichWithContext, resolveParentIds } from "./senate.ts";
import { evaluate } from "./evaluate.ts";
import { isSupabaseConfigured, saveToSupabase } from "./db.ts";
import type { Amendment, Evaluation } from "./types.ts";

const args = process.argv.slice(2);
const limitIdx = args.indexOf("--limit");
const limit = limitIdx >= 0 ? parseInt(args[limitIdx + 1] ?? "5", 10) : 5;
const offsetIdx = args.indexOf("--offset");
const offset = offsetIdx >= 0 ? parseInt(args[offsetIdx + 1] ?? "0", 10) : 0;

async function main() {
  console.log(`\u2192 Downloading metadata for ${limit} amendments (Senate, leg. XIX)...`);
  const base = await fetchAmendments(19, limit, offset);

  console.log("\u2192 Fetching Akoma Ntoso texts...");
  const enriched = (await Promise.allSettled(base.map(enrichWithContext)))
    .filter((r): r is PromiseFulfilledResult<Amendment> => r.status === "fulfilled")
    .map((r) => r.value);

  // chronological order (submission date)
  enriched.sort((a, b) => (a.date ?? "").localeCompare(b.date ?? ""));

  // link subemendamenti (proposte di modifica) to the parent emendamento
  try {
    await resolveParentIds(enriched);
  } catch (e) {
    console.log(`(risoluzione emendamento padre non riuscita: ${(e as Error).message})`);
  }

  console.log(`\u2192 Evaluating ${enriched.length} amendments...`);
  const evaluations: Evaluation[] = [];
  for (const eme of enriched) {
    const v = await evaluate(eme);
    evaluations.push(v);
    console.log(
      `\n\u2501\u2501\u2501 ${eme.type === "G" ? "Order of the day" : "Amendment"} ${eme.number} \u2014 ${eme.bill ?? "?"} \u2014 ${eme.date ?? "n/a"} (${eme.committeeSeat ? "Committee" : "Plenary"})`,
    );
    console.log(`    Total score: ${v.totalScore}/10 (model: ${v.model})`);
    for (const s of v.scores) {
      console.log(`    \u2022 ${s.criterion}: ${s.score}/10 \u2014 ${s.rationale}`);
    }
    console.log(`    Summary: ${v.summary}`);
    console.log(`    Text: ${(eme.text ?? "").slice(0, 280)}${(eme.text ?? "").length > 280 ? "..." : ""}`);
    if (eme.signatories && eme.signatories.length > 0) {
      const f = eme.signatories;
      console.log(`    Signatories (${f.length}): ${f.slice(0, 5).join(", ")}${f.length > 5 ? ", ..." : ""}`);
    }
    if (eme.parentNumber) {
      console.log(`    Padre: emendamento ${eme.parentNumber}${eme.parentId ? ` (${eme.parentId})` : ""}`);
    }
    console.log(`    Source: ${eme.textUrl}`);
  }

  if (isSupabaseConfigured()) {
    console.log("\u2192 Saving to Supabase...");
    await saveToSupabase(enriched, evaluations);
    console.log("\u2192 Save completed.");
  } else {
    console.log("(Set SUPABASE_URL and SUPABASE_KEY to save to Supabase)");
  }
}

main().catch((e) => {
  console.error("Error:", e.message);
  process.exit(1);
});
