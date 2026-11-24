// Tests for store.js (the web app's PostgREST module) with a stubbed fetch.

import { test } from "node:test";
import assert from "node:assert/strict";
import { createStore, sanitizeQuery } from "../store.js";

function jsonResponse(payload, { status = 200, contentRange = null } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (h) => (h.toLowerCase() === "content-range" ? contentRange : null) },
    text: async () => (typeof payload === "string" ? payload : JSON.stringify(payload)),
  };
}

function mockFetch(handler) {
  const calls = [];
  globalThis.fetch = async (url, opts) => {
    calls.push({ url: String(url), headers: opts.headers });
    return handler(String(url), opts);
  };
  return calls;
}

const CFG = { url: "https://x.supabase.co/", key: "test-key" };

async function makeStore(handler) {
  const calls = mockFetch(handler);
  const store = createStore(CFG);
  await store.init();
  return { store, calls };
}

const okInit = () => jsonResponse([]);

test("sanitizeQuery strips PostgREST metacharacters", () => {
  assert.equal(sanitizeQuery("100%_*"), "100");
  assert.equal(sanitizeQuery('l"articolo, (x)\\y.z'), "l articolo x y z");
  assert.equal(sanitizeQuery("  a   b "), "a b");
  assert.equal(sanitizeQuery(""), "");
});

test("init probes the signatory columns and search builds the list query", async () => {
  const { store, calls } = await makeStore((url) => {
    if (url.includes("/rest/v1/emendamenti?")) return jsonResponse([]);
    return jsonResponse([{ id: 1 }], { contentRange: "0-29/123" });
  });
  assert.equal(calls.length, 2);
  assert.ok(calls[0].url.endsWith("/rest/v1/emendamenti?select=firmatari&limit=1"));
  assert.ok(calls[1].url.endsWith("/rest/v1/emendamenti?select=firmatari_uri&limit=1"));

  const r = await store.search({ q: "", type: "all", minScore: 0, sort: "score_desc", offset: 0, limit: 30 });
  assert.deepEqual(r, { items: [{ id: 1 }], total: 123 });
  const main = calls[2];
  assert.ok(main.url.startsWith("https://x.supabase.co/rest/v1/valutazioni?"));
  assert.ok(main.url.includes("order=score_totale.desc.nullslast"));
  assert.ok(main.url.includes("limit=30&offset=0"));
  assert.ok(main.url.includes("emendamenti!inner(numero,tipo,ddl,data,url_testo,firmatari,firmatari_uri)"));
  assert.equal(main.headers.apikey, "test-key");
  assert.equal(main.headers.Authorization, "Bearer test-key");
  assert.equal(main.headers.Prefer, "count=exact");
});

test("search applies type filter, min score and sort", async () => {
  const { store, calls } = await makeStore((url) =>
    url.includes("/rest/v1/valutazioni?") ? jsonResponse([], { contentRange: "*/0" }) : okInit()
  );
  await store.search({ q: "", type: "E", minScore: 7, sort: "data_asc", offset: 60, limit: 30 });
  const url = calls[calls.length - 1].url;
  assert.ok(url.includes("score_totale=gte.7"));
  assert.ok(url.includes("emendamenti.tipo=eq.E"));
  assert.ok(url.includes("order=emendamenti(data).asc.nullslast"));
  assert.ok(url.includes("offset=60"));
});

test("search with q runs the two probes then the or= main query", async () => {
  const { store, calls } = await makeStore((url) => {
    if (url.includes("riepilogo.ilike")) return jsonResponse([{ id: 5 }]);
    if (url.includes("/rest/v1/emendamenti?")) return jsonResponse([{ id: "http://dati.senato.it/emendamento/9" }]);
    return jsonResponse([{ id: 5 }], { contentRange: "0-0/1" });
  });
  const r = await store.search({ q: "salute", type: "all", minScore: 0, sort: "score_desc", offset: 0, limit: 30 });
  assert.equal(r.total, 1);
  const emProbe = calls.find((c) => c.url.includes("/rest/v1/emendamenti?select=id&or="));
  assert.ok(emProbe.url.includes("firmatari.ilike.*salute*"));
  const main = calls[calls.length - 1].url;
  assert.ok(main.includes('or=(id.in.(5),emendamento_id.in.("http://dati.senato.it/emendamento/9"))'));
});

test("search with q and no matches returns empty without a main call", async () => {
  const { store, calls } = await makeStore((url) =>
    url.includes("ilike") || url.includes("/rest/v1/emendamenti?") ? jsonResponse([]) : okInit()
  );
  const before = calls.length;
  const r = await store.search({ q: "zzzz", type: "all", minScore: 0, sort: "score_desc", offset: 0, limit: 30 });
  assert.deepEqual(r, { items: [], total: 0 });
  assert.equal(calls.length - before, 2); // only the two probes
});

test("search treats 416 as end-of-list, other errors reject with status", async () => {
  const { store } = await makeStore((url) =>
    url.includes("/rest/v1/emendamenti?") ? okInit() : jsonResponse("[]", { status: 416 })
  );
  const r = await store.search({ q: "", type: "all", minScore: 0, sort: "score_desc", offset: 9999, limit: 30 });
  assert.deepEqual(r, { items: [], total: null });

  mockFetch(() => jsonResponse(JSON.stringify({ message: "boom" }), { status: 500 }));
  const store2 = createStore(CFG);
  await store2.init().catch(() => {});
  await assert.rejects(
    () => store2.search({ q: "", type: "all", minScore: 0, sort: "score_desc", offset: 0, limit: 30 }),
    (err) => err.status === 500 && err.message.includes("HTTP 500") && err.message.includes("boom")
  );
});

test("detail fetches the valutazione with the detail select", async () => {
  const { store, calls } = await makeStore((url) =>
    url.includes("/rest/v1/emendamenti?") ? okInit() : jsonResponse([{ id: 42, emendamenti: { numero: "1.30" } }])
  );
  const row = await store.detail(42);
  assert.equal(row.id, 42);
  const url = calls[calls.length - 1].url;
  assert.ok(url.includes("valutazioni?id=eq.42&select="));
  assert.ok(url.includes("testo,url_testo,atto_uri,sede_commissione"));
  assert.ok(url.includes("firmatari,firmatari_uri"));
});

test("detail rejects when the valutazione is missing", async () => {
  const { store } = await makeStore((url) =>
    url.includes("/rest/v1/emendamenti?") ? okInit() : jsonResponse([])
  );
  await assert.rejects(() => store.detail(999), /Valutazione non trovata/);
});

test("stats aggregates the five count queries", async () => {
  const { store, calls } = await makeStore((url) => {
    if (url.includes("/rest/v1/emendamenti?")) return okInit();
    if (url.includes("score_totale=gte.7")) return jsonResponse([], { contentRange: "*/41" });
    if (url.includes("emendamenti.tipo=eq.E")) return jsonResponse([], { contentRange: "*/100" });
    if (url.includes("emendamenti.tipo=eq.G")) return jsonResponse([], { contentRange: "*/12" });
    if (url.includes("modello,ts")) return jsonResponse([{ modello: "mistral-small-latest", ts: "2026-11-24T03:00:00Z" }]);
    return jsonResponse([], { contentRange: "*/153" });
  });
  const s = await store.stats();
  assert.deepEqual(s, { total: 153, hi: 41, e: 100, g: 12, last: { modello: "mistral-small-latest", ts: "2026-11-24T03:00:00Z" } });
  assert.equal(calls.filter((c) => c.url.includes("/rest/v1/valutazioni?")).length, 5);
});

test("init without the signatory columns omits them from the select", async () => {
  mockFetch((url) =>
    url.includes("select=firmatari_uri") ? jsonResponse("nope", { status: 400 }) : okInit()
  );
  const store = createStore(CFG);
  await store.init();
  mockFetch((url) => jsonResponse([], { contentRange: "*/0" }));
  const calls = [];
  globalThis.fetch = async (url, opts) => {
    calls.push(String(url));
    return jsonResponse([], { contentRange: "*/0" });
  };
  await store.search({ q: "", type: "all", minScore: 0, sort: "score_desc", offset: 0, limit: 30 });
  assert.ok(!calls[0].includes("firmatari_uri"));
  assert.ok(calls[0].includes("firmatari,") === false || calls[0].includes("firmatari_uri") === false);
});
