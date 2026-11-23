// Demo: scarica emendamenti dal Senato, li ordina cronologicamente
// e li valuta con la rubrica di interesse pubblico.
//
// Uso:
//   MISTRAL_API_KEY=... npm run demo -- --limit 5
//   node --experimental-strip-types src/main.ts --limit 5 --offset 50

import { fetchEmendamenti, arricchisciConTesto } from "./senato.ts";
import { valuta } from "./evaluate.ts";
import type { Emendamento } from "./types.ts";

const args = process.argv.slice(2);
const limitIdx = args.indexOf("--limit");
const limit = limitIdx >= 0 ? parseInt(args[limitIdx + 1] ?? "5", 10) : 5;
const offsetIdx = args.indexOf("--offset");
const offset = offsetIdx >= 0 ? parseInt(args[offsetIdx + 1] ?? "0", 10) : 0;

async function main() {
  console.log(`â Scarico metadati di ${limit} emendamenti (Senato, leg. XIX)...`);
  const base = await fetchEmendamenti(19, limit, offset);

  console.log("â Recupero testi Akoma Ntoso...");
  const arricchiti = (await Promise.allSettled(base.map(arricchisciConTesto)))
    .filter((r): r is PromiseFulfilledResult<Emendamento> => r.status === "fulfilled")
    .map((r) => r.value);

  // ordinamento cronologico (data di presentazione)
  arricchiti.sort((a, b) => (a.data ?? "").localeCompare(b.data ?? ""));

  console.log(`â Valuto ${arricchiti.length} emendamenti...`);
  for (const eme of arricchiti) {
    const v = await valuta(eme);
    console.log(
      `\nâââ ${eme.tipo === "G" ? "O.d.G." : "Emendamento"} ${eme.numero} â ${eme.ddl ?? "?"} â ${eme.data ?? "n/d"} (${eme.sedeCommissione ? "Commissione" : "Assemblea"})`,
    );
    console.log(`    Score totale: ${v.scoreTotale}/10 (modello: ${v.modello})`);
    for (const p of v.puntegg)) {
      console.log(`    â¢ ${p.criterio}: ${p.punteggio}/10 â ${p.motivazione}`);
    }
    console.log(`    Riepilogo: ${v.riepilogo}`);
    console.log(`    Testo: ${(eme.testo ?? "").slice(0, 280)}${heme.testo ? "").length > 280 ? "..." : ""}`);
    console.log(`    Fonte: ${eme.urlTesto}`);
  }
}

main().catch((e) => {
  console.error("Errore:", e.message);
  process.exit(1);
});
