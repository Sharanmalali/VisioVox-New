"""Write the paper's data tables as LaTeX fragments (paper/tables/*.tex).

Generated rather than typed, so a table cannot drift from the logs it reports.
Run after make_figures.py, which writes data/summary.json.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
DATA = HERE / "data"
OUT = HERE / "tables"
OUT.mkdir(exist_ok=True)

summary = json.loads((DATA / "summary.json").read_text())


def signed(x: float, nd: int = 2) -> str:
    return f"${'+' if x >= 0 else '-'}{abs(x):.{nd}f}$"


# ---- product condition -----------------------------------------------------
prod = summary["product"]
rows = [
    ("C1", "Libri2Mix", "C1"),
    ("C3", "+ room sim.", "C3"),
    ("C2 v1", "VoxCeleb2, 8k", "C2 v1"),
    ("C2 v2", "VoxCeleb2, 12k", "C2 v2"),
    ("C2 v3", "blended, 4.5k", "C2 v3"),
]


def cell(r: dict[str, float]) -> str:
    return f"{signed(r['mean'])} & $[{r['lo']:.2f}, {r['hi']:.2f}]$"


lines = []
for label, data, model in rows:
    r2, r3 = prod[model]["2"], prod[model]["3"]
    best = model == "C3"
    c2 = cell(r2)
    c3 = cell(r3)
    if best:
        c2 = c2.replace(signed(r2["mean"]), f"$\\mathbf{{{r2['mean']:+.2f}}}$")
        c3 = c3.replace(signed(r3["mean"]), f"$\\mathbf{{{r3['mean']:+.2f}}}$")
    lines.append(f"{label} & {data} & {c2} & {c3} \\\\")
mix2, mix3 = prod["C3"]["2"]["mix"], prod["C3"]["3"]["mix"]
n2, n3 = int(prod["C3"]["2"]["n"]), int(prod["C3"]["3"]["n"])
# Placeholders rather than an f-string: the LaTeX braces stay readable.
header = (
    (
        "\\begin{tabular}{@{}llrcrc@{}}\n\\toprule\n"
        " & & \\multicolumn{2}{c}{2 speakers ($n$=N2)} & \\multicolumn{2}{c}{3 speakers ($n$=N3)} \\\\\n"
        "\\cmidrule(lr){3-4}\\cmidrule(l){5-6}\n"
        "Model & Data & SI-SDRi & 95\\% CI & SI-SDRi & 95\\% CI \\\\\n\\midrule\n"
    )
    .replace("N2", str(n2))
    .replace("N3", str(n3))
)
(OUT / "product.tex").write_text(
    header
    + "\n".join(lines)
    + "\n\\midrule\n"
    + f"\\multicolumn{{2}}{{@{{}}l}}{{Mixture SI-SDR (input)}} & {signed(mix2)} & & {signed(mix3)} & \\\\\n"
    + "\\bottomrule\n\\end{tabular}\n"
)

# ---- AV-MossFormer2 side-by-side test -----------------------------------------
moss = summary["moss_rows"]
lines = []
for r in moss:
    lines.append(
        f"{r['pair'].replace('pair', 'P')} & {r['speaker']} & {signed(r['mix_si_sdr'])} & "
        f"{signed(r['out_si_sdr'])} & {signed(r['si_sdri'])} & {signed(r['vs_other_speaker'], 1)} \\\\"
    )
mean = np.mean([r["si_sdri"] for r in moss])
(OUT / "avmossformer2.tex").write_text(
    "\\begin{tabular}{@{}llrrrr@{}}\n\\toprule\n"
    "Pair & Side & Mix & Output & SI-SDRi & Leak \\\\\n\\midrule\n"
    + "\n".join(lines)
    + f"\n\\midrule\nMean & & & & $\\mathbf{{{mean:+.2f}}}$ & \\\\\n\\bottomrule\n\\end{{tabular}}\n"
)

# ---- production latency ----------------------------------------------------------
jobs: list[dict[str, float]] = []
with (DATA / "production-jobs.csv").open() as f:
    for raw in csv.DictReader(f):
        if raw.get("dur_ms", "").strip().isdigit():
            jobs.append(
                {k: float(v) if v not in ("", None) else float("nan") for k, v in raw.items()}
            )
by_dur = {j["dur_ms"]: j for j in jobs if j["width"] == j["width"] and j["width"]}
for j in jobs:
    if not (j["width"] == j["width"] and j["width"]) and j["dur_ms"] in by_dur:
        j["width"], j["height"] = by_dur[j["dur_ms"]]["width"], by_dur[j["dur_ms"]]["height"]
groups: dict[tuple[float, float, float], list[dict[str, float]]] = {}
for j in jobs:
    groups.setdefault((j["dur_ms"], j["width"], j["height"]), []).append(j)
lines = []
for key in sorted(groups):
    g = groups[key]
    dur = key[0] / 1000
    wall = np.mean([x["wall_s"] for x in g])
    s5 = np.mean([x["s5"] for x in g]) / 1000
    s7 = np.mean([x["s7"] for x in g]) / 1000
    lines.append(
        f"{dur:.2f} & {int(key[1])}$\\times${int(key[2])} & {len(g)} & {s5:.1f} & {s7:.1f} & {wall:.1f} & {wall / dur:.2f} \\\\"
    )
rtf = [x["wall_s"] / (x["dur_ms"] / 1000) for x in jobs]
(OUT / "latency.tex").write_text(
    "\\begin{tabular}{@{}rrrrrrr@{}}\n\\toprule\n"
    "Dur. (s) & Resolution & $n$ & S5 (s) & S7 (s) & Total (s) & RTF \\\\\n\\midrule\n"
    + "\n".join(lines)
    + f"\n\\midrule\nAll & & {len(jobs)} & & & & {np.mean(rtf):.2f} \\\\\n\\bottomrule\n\\end{{tabular}}\n"
)
print("tables written:", sorted(p.name for p in OUT.iterdir()))
print("RTF mean", np.mean(rtf), "median", np.median(rtf))
