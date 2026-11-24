// Store module for the web app: every PostgREST detail lives here (headers,
// query building, sanitization, pagination metadata, error mapping, column
// feature detection). The UI only sees createStore({url, key}) and calls
// init / search / detail / stats. Zero dependencies, ES module.

export function sanitizeQuery(q) {
  return String(q || "").replace(/[%_*,\"'()\\\\.]/g, " ").replace(/\s+/g, " ").trim();
}

export function createStore(config) {
  var base = String(config.url || "").replace(/\/+$/, "");
  var key = config.key || "";
  var hasSignatories = false;
  var hasSignatoryUris = false;
  var hasChamber = false;   // migration 0005 (camera, esito)
  var hasCorrente = false;  // migration 0004 (versions)
  var hasFinance = false;   // migration 0006 (impatto_finanziario)

  function api(path, count) {
    var h = { apikey: key, Authorization: "Bearer " + key };
    if (count) h.Prefer = "count=exact";
    return fetch(base + "/rest/v1/" + path, { headers: h }).then(function (res) {
      return res.text().then(function (text) {
        if (!res.ok) {
          var msg = text;
          try { msg = JSON.parse(text).message || text; } catch (e) {}
          var err = new Error("HTTP " + res.status + " " + String(msg).slice(0, 160));
          err.status = res.status;
          throw err;
        }
        var total = null;
        var range = res.headers.get("content-range");
        if (range) { var t = range.split("/")[1]; if (t && t !== "*") total = parseInt(t, 10); }
        return { data: JSON.parse(text || "[]"), total: total };
      });
    });
  }

  function emExtra() { return (hasSignatories ? ",firmatari" : "") + (hasSignatoryUris ? ",firmatari_uri" : "") + (hasChamber ? ",camera,esito" : ""); }
  function selList() {
    return "id,score_totale,riepilogo,modello,ts,emendamenti!inner(numero,tipo,ddl,data,url_testo" + emExtra() + "),punteggi(criterio,punteggio,motivazione)";
  }
  function selDetail() {
    return "id,score_totale,riepilogo,modello,ts" + (hasFinance ? ",impatto_finanziario,alloca_risorse,importo_stimato_eur,motivazione_finanziaria" : "") + ",emendamenti!inner(id,numero,tipo,ddl,data,testo,url_testo,atto_uri,sede_commissione" + emExtra() + "),punteggi(criterio,punteggio,motivazione)";
  }
  function orderClause(sort) {
    if (sort === "score_asc") return "score_totale.asc.nullslast";
    if (sort === "data_desc") return "emendamenti(data).desc.nullslast";
    if (sort === "data_asc") return "emendamenti(data).asc.nullslast";
    return "score_totale.desc.nullslast";
  }
  function baseParams(o) {
    var p = ["select=" + selList(), "order=" + orderClause(o.sort), "limit=" + o.limit, "offset=" + o.offset];
    if (o.minScore > 0) p.push("score_totale=gte." + o.minScore);
    if (o.type !== "all") p.push("emendamenti.tipo=eq." + o.type);
    if (hasCorrente) p.push("emendamenti.corrente=eq.true");
    if (hasChamber && o.chamber && o.chamber !== "all") p.push("emendamenti.camera=eq." + o.chamber);
    return p;
  }
  function searchOrClause(qs) {
    var pat = "*" + qs + "*";
    var emOr = ["numero.ilike." + pat, "ddl.ilike." + pat, "testo.ilike." + pat];
    if (hasSignatories) emOr.push("firmatari.ilike." + pat);
    return Promise.all([
      api("valutazioni?select=id&or=(riepilogo.ilike." + pat + ")&limit=1000"),
      api("emendamenti?select=id&or=(" + emOr.join(",") + ")" + (hasCorrente ? "&corrente=eq.true" : "") + "&limit=1000")
    ]).then(function (rs) {
      var valIds = rs[0].data.map(function (r) { return r.id; });
      var emIds = rs[1].data.map(function (r) { return '"' + String(r.id).replace(/"/g, "") + '"'; });
      var parts = [];
      if (valIds.length) parts.push("id.in.(" + valIds.join(",") + ")");
      if (emIds.length) parts.push("emendamento_id.in.(" + emIds.join(",") + ")");
      return parts.length ? "or=(" + parts.join(",") + ")" : null;
    });
  }

  return {
    // probes whether the firmatari / firmatari_uri columns exist (migrations 0002/0003)
    init: function () {
      return Promise.all([
        api("emendamenti?select=firmatari&limit=1").catch(function () { return null; }),
        api("emendamenti?select=firmatari_uri&limit=1").catch(function () { return null; }),
        api("emendamenti?select=camera&limit=1").catch(function () { return null; }),
        api("emendamenti?select=corrente&limit=1").catch(function () { return null; }),
        api("valutazioni?select=impatto_finanziario&limit=1").catch(function () { return null; })
      ]).then(function (rs) {
        hasSignatories = !!rs[0];
        hasSignatoryUris = !!rs[1];
        hasChamber = !!rs[2];
        hasCorrente = !!rs[3];
        hasFinance = !!rs[4];
        return {
          signatories: hasSignatories,
          signatoryUris: hasSignatoryUris,
          chamber: hasChamber,
          corrente: hasCorrente,
          finance: hasFinance,
        };
      });
    },

    // o: { q, type, minScore, sort, offset, limit } -> { items, total }
    search: function (o) {
      var qs = sanitizeQuery(o.q);
      var fetchPage = function (extra) {
        var params = baseParams(o);
        if (extra) params.push(extra);
        return api("valutazioni?" + params.join("&"), true).then(function (r) {
          return { items: r.data, total: r.total };
        }).catch(function (err) {
          if (err && err.status === 416) return { items: [], total: null };
          throw err;
        });
      };
      if (!qs) return fetchPage(null);
      return searchOrClause(qs).catch(function () {
        throw new Error("Ricerca non riuscita");
      }).then(function (orClause) {
        if (!orClause) return { items: [], total: 0 };
        return fetchPage(orClause);
      });
    },

    detail: function (id) {
      return api("valutazioni?id=eq." + id + "&select=" + selDetail() + "&limit=1").then(function (r) {
        if (!r.data.length) throw new Error("Valutazione non trovata");
        return r.data[0];
      });
    },

    stats: function () {
      return Promise.all([
        api(hasCorrente ? "valutazioni?select=id,emendamenti!inner(corrente)&emendamenti.corrente=eq.true&limit=0" : "valutazioni?select=id&limit=0", true),
        api(hasCorrente ? "valutazioni?select=id,emendamenti!inner(corrente)&emendamenti.corrente=eq.true&score_totale=gte.7&limit=0" : "valutazioni?select=id&score_totale=gte.7&limit=0", true),
        api("valutazioni?select=id,emendamenti!inner(tipo)&emendamenti.tipo=eq.E" + (hasCorrente ? "&emendamenti.corrente=eq.true" : "") + "&limit=0", true),
        api("valutazioni?select=id,emendamenti!inner(tipo)&emendamenti.tipo=eq.G" + (hasCorrente ? "&emendamenti.corrente=eq.true" : "") + "&limit=0", true),
        api("valutazioni?select=modello,ts&order=ts.desc.nullslast&limit=1")
      ]).then(function (rs) {
        return {
          total: rs[0].total || 0,
          hi: rs[1].total || 0,
          e: rs[2].total || 0,
          g: rs[3].total || 0,
          last: rs[4].data[0] || null
        };
      });
    }
  };
}
