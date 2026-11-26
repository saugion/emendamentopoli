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
  assert.equal(calls.length, 6);
  assert.ok(calls[0].url.endsWith("/rest/v1/emendamenti?select=firmatari&limit=1"));
  assert.ok(calls[1].url.endsWith("/rest/v1/emendamenti?select=firmatari_uri&limit=1"));
  assert.ok(calls[2].url.endsWith("/rest/v1/emendamenti?select=camera&limit=1"));
  assert.ok(calls[3].url.endsWith("/rest/v1/emendamenti?select=corrente&limit=1"));
  assert.ok(calls[4].url.endsWith("/rest/v1/valutazioni?select=impatto_finanziario&limit=1"));
  assert.ok(calls[5].url.endsWith("/rest/v1/emendamenti?select=numero_padre&limit=1"));

  const r = await store.search({ q: "", type: "all", minScore: 0, sort: "score_desc", offset: 0, limit: 30 });
  assert.deepEqual(r, { items: [{ id: 1 }], total: 123 });
  const main = calls[6];
  assert.ok(main.url.startsWith("https://x.supabase.co/rest/v1/valutazioni?"));
  assert.ok(main.url.includes("order=score_totale.desc.nullslast"));
  assert.ok(main.url.includes("limit=30&offset=0"));
  assert.ok(main.url.includes("emendamenti!inner(numero,tipo,ddl,data,url_testo,firmatari,firmatari_uri,camera,esito,numero_padre)"));
  assert.ok(main.url.includes("emendamenti.corrente=eq.true"));
  assert.equal(main.headers.apikey, "test-key");
  assert.equal(main.headers.Authorization, "Bearer test-key");
  assert.equal(main.headers.Prefer, "count=exact");
});

test("search applies type filter, min score and sort", async () => {
  const { store, calls } = await makeStore((url) =>
    url.includes("/rest/v1/valutazioni?") ? jsonResponse([], { contentRange: "*/0" }) : okInit()
  );
  await store.search({ q: "", type: "E", minScore: 7, sort: "score_asc", offset: 60, limit: 30 });
  const url = calls[calls.length - 1].url;
  assert.ok(url.includes("score_totale=gte.7"));
  assert.ok(url.includes("emendamenti.tipo=eq.E"));
  assert.ok(url.includes("order=score_totale.asc.nullslast"));
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
  assert.ok(main.includes("or=" + encodeURIComponent('(id.in.(5),emendamento_id.in.("http://dati.senato.it/emendamento/9"))')));
});

test("search encodes the or= clause: ids with # (camera) must not truncate the query string", async () => {
  const { store, calls } = await makeStore((url) => {
    if (url.includes("riepilogo.ilike")) return jsonResponse([]);
    if (url.includes("/rest/v1/emendamenti?")) return jsonResponse([{ id: "camera:19:odg:9/02355/145#v1" }]);
    return jsonResponse([], { contentRange: "*/0" });
  });
  await store.search({ q: "1100", type: "all", minScore: 0, sort: "score_desc", offset: 0, limit: 30 });
  const main = calls[calls.length - 1].url;
  const query = main.slice(main.indexOf("?") + 1);
  assert.ok(!query.includes("#"), "raw # in query string would truncate it at the fragment");
  assert.ok(query.includes("or=" + encodeURIComponent('(emendamento_id.in.("camera:19:odg:9/02355/145#v1"))')));
  assert.ok(query.includes("%23v1"));
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

test("detail of a subemendamento fetches the parent row (migration 0008)", async () => {
  const { store, calls } = await makeStore((url) => {
    if (url.includes("valutazioni?id=eq.42")) {
      return jsonResponse([{
        id: 42,
        emendamenti: { numero: "2.0.1000/14", numero_padre: "2.0.1000", emendamento_padre_id: "http://dati.senato.it/emendamento/1529000" },
      }]);
    }
    if (decodeURIComponent(url).includes("emendamenti?id=eq.http://dati.senato.it/emendamento/1529000")) {
      return jsonResponse([{ id: "http://dati.senato.it/emendamento/1529000", numero: "2.0.1000", url_testo: "http://www.senato.it/show-doc?tipodoc=Emend&leg=19&id=1529000", ddl: "Ddl 1222" }]);
    }
    return okInit();
  });
  const row = await store.detail(42);
  assert.equal(row.emendamenti_padre.numero, "2.0.1000");
  const det = calls.find((c) => c.url.includes("valutazioni?id=eq.42"));
  assert.ok(det.url.includes("emendamento_padre_id"));
  assert.ok(det.url.includes("numero_padre"));
  const par = calls.find((c) => c.url.includes("emendamenti?id=eq."));
  assert.ok(par.url.includes("id=eq." + encodeURIComponent("http://dati.senato.it/emendamento/1529000")));
  assert.ok(par.url.includes("select=id,numero,url_testo,ddl&limit=1"));
});

test("without migration 0008 the parent columns are omitted and no parent is fetched", async () => {
  mockFetch((url) =>
    url.includes("select=numero_padre")
      ? jsonResponse("nope", { status: 400 })
      : url.includes("valutazioni?id=eq.42")
        ? jsonResponse([{ id: 42, emendamenti: { numero: "2.0.1000/14" } }])
        : okInit()
  );
  const store = createStore(CFG);
  const flags = await store.init();
  assert.equal(flags.parent, false);
  const calls = [];
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => { calls.push(String(url)); return origFetch(url, opts); };
  const row = await store.detail(42);
  assert.equal(row.emendamenti_padre, undefined);
  assert.ok(!calls[calls.length - 1].includes("emendamento_padre_id"));
  assert.ok(!calls.some((u) => u.includes("emendamenti?id=eq.")));
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
  assert.equal(calls.filter((c) => c.url.includes("/rest/v1/valutazioni?") && !c.url.includes("impatto_finanziario")).length, 5);
});

test("search with chamber=camera filters by camera and corrente", async () => {
  const { store, calls } = await makeStore((url) =>
    url.includes("/rest/v1/valutazioni?") ? jsonResponse([], { contentRange: "*/0" }) : okInit()
  );
  await store.search({ q: "", type: "all", chamber: "camera", minScore: 0, sort: "score_desc", offset: 0, limit: 30 });
  const url = calls[calls.length - 1].url;
  assert.ok(url.includes("emendamenti.camera=eq.camera"));
  assert.ok(url.includes("emendamenti.corrente=eq.true"));
});

test("detail select includes the financial fields when migration 0006 exists", async () => {
  const { store, calls } = await makeStore((url) =>
    url.includes("/rest/v1/valutazioni?id=") ? jsonResponse([{ id: 7 }]) : okInit()
  );
  await store.detail(7);
  const url = calls[calls.length - 1].url;
  assert.ok(url.includes("impatto_finanziario,alloca_risorse,importo_stimato_eur,motivazione_finanziaria"));
  assert.ok(url.includes("camera,esito"));
});

test("without migrations 0004-0006 the queries degrade gracefully", async () => {
  mockFetch((url) =>
    url.includes("select=camera") || url.includes("select=corrente") || url.includes("select=impatto_finanziario")
      ? jsonResponse("nope", { status: 400 })
      : okInit()
  );
  const store = createStore(CFG);
  const flags = await store.init();
  assert.deepEqual(flags, { signatories: true, signatoryUris: true, chamber: false, corrente: false, finance: false, parent: true });
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return jsonResponse([], { contentRange: "*/0" });
  };
  await store.search({ q: "", type: "all", chamber: "camera", minScore: 0, sort: "score_desc", offset: 0, limit: 30 });
  assert.ok(!calls[0].includes("corrente=eq.true"));
  assert.ok(!calls[0].includes("camera=eq.camera"));
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

test("search with excludeSubs adds numero_padre=is.null when migration 0008 exists (and to the em probe)", async () => {
  const { store, calls } = await makeStore((url) =>
    url.includes("/rest/v1/emendamenti?") ? jsonResponse([]) : jsonResponse([{ id: 1 }], { contentRange: "0-0/1" })
  );
  await store.search({ q: "", type: "all", minScore: 0, excludeSubs: true, sort: "score_desc", offset: 0, limit: 30 });
  const main = calls[calls.length - 1].url;
  assert.ok(main.includes("emendamenti.numero_padre=is.null"));
  // senza il flag il parametro non c'e'
  await store.search({ q: "", type: "all", minScore: 0, excludeSubs: false, sort: "score_desc", offset: 0, limit: 30 });
  assert.ok(!calls[calls.length - 1].url.includes("numero_padre=is.null"));
});

test("search with q + excludeSubs applies the filter also to the emendamenti probe", async () => {
  const { store, calls } = await makeStore((url) => {
    if (url.includes("riepilogo.ilike")) return jsonResponse([]);
    if (url.includes("/rest/v1/emendamenti?")) return jsonResponse([{ id: "http://dati.senato.it/emendamento/9" }]);
    return jsonResponse([], { contentRange: "*/0" });
  });
  await store.search({ q: "salute", type: "all", minScore: 0, excludeSubs: true, sort: "score_desc", offset: 0, limit: 30 });
  const emProbe = calls.find((c) => c.url.includes("/rest/v1/emendamenti?select=id&or="));
  assert.ok(emProbe.url.includes("numero_padre=is.null"));
  const main = calls[calls.length - 1].url;
  assert.ok(main.includes("emendamenti.numero_padre=is.null"));
});

test("without migration 0008 the excludeSubs param is never sent", async () => {
  mockFetch((url) =>
    url.includes("select=numero_padre")
      ? jsonResponse("nope", { status: 400 })
      : url.includes("/rest/v1/valutazioni?")
        ? jsonResponse([], { contentRange: "*/0" })
        : okInit()
  );
  const store = createStore(CFG);
  const flags = await store.init();
  assert.equal(flags.parent, false);
  const calls = [];
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => { calls.push(String(url)); return origFetch(url, opts); };
  await store.search({ q: "", type: "all", minScore: 0, excludeSubs: true, sort: "score_desc", offset: 0, limit: 30 });
  assert.ok(!calls[calls.length - 1].includes("numero_padre"));
});

test("date sorts query emendamenti as root and flip rows to the valutazione shape", async () => {
  const { store, calls } = await makeStore((url) => {
    if (url.includes("select=id,numero")) {
      return jsonResponse([{
        id: "camera:19:odg:9/03076/003#v1", numero: "9/03076/003", tipo: "E", ddl: "Ddl 3076",
        data: "2026-11-20", url_testo: "u", firmatari: "Rossi", firmatari_uri: "", camera: "camera",
        esito: null, numero_padre: null,
        valutazioni: [{ id: 5, score_totale: 8.5, riepilogo: "r", modello: "m", ts: "2026-11-21T00:00:00Z", punteggi: [{ criterio: "salute", punteggio: 7, motivazione: "x" }] }]
      }], { contentRange: "0-0/1" });
    }
    if (url.includes("/rest/v1/emendamenti?")) return okInit();
    return jsonResponse([], { contentRange: "*/0" });
  });
  const r = await store.search({ q: "", type: "E", chamber: "camera", minScore: 7, excludeSubs: true, sort: "data_desc", offset: 60, limit: 30 });
  const main = calls[calls.length - 1];
  assert.ok(main.url.startsWith("https://x.supabase.co/rest/v1/emendamenti?"));
  assert.ok(main.url.includes("order=data.desc.nullslast"));
  assert.ok(main.url.includes("select=id,numero,tipo,ddl,data,url_testo,firmatari,firmatari_uri,camera,esito,numero_padre,valutazioni!inner(id,score_totale,riepilogo,modello,ts,punteggi(criterio,punteggio,motivazione))"));
  assert.ok(main.url.includes("corrente=eq.true"));
  assert.ok(main.url.includes("tipo=eq.E"));
  assert.ok(main.url.includes("camera=eq.camera"));
  assert.ok(main.url.includes("numero_padre=is.null"));
  assert.ok(main.url.includes("valutazioni.score_totale=gte.7"));
  assert.ok(main.url.includes("limit=30&offset=60"));
  assert.ok(!main.url.includes("emendamenti.corrente"));
  assert.equal(main.headers.Prefer, "count=exact");
  assert.equal(r.total, 1);
  assert.equal(r.items.length, 1);
  assert.equal(r.items[0].id, 5);
  assert.equal(r.items[0].score_totale, 8.5);
  assert.equal(r.items[0].emendamenti.numero, "9/03076/003");
  assert.equal(r.items[0].emendamenti.camera, "camera");
  assert.deepEqual(r.items[0].punteggi, [{ criterio: "salute", punteggio: 7, motivazione: "x" }]);
  assert.ok(!("valutazioni" in r.items[0].emendamenti));
});

test("data_asc uses data.asc.nullslast on the emendamenti root and omits unset filters", async () => {
  const { store, calls } = await makeStore((url) =>
    url.includes("select=id,numero") ? jsonResponse([], { contentRange: "*/0" }) : okInit()
  );
  await store.search({ q: "", type: "all", chamber: "all", minScore: 0, excludeSubs: false, sort: "data_asc", offset: 0, limit: 30 });
  const main = calls[calls.length - 1];
  assert.ok(main.url.startsWith("https://x.supabase.co/rest/v1/emendamenti?"));
  assert.ok(main.url.includes("order=data.asc.nullslast"));
  assert.ok(!main.url.includes("tipo=eq."));
  assert.ok(!main.url.includes("camera=eq."));
  assert.ok(!main.url.includes("numero_padre=is.null"));
  assert.ok(!main.url.includes("score_totale=gte."));
});

test("search with q + date sort flips the or= clause onto the emendamenti root (no raw # in query)", async () => {
  const { store, calls } = await makeStore((url) => {
    if (url.includes("riepilogo.ilike")) return jsonResponse([{ id: 5 }]);
    if (url.includes("/rest/v1/emendamenti?select=id&or=")) return jsonResponse([{ id: "camera:19:odg:9/02355/145#v1" }]);
    return jsonResponse([], { contentRange: "*/0" });
  });
  await store.search({ q: "1100", type: "all", minScore: 0, sort: "data_desc", offset: 0, limit: 30 });
  const main = calls[calls.length - 1];
  assert.ok(main.url.startsWith("https://x.supabase.co/rest/v1/emendamenti?"));
  const query = main.url.slice(main.url.indexOf("?") + 1);
  assert.ok(!query.includes("#"), "raw # in query string would truncate it at the fragment");
  assert.ok(query.includes("or=" + encodeURIComponent('(id.in.("camera:19:odg:9/02355/145#v1"),valutazioni.id.in.(5))')));
  assert.ok(query.includes("%23v1"));
});
