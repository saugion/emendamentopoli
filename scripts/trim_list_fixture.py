"""Deterministic trimmer: keeps the page head, the first 5 rows and the rows whose ids are given, and the page tail."""
import re
import sys

src, dst, keep = sys.argv[1], sys.argv[2], set(sys.argv[3].split(","))
s = open(src, encoding="utf-8-sig").read()
rows = list(re.finditer(r'\s*<tr class="normale" id="tr\.id\.(.+?)\.position\.\d{8}\.\d+">.*?</tr>', s, re.S))
head, tail = s[: rows[0].start()], s[rows[-1].end():]
kept = [m.group(0) for i, m in enumerate(rows) if i < 5 or m.group(1) + "." in keep]
open(dst, "w", encoding="utf-8").write("\ufeff" + head + "".join(kept) + tail)
print(dst, len(kept))
