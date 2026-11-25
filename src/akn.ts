// Pure Akoma Ntoso parser: XML text -> ParsedAkn (no fetching).
// Shared contract with workflows/.../akn.py: tests/fixtures/akn/*.xml +
// *.expected.json are read by both the Node and the Python test suites.

export interface ParsedAkn {
  date: string | null;      // submission date (YYYY-MM-DD)
  bill: string | null;      // e.g. "Ddl 391"
  text: string | null;      // plain-text body
  signatories: string[];    // senators' names (first = proposer)
  signatoryUris: string[];  // senato.it profile URLs, aligned with signatories ("" if unknown)
  parentNumber: string | null; // numero dell'emendamento padre (subemendamento "X/N" -> "X")
}

/** Extracts the submission date: <an:FRBRdate date="..." name="presentazione" /> */
function extractDate(xml: string): string | null {
  return /FRBRdate date="(\d{4}-\d{2}-\d{2})" name="presentazione"/.exec(xml)?.[1] ?? null;
}

/** Extracts the referenced bill (showAs="Ddl 391"). */
function extractBill(xml: string): string | null {
  return /activeRef[^>]*showAs="(Ddl [^"]+)"/.exec(xml)?.[1] ?? null;
}

/** Body: contents of <an:amendmentBody> cleaned of tags. */
function extractText(xml: string): string | null {
  const body = /<an:amendmentBody>([\s\S]*?)<\/an:amendmentBody>/.exec(xml);
  if (!body) return null;
  return decode(
    body[1]
      .replace(/<(an:)?doc[\s\S]*?>[\s\S]*?<\/(an:)?doc>/g, "") // doc notes
      .replace(/&#x[0-9a-fA-F]+;/g, " ")
      .replace(/<[^>]+>/g, " ")
      .replace(/&nbsp;/g, " ")
      .replace(/\s+/g, " ")
      .trim(),
  );
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

/**
 * Extracts the parent amendment number of a "proposta di modifica" (subemendamento, numero "X/N"):
 * the preface's <an:affectedDocument> points at an <an:activeRef>/<an:original> whose href is the
 * parent Emend work URI (.../Emend/<ramo>/<atto>/<sede>/<numero>); fallback: <an:docNumber>
 * "Proposta di modifica n. X/N".
 */
export function extractParentNumber(xml: string): string | null {
  const refs = new Map<string, string>();
  for (const m of xml.matchAll(/<(?:an:)?(?:activeRef|original)\b[^>]*>/g)) {
    const id = / id="([^"]+)"/.exec(m[0])?.[1];
    const href = / href="([^"]+)"/.exec(m[0])?.[1];
    if (id && href) refs.set(id, href);
  }
  for (const m of xml.matchAll(/<(?:an:)?affectedDocument\b([^>]*)>/g)) {
    const ref = / href="#([^"]+)"/.exec(m[1])?.[1];
    const href = ref ? refs.get(ref) : undefined;
    const em = href ? /\/Emend\/[^/]+\/[^/]+\/[^/]+\/([^/]+)/.exec(href) : null;
    if (em) return em[1];
  }
  for (const m of xml.matchAll(/<(?:an:)?docNumber\b[^>]*>([\s\S]*?)<\/(?:an:)?docNumber>/g)) {
    const text = decode(m[1].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim());
    const pm = /proposta di modifica\s+n\.?\s*(\S+)/i.exec(text);
    if (pm && pm[1].includes("/")) return pm[1].slice(0, pm[1].lastIndexOf("/"));
  }
  return null;
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

/** Parses an Akoma Ntoso document into the shared ParsedAkn shape. */
export function parseAkn(xml: string, legislature: number): ParsedAkn {
  const s = extractSignatories(xml, legislature);
  return {
    date: extractDate(xml),
    bill: extractBill(xml),
    text: extractText(xml),
    signatories: s.names,
    signatoryUris: s.uris,
    parentNumber: extractParentNumber(xml),
  };
}
