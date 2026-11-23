// Client per gli Open Data del Senato (dati.senato.it, licenza CC BY 3.0)
// Endpoint SPARQL + testi Akoma Ntoso dei singoli emendamenti.

import type { Emendamento } from "./types.ts";

const SPARQL_ENDPOINT = "https://dati.senato.it/sparql";
const PREFIX = "PREFIX osr: <http://dati.senato.it/osr/>";
const UA = "curl/8.5.0"; // CloudFront accetta solo user-agent curl-like su questi path

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

/** Scarica i metadati degli emendamenti per legislatura (di default la XIX). */
export async function fetchEmendamenti(
  legislatura = 19,
  limit = 50,
  offset = 0,
): Promise<Emendamento[]> {
  const q = `${PREFIX}
SELECT ?eme ?numero ?tipo ?flagCom ?oggetto ?urlTesto ?urlTestoXml WHERE {
  ?eme a osr:Emendamento ;
       osr:legislatura ${legislatura} ;
       osr:numero ?numero ;
       osr:tipo ?tipo ;
       osr:flagCommissione ?flagCom ;
       osr:oggetto ?oggetto ;
       osr:URLTesto ?urlTesto ;
       osr:URLTestoXml ?urlTestoXml .
}
OFFSET ${offset} LIMIT ${limit}`;
  const rows = await sparqlSelect(q);
  return rows.map((r) => ({
    id: r.eme,
    legislatura,
    numero: r.numero,
    tipo: r.tipo,
    sedeCommissione: r.flagCom === "1",
    attoUri: r.oggetto,
    urlTesto: r.urlTesto,
    urlTestoXml: r.urlTestoXml,
  }));
}

/** Scarica il testo Akoma Ntoso di un emendamento e ne estrae data, atto e testo. */
export async function arricchisciConTesto(eme: Emendamento): Promise<Emendamento> {
  const url = eme.urlTestoXml.replace(/^http:/, "https:");
  const res = await fetch(url, { headers: { "User-Agent": UA } });
  if (!res.ok) throw new Error(`Fetch AKN ${res.status} per ${eme.id}`);
  // il XML e' iso-8859-1: decodifica byte->latin1 (res.text() assumerebbe UTF-8)
  const xml = new TextDecoder("windows-1252").decode(await res.arrayBuffer());

  // data di presentazione (frase: <an:FRBRdate date="..." name="presentazione" />)
  const m = xml.match(/FRBRdate date="(\d{4}-\d{2}-\d{2})" name="presentazione"/);
  eme.data = m?.[1];

  // atto di riferimento (frase: showAs="Ddl 391")
  const mDdl = xml.match(/activeRef[^>]*showAs="(Ddl [^"]+)"/);
  eme.ddl = mDdl?.[1];

  // testo: contenuto di <an:amendmentBody> ripulito dai tag
  const body = xml.match(/<an:amendmentBody>([\s\S]*?)<\/an:amendmentBody>/);
  if (body) {
    eme.testo = decode(
      body[1]
        .replace(/<(an:)?doc[\s\S]*?>[\s\S]*?<\/(an:)?doc>/g, "") // note doc
        .replace(/&#x[0-9a-fA-F]+;/g, " ")
        .replace(/<[^>]+>/g, " ")
        .replace(/&nbsp;/g, " ")
        .replace(/\s+/g, " ")
        .trim(),
    );
  }
  return eme;
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
