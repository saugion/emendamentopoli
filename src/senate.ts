// Client for the Senate Open Data (dati.senato.it, CC BY 3.0 license)
// SPARQL endpoint + Akoma Ntoso texts of individual amendments.

import type { Amendment } from "./types.ts";

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

/** Downloads the Akoma Ntoso text of an amendment and extracts date, bill and text. */
export async function enrichWithContext(eme: Amendment): Promise<Amendment> {
  const url = eme.textXmlUrl.replace(/^http:/, "https:");
  const res = await fetch(url, { headers: { "User-Agent": UA } });
  if (!res.ok) throw new Error(`AKN fetch ${res.status} for ${eme.id}`);
  // the XML is iso-8859-1: decode bytes as latin1 (res.text() would assume UTF-8)
  const xml = new TextDecoder("windows-1252").decode(await res.arrayBuffer());

  // submission date (<an:FRBRdate date="..." name="presentazione" />)
  const m = xml.match(/FRBRdate date="(\d{4}-\d{2}-\d{2})" name="presentazione"/);
  eme.date = m?.[1];

  // referenced bill (showAs="Ddl 391")
  const mBill = xml.match(/activeRef[^>]*showAs="(Ddl [^"]+)"/);
  eme.bill = mBill?.[1];

  // body: contents of <an:amendmentBody> cleaned of tags
  const body = xml.match(/<an:amendmentBody>([\s\S]*?)<\/an:amendmentBody>/);
  if (body) {
    eme.text = decode(
      body[1]
        .replace(/<(an:)?doc[\s\S]*?>[\s\S]*?<\/(an:)?doc>/g, "") // doc notes
        .replace(/&#x[0-9a-fA-F]+;/g, " ")
        .replace(/<[^>]+>/g, " ")
        .replace(/&nbsp;/g, " ")
        .replace(/\s+/g, " ")
        .trim(),
    );
  }
  // signatories: <an:docProponent> in the preface (the first one is the proposer)
  const s = extractSignatories(xml, eme.legislature);
  eme.signatories = s.names;
  eme.signatoryUris = s.uris;

  return eme;
}

/**
 * Extracts signatories from the AKN preface: each <an:docProponent> references
 * (refersTo) an <an:TLCPerson> in <an:references> with an href to
 * dati.senato.it, from which the senato.it profile page URL is derived.
 */
export function extractSignatories(
  xml: string,
  legislature: number,
): { names: string[]; uris: string[] } {
  const persons = new Map<string, string>();
  for (const m of xml.matchAll(/<(?:an:)?TLCPerson\b[^>]*>/g)) {
    const id = / id="([^"]+)"/.exec(m[0])?.[1];
    const href = / href="([^"]+)"/.exec(m[0])?.[1];
    if (id && href) persons.set(id, href);
  }
  const names: string[] = [];
  const uris: string[] = [];
  for (const m of xml.matchAll(/<(?:an:)?docProponent\b([^>]*)>([\s\S]*?)<\/(?:an:)?docProponent>/g)) {
    const name = decode(m[2].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim());
    if (!name || names.includes(name)) continue;
    const ref = / refersTo="#([^"]+)"/.exec(m[1])?.[1];
    const href = ref ? persons.get(ref) : undefined;
    names.push(name);
    uris.push(href ? senatorProfile(href, legislature) : "");
  }
  return { names, uris };
}

/** dati.senato.it href (e.g. .../senatore/407 or .../29521) -> profile page on senato.it ("" if not a person, e.g. senatore/0 = "Il Relatore"). */
function senatorProfile(href: string, legislature: number): string {
  const m = href.match(/^https?:\/\/dati\.senato\.it\/(?:senatore\/)?(\d+)\/?$/);
  if (!m) return href.replace(/^http:/, "https:");
  if (/^0+$/.test(m[1])) return "";
  return `https://www.senato.it/leg/${legislature}/BGT/Schede/Attsen/${m[1].padStart(8, "0")}.htm`;
}

function decode(s: string): string {
  const map: Record<string, string> = {
    "&#039;": "'",
    "&apos;": "'",
    "&quot;": '"',
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
  };
  return s.replace(/&#039;|&apos;|&quot;|&amp;|&lt;|&gt;/g, (m) => map[m] ?? m);
}
