// Client for the Senate Open Data (dati.senato.it, CC BY 3.0 license)
// SPARQL endpoint + Akoma Ntoso texts of individual amendments.
// Parsing lives in the pure akn.ts module; this file only fetches.

import type { Amendment } from "./types.ts";
import { parseAkn } from "./akn.ts";

const SPARQL_ENDPOINT = "https://dati.senato.it/sparql";
const PREFIX = "PREFIX osr: <http://dati.senato.it/osr/>";
const UA = "curl/8.5.0"; // CloudFront only accepts curl-like user-agents on these paths

interface SparqlBinding {
  [varName: string]: { type: string; value: string };
}

async function sparqlSelect(query: string): Promise<Record<string, string>[]> {
  const url =
    SPARQL_ENDPOINT +
    "?query=" +
    encodeURIComponent(query) +
    "&format=application%2Fsparql-results%2Bjson";
  const res = await fetch(url, {
    headers: { Accept: "application/sparql-results+json" },
  });
  if (!res.ok) throw new Error(`SPARQL ${res.status}: ${await res.text()}`);
  const json = (await res.json()) as {
    results: { bindings: SparqlBinding[] };
  };
  return json.results.bindings.map((b) =>
    Object.fromEntries(Object.entries(b).map(([k, v]) => [k, v.value])),
  );
}

/** Numero dell'emendamento padre da "X/N" (subemendamento / proposta di modifica); undefined per gli altri.
 * Gli o.d.g. (tipo G) hanno numeri con "/" nativi (G/912/1/5), quindi non sono subemendamenti. */
export function parentNumberFromNumber(number: string, type: string): string | undefined {
  if (type === "G") return undefined;
  const i = number.lastIndexOf("/");
  return i > 0 ? number.slice(0, i) : undefined;
}

/** Downloads amendment metadata for a legislature (XIX by default). */
export async function fetchAmendments(
  legislature = 19,
  limit = 50,
  offset = 0,
): Promise<Amendment[]> {
  const q = `${PREFIX}
SELECT ?eme ?number ?type ?committeeFlag ?act ?textUrl ?textXmlUrl WHERE {
  ?eme a osr:Emendamento ;
       osr:legislatura ${legislature} ;
       osr:numero ?number ;
       osr:tipo ?type ;
       osr:flagCommissione ?committeeFlag ;
       osr:oggetto ?act ;
       osr:URLTesto ?textUrl ;
       osr:URLTestoXml ?textXmlUrl .
}
OFFSET ${offset} LIMIT ${limit}`;
  const rows = await sparqlSelect(q);
  return rows.map((r) => ({
    id: r.eme,
    legislature,
    number: r.number,
    type: r.type,
    committeeSeat: r.committeeFlag === "1",
    actUri: r.act,
    textUrl: r.textUrl,
    textXmlUrl: r.textXmlUrl,
    parentNumber: parentNumberFromNumber(r.number, r.type),
  }));
}

/** Resolves parentId of subemendamenti (proposte di modifica) via SPARQL: the parent is the
 * emendamento of the same atto + sede with numero = parentNumber. One query per (legislature, atto, sede). */
export async function resolveParentIds(amendments: Amendment[]): Promise<void> {
  const groups = new Map<string, { legislature: number; act: string; committeeSeat: boolean; items: Amendment[] }>();
  for (const a of amendments) {
    if (!a.parentNumber || a.parentId) continue;
    const key = `${a.legislature}|${a.actUri}|${a.committeeSeat}`;
    let g = groups.get(key);
    if (!g) {
      g = { legislature: a.legislature, act: a.actUri, committeeSeat: a.committeeSeat, items: [] };
      groups.set(key, g);
    }
    g.items.push(a);
  }
  for (const g of groups.values()) {
    const numbers = [...new Set(g.items.map((a) => a.parentNumber as string))];
    const values = numbers.map((n) => `"${n.replace(/"/g, "")}"`).join(", ");
    const q = `${PREFIX}
SELECT ?eme ?number ?flag WHERE {
  ?eme a osr:Emendamento ;
       osr:legislatura ${g.legislature} ;
       osr:oggetto <${g.act}> ;
       osr:numero ?number ;
       osr:flagCommissione ?flag .
  FILTER(STR(?flag) = "${g.committeeSeat ? "1" : "0"}")
  FILTER(?number IN (${values}))
}`;
    const rows = await sparqlSelect(q);
    const byNumber = new Map(rows.map((r) => [r.number, r.eme]));
    for (const a of g.items) a.parentId = byNumber.get(a.parentNumber as string) ?? a.parentId;
  }
}

/** Downloads the Akoma Ntoso text of an amendment and parses date, bill, text and signatories. */
export async function enrichWithContext(eme: Amendment): Promise<Amendment> {
  const url = eme.textXmlUrl.replace(/^http:/, "https:");
  const res = await fetch(url, { headers: { "User-Agent": UA } });
  if (!res.ok) throw new Error(`AKN fetch ${res.status} for ${eme.id}`);
  // the XML is iso-8859-1: decode bytes as latin1 (res.text() would assume UTF-8)
  const xml = new TextDecoder("windows-1252").decode(await res.arrayBuffer());
  const parsed = parseAkn(xml, eme.legislature);
  eme.date = parsed.date ?? undefined;
  eme.bill = parsed.bill ?? undefined;
  eme.text = parsed.text ?? undefined;
  eme.signatories = parsed.signatories;
  eme.signatoryUris = parsed.signatoryUris;
  eme.parentNumber = parsed.parentNumber ?? eme.parentNumber;
  return eme;
}
