"""Stratified 200-sample of the relevance pre-filter, for human labelling.

Reads SUPABASE_URL/SUPABASE_KEY, reads all current rows with text and a computed
rilevante_spesa, picks a deterministic pseudo-random sample (md5(id|emendamentopoli-2026))
per stratum (camera_true 50, camera_false 75, senato_true 25, senato_false 50) and writes
docs/validation/rilevanza_campione.csv (etichetta_umana left empty for the human) and
docs/validation/rilevanza_popolazione.json (stratum sizes).
"""

import csv
import hashlib
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workflows" / "src"))

from amendments_worker.sources.supabase import select_all  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "validation"
STRATA = [("camera_true", 50), ("camera_false", 75), ("senato_true", 25), ("senato_false", 50)]


async def main() -> None:
    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_KEY"]
    async with httpx.AsyncClient(timeout=90.0) as client:
        rows = await select_all(
            client,
            "emendamenti",
            "id,camera,tipo,numero,ddl,testo,rilevante_spesa,rilevanza_motivo",
            {"corrente": "is.true", "testo": "not.is.null", "rilevante_spesa": "not.is.null"},
        )
    buckets: dict[str, list[dict]] = {"camera_true": [], "camera_false": [], "senato_true": [], "senato_false": []}
    for r in rows:
        strato = f"{r['camera']}_{'true' if r['rilevante_spesa'] else 'false'}"
        if strato in buckets:
            buckets[strato].append(r)
    population = {s: len(buckets[s]) for s, _ in STRATA}
    OUT.mkdir(parents=True, exist_ok=True)
    csv_path = OUT / "rilevanza_campione.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(["strato", "id", "camera", "tipo", "numero", "ddl", "predetto", "motivi", "testo", "etichetta_umana"])
        total = 0
        for strato, n in STRATA:
            rows_s = sorted(
                buckets[strato],
                key=lambda r: hashlib.md5((r["id"] + "|emendamentopoli-2026").encode()).hexdigest(),
            )
            for r in rows_s[:n]:
                w.writerow([
                    strato, r["id"], r["camera"], r["tipo"], r["numero"], r["ddl"],
                    str(bool(r["rilevante_spesa"])).lower(), ";".join(r.get("rilevanza_motivo") or []),
                    r["testo"], "",
                ])
                total += 1
    (OUT / "rilevanza_popolazione.json").write_text(
        json.dumps(population, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {csv_path} ({total} rows) and {OUT / 'rilevanza_popolazione.json'} {population}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
