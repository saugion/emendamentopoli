"""In-memory fakes of the Supabase tables for workflow-level tests.

FakeSupabase keeps dict tables; upsert merges by the conflict columns;
valutazioni enforces uniqueness of (emendamento_id, rubrica_versione) and ignores
duplicates; rpc_ricalcola_versioni reproduces the SQL function with the same
ordering (ordine_versione, id).
"""


class FakeSupabase:
    def __init__(self) -> None:
        self.emendamenti: dict[str, dict] = {}
        self.valutazioni: dict[int, dict] = {}
        self.punteggi: list[dict] = []
        self.cursori: dict[str, str] = {}
        self.atti: dict[str, dict] = {}
        self.liste: dict[str, dict] = {}
        self._next_valutazione_id = 1
        self.rpc_calls: list[list[str]] = []

    def upsert_emendamenti(self, rows: list[dict]) -> None:
        for r in rows:
            self.emendamenti[r["id"]] = {**self.emendamenti.get(r["id"], {}), **r}

    def upsert(self, table: str, rows: list[dict], on_conflict: str) -> None:
        conflict_cols = [c for c in on_conflict.split(",") if c]
        store = getattr(self, table if table != "camera_liste_emendamenti" else "liste")
        for r in rows:
            key = tuple(r.get(c) for c in conflict_cols)
            existing = None
            if len(conflict_cols) == 1:
                existing = store.get(key[0])
            if existing is not None:
                existing.update(r)
            else:
                if len(conflict_cols) == 1:
                    store[key[0]] = dict(r)
                else:
                    store[r.get("id") or r.get("url") or str(key)] = dict(r)

    def insert_valutazioni(self, rows: list[dict]) -> list[dict]:
        """Insert with the (emendamento_id, rubrica_versione) uniqueness; returns inserted rows."""
        inserted: list[dict] = []
        for r in rows:
            key = (r["emendamento_id"], r.get("rubrica_versione", 1))
            if any(
                (v["emendamento_id"], v.get("rubrica_versione", 1)) == key
                for v in self.valutazioni.values()
            ):
                continue
            vid = self._next_valutazione_id
            self._next_valutazione_id += 1
            v = {"id": vid, **r}
            self.valutazioni[vid] = v
            inserted.append(v)
        return inserted

    def add_punteggi(self, valutazione_id: int, rows: list[dict]) -> None:
        self.punteggi.extend({"valutazione_id": valutazione_id, **r} for r in rows)

    def rpc_ricalcola_versioni(self, keys: list[str]) -> int:
        """Python twin of the ricalcola_versioni SQL function: within each chiave_naturale,
        order by (ordine_versione, id); versione = 1..n, corrente = last, superato_da = next id."""
        self.rpc_calls.append(list(keys))
        n = 0
        groups: dict[str, list[dict]] = {}
        for row in self.emendamenti.values():
            if row.get("chiave_naturale") in set(keys):
                groups.setdefault(row["chiave_naturale"], []).append(row)
        for rows in groups.values():
            rows.sort(key=lambda r: (r.get("ordine_versione", 0), r["id"]))
            tot = len(rows)
            for i, r in enumerate(rows):
                v = i + 1
                corrente = i == tot - 1
                nxt = None if corrente else rows[i + 1]["id"]
                if (
                    r.get("versione") != v
                    or r.get("corrente") != corrente
                    or r.get("superato_da") != nxt
                ):
                    n += 1
                r["versione"] = v
                r["corrente"] = corrente
                r["superato_da"] = nxt
        return n
