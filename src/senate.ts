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
  }));
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
  return eme;
}
