// Demo: scarica emendamenti dal Senato, li ordina cronologicamente
// e li valuta con la rubrica di interesse pubblico.
//
// Uso:
//   MISTRAL_API_KEY=... npm run demo -- --limit 5
//   node --experimental-strip-types src/main.ts --limit 5 --offset 50

import { fetchEmendamenti, arricchisciConTesto } from "./senato.ts";
import { valuta } from "./evaluate.ts";
import { supabaseConfigurato, salvaSuSupabase } from "./db.ts";
import type { Emendamento, Valutazione } from "./types.ts";

const args = process.argv.slice(2);
const limitIdx = args.indexOf("--limit");
const limit = limitIdx >= 0 ? parseInt(args[limitIdx + 1] ?? "5", 10) : 5;
const offsetIdx = args.indexOf("--offset");
const offset = offsetIdx >= 0 ? parseInt(args[offsetIdx + 1] ?? "0", 10) : 0;

async function main() {
  console.log(`\u2192 Scarico metadati di ${limit} emendamenti (Senato, leg. XIX)...`);
  const base = await fetchEmendamenti(19, limit, offset);

  console.log("\u2192 Recupero testi Akoma Ntoso...");
  const arricchiti = (await Promise.allSettled(base.map(arricchisciConTesto)))
    .filter((r): r is PromiseFulfilledResult<Emendamento> => r.status === "fulfilled")
    .map((r) => r.value);

  // ordinamento cronologico (data di presentazione)
  arricchiti.sort((a, b) => (a.data ?? "").localeCompare(b.data ?? ""));

  console.log(`\u2192 Valuto ${arricchiti.length} emendamenti...`);
  const valutazioni: Valutazione[] = [];
  for (const eme of arricchiti) {
    const v = await valuta(eme);
    valutazioni.push(v);
    console.log(
      `\n\u2501\u2501\u2501 ${eme.tipo === "G" ? "O.d.G." : "Emendamento"} ${eme.numero} \u2014 ${eme.ddl ?? "?"} \u2014 ${eme.data ?? "n/d"} (${eme.sedeCommissione ? "Commissione" : "Assemblea"})`,
    );
    console.log(`    Score totale: ${v.scoreTotale}/10 (modello: ${v.modello})`);
    for (const p of v.punteggi) {
      console.log(`    \u2022 ${p.criterio}: ${p.punteggio}/10 \u2014 ${p.motivazione}`);
    }
    console.log(`    Riepilogo: ${v.riepilogo}`);
    console.log(`    Testo: ${(eme.testo ?? "").slice(0, 280)}${(eme.testo ?? "").length > 280 ? "..." : ""}`);
    if (eme.firmatari && eme.firmatari.length > 0) {
      const f = eme.firmatari;
      console.log(`    Firmatari (${f.length}): ${f.slice(0, 5).join(", ")}${f.length > 5 ? ", ..." : ""}`);
    }
    console.log(`    Fonte: ${eme.urlTesto}`);
  }

  if (supabaseConfigurato()) {
    console.log("\u2192 Salvo su Supabase...");
    await salvaSuSupabase(arricchiti, valutazioni);
    console.log("\u2192 Salvataggio completato.");
  } else {
    console.log("(Imposta SUPABASE_URL e SUPABASE_KEY per salvare su Supabase)");
  }
}

main().catch((e) => {
  console.error("Errore:", e.message);
  process.exit(1);
});
