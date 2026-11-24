"""Metrics of the relevance pre-filter on the labelled sample.

Input: docs/validation/rilevanza_campione.csv (with etichetta_umana filled) and
docs/validation/rilevanza_popolazione.json. For each stratum s with population N_s:
p_s = share of label 1, q_s = share of label 1 AND predicted 1, r_s = share predicted 1.

recall    = sum_s N_s*q_s / sum_s N_s*p_s     (overall: all strata; per chamber: only its strata)
precision = sum_s N_s*q_s / sum_s N_s*r_s

Pass threshold: recall >= 0.90 overall AND per chamber under R1. Prints the three
recalls and precisions with 3 decimals. Usage: python3 scripts/metrics_rilevanza.py
"""

import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VAL = REPO / "docs" / "validation"
CHAMBERS = {"camera": ("camera_true", "camera_false"), "senato": ("senato_true", "senato_false")}


def main() -> None:
    with (VAL / "rilevanza_campione.csv").open(encoding="utf-8") as f:
        sample = list(csv.DictReader(f))
    population = json.loads((VAL / "rilevanza_popolazione.json").read_text(encoding="utf-8"))

    def stats(strata: tuple[str, ...]) -> tuple[float, float, float]:
        num_r = num_p = den_r = den_p = 0.0
        for s in strata:
            rows = [r for r in sample if r["strato"] == s]
            n = len(rows)
            if not n:
                continue
            n_s = population[s]
            p = sum(1 for r in rows if r["etichetta_umana"] == "1") / n
            q = sum(1 for r in rows if r["etichetta_umana"] == "1" and r["predetto"] == "true") / n
            r_ = sum(1 for r in rows if r["predetto"] == "true") / n
            num_r += n_s * q
            num_p += n_s * q
            den_r += n_s * p
            den_p += n_s * r_
        recall = num_r / den_r if den_r else 0.0
        precision = num_p / den_p if den_p else 0.0
        return recall, precision, num_p

    all_strata = tuple(population.keys())
    overall = stats(all_strata)
    print(f"overall   recall={overall[0]:.3f} precision={overall[1]:.3f}")
    ok = overall[0] >= 0.90
    for chamber, strata in CHAMBERS.items():
        recall, precision, _ = stats(strata)
        print(f"{chamber:8s} recall={recall:.3f} precision={precision:.3f}")
        ok = ok and recall >= 0.90
    print("PASS" if ok else "FAIL (recall < 0.90: apply fallback R2/R3 per the spec)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
