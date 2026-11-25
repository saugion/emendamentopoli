// Drives the TS AKN parser against the shared fixtures in tests/fixtures/akn/
// (same XML + expected JSON as the Python suite in workflows/tests/).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseAkn } from "../src/akn.ts";

const here = dirname(fileURLToPath(import.meta.url));
const fixturesDir = join(here, "fixtures", "akn");
const LEGISLATURE = 19;

const xmlFiles = readdirSync(fixturesDir).filter((f) => f.endsWith(".xml")).sort();
assert.ok(xmlFiles.length > 0, "no AKN fixtures found");

for (const name of xmlFiles) {
  test(`fixture ${name}`, () => {
    const xml = readFileSync(join(fixturesDir, name), "utf8");
    const expected = JSON.parse(
      readFileSync(join(fixturesDir, name.replace(/\.xml$/, ".expected.json")), "utf8"),
    );
    const p = parseAkn(xml, LEGISLATURE);
    assert.deepEqual(
      {
        date: p.date,
        bill: p.bill,
        text: p.text,
        signatories: p.signatories,
        signatory_uris: p.signatoryUris,
        parent_number: p.parentNumber,
      },
      expected,
    );
  });
}
