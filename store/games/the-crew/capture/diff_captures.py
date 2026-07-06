"""Diff two capture CSVs (path,size,mtime,sha256). Usage:
python diff_captures.py baseline.csv post.csv
WebView2 browser-cache noise is excluded."""
import csv
import sys

NOISE = ("\\Launcher\\TCULauncher.exe.WebView2",)


def load(path):
    rows = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            p = row["path"]
            if any(p.startswith(n) for n in NOISE):
                continue
            rows[p] = (row["size"], row["mtime"], row["sha256"])
    return rows


before, after = load(sys.argv[1]), load(sys.argv[2])

added = sorted(set(after) - set(before))
removed = sorted(set(before) - set(after))
changed = sorted(p for p in set(before) & set(after) if before[p] != after[p])

print(f"ADDED ({len(added)}):")
for p in added:
    print(f"  + {after[p][0]:>12}  {p}")
print(f"\nREMOVED ({len(removed)}):")
for p in removed:
    print(f"  - {p}")
print(f"\nCHANGED ({len(changed)}):")
for p in changed:
    b, a = before[p], after[p]
    what = "content" if (b[2] and a[2] and b[2] != a[2]) else "size/mtime"
    print(f"  ~ {p}  [{what}] {b[0]} -> {a[0]} bytes")
