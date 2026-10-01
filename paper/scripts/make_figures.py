"""Generate every data figure in the paper from the files in paper/data/.

Nothing here is typed in by hand except the Phase-1 baseline table, which is
transcribed from docs/27-phase1-baseline-report.md (the output of
scripts/measure_permutation_full.py and scripts/run_baseline.py); its source is
noted where it is used.

    python paper/scripts/make_figures.py
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure

HERE = Path(__file__).resolve().parents[1]
DATA = HERE / "data"
OUT = HERE / "figures"
OUT.mkdir(exist_ok=True)

# IEEE column is 3.5 in; text width 7.16 in. Fonts match the 10 pt body in
# Times, scaled for figures (8 pt).
COL, FULL = 3.5, 7.16
plt.rcParams.update(
    {
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Nimbus Roman", "Nimbus Roman No9 L", "DejaVu Serif"],
        "mathtext.fontset": "stix",
        "font.size": 8,
        "axes.titlesize": 8,
        "axes.labelsize": 8,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "legend.fontsize": 7,
        "axes.linewidth": 0.6,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "lines.linewidth": 1.1,
        "grid.linewidth": 0.4,
        "grid.alpha": 0.35,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.02,
        "pdf.fonttype": 42,
    }
)
# Okabe-Ito, colour-blind safe.
C = {
    "blue": "#0072B2",
    "orange": "#E69F00",
    "green": "#009E73",
    "red": "#D55E00",
    "purple": "#CC79A7",
    "sky": "#56B4E9",
    "grey": "#7f7f7f",
    "black": "#000000",
}


def save(fig: Figure, name: str) -> None:
    fig.savefig(OUT / f"{name}.pdf")
    fig.savefig(OUT / f"{name}.png", dpi=300)
    plt.close(fig)
    print("wrote", name)


# --------------------------------------------------------------------------
# 1. Training curves (validation SI-SDRi vs step), from runs/*/log.json
# --------------------------------------------------------------------------
def load_log(name: str) -> list[dict[str, float]]:
    data: list[dict[str, float]] = json.loads((DATA / f"train-{name}.json").read_text())
    return data


def fig_training() -> None:
    c1, c3, c2 = load_log("c1"), load_log("c3"), load_log("c2-v2")
    fig, axes = plt.subplots(1, 3, figsize=(FULL, 1.75), constrained_layout=True)

    ax = axes[0]
    x = [r["step"] / 1000 for r in c1]
    ax.plot(x, [r["val_si_sdri"] for r in c1], color=C["blue"])
    best = max(c1, key=lambda r: r["val_si_sdri"])
    ax.plot(best["step"] / 1000, best["val_si_sdri"], "o", ms=3, color=C["blue"])
    ax.set_title("(a) C1: Libri2Mix, anechoic")
    ax.set_xlabel("step ($\\times10^3$)")
    ax.set_ylabel("val. SI-SDRi (dB)")

    ax = axes[1]
    x = [r["step"] / 1000 for r in c3]
    ax.plot(x, [r["val_si_sdri"] for r in c3], color=C["green"])
    best = max(c3, key=lambda r: r["val_si_sdri"])
    ax.plot(best["step"] / 1000, best["val_si_sdri"], "o", ms=3, color=C["green"])
    ax.annotate(
        f"{best['val_si_sdri']:.2f} dB",
        (best["step"] / 1000, best["val_si_sdri"]),
        xytext=(-34, -14),
        textcoords="offset points",
        fontsize=6.5,
    )
    ax.set_title("(b) C3: + room simulation (init. C1)")
    ax.set_xlabel("step ($\\times10^3$)")

    ax = axes[2]
    x = [r["step"] / 1000 for r in c2]
    ax.plot(x, [r["val_si_sdri"] for r in c2], color=C["purple"], label="audio-visual")
    ax.plot(x, [r["audio_only"] for r in c2], color=C["orange"], ls="--", label="audio cue only")
    ax.plot(x, [r["visual_only"] for r in c2], color=C["sky"], ls=":", label="visual cue only")
    ax.set_title("(c) C2 v2: VoxCeleb2 AV (init. C3)")
    ax.set_xlabel("step ($\\times10^3$)")
    ax.legend(frameon=False, loc="lower right")
    for a in axes:
        a.grid(True)
    save(fig, "training_curves")


# --------------------------------------------------------------------------
# 2. Product-condition evaluation, from eval-product.log / eval-diagnostic.log
# --------------------------------------------------------------------------
ROW = re.compile(
    r"(?P<spk>\d) speakers\s+n=(?P<n>\d+).*?mixture\s+(?P<mix>[-+]?[\d.]+) dB\s*\n\s*"
    r"SI-SDRi\s+(?P<mean>[-+]?[\d.]+) dB\s+\[(?P<lo>[-+]?[\d.]+), (?P<hi>[-+]?[\d.]+)\]"
    r"\s+median\s+(?P<median>[-+]?[\d.]+)",
    re.S,
)


def parse_product() -> dict[str, dict[int, dict[str, float]]]:
    text = (DATA / "eval-product.log").read_text() + (DATA / "eval-diagnostic.log").read_text()
    blocks = re.split(r"#{8} ", text)[1:]
    out: dict[str, dict[int, dict[str, float]]] = {}
    for b in blocks:
        label = b.split("(")[0].strip()  # "C2 v2", "C3", "C2 v1", "C1"
        rows = {}
        for m in ROW.finditer(b):
            rows[int(m["spk"])] = {
                k: float(m[k]) for k in ("n", "mix", "mean", "lo", "hi", "median")
            }
        out[label] = rows
    return out


def fig_product() -> dict[str, dict[int, dict[str, float]]]:
    prod = parse_product()
    order = ["C1", "C3", "C2 v1", "C2 v2"]
    colours = [C["grey"], C["green"], C["sky"], C["purple"]]
    # In-domain validation score each model was selected on (its own dev set).
    indomain = {
        "C1": max(r["val_si_sdri"] for r in load_log("c1")),
        "C3": max(r["val_si_sdri"] for r in load_log("c3")),
        "C2 v1": max(r["val_si_sdri"] for r in load_log("c2-v1-noPretrain")),
        "C2 v2": max(r["val_si_sdri"] for r in load_log("c2-v2")),
    }
    fig, (a, b) = plt.subplots(
        1, 2, figsize=(FULL, 1.95), constrained_layout=True, gridspec_kw={"width_ratios": [1.35, 1]}
    )
    width = 0.19
    xs = np.arange(2)
    for i, (name, col) in enumerate(zip(order, colours, strict=True)):
        means = [prod[name][k]["mean"] for k in (2, 3)]
        lo = [prod[name][k]["mean"] - prod[name][k]["lo"] for k in (2, 3)]
        hi = [prod[name][k]["hi"] - prod[name][k]["mean"] for k in (2, 3)]
        a.bar(
            xs + (i - 1.5) * width,
            means,
            width,
            yerr=[lo, hi],
            color=col,
            edgecolor="black",
            linewidth=0.4,
            error_kw={"elinewidth": 0.6, "capsize": 1.8},
            label=name,
        )
    a.axhline(0, color="black", lw=0.6)
    a.set_xticks(xs, ["2 speakers ($n$=181)", "3 speakers ($n$=200)"])
    a.set_ylabel("SI-SDRi (dB), 95% CI")
    a.legend(frameon=False, ncol=4, loc="upper right", columnspacing=0.8, handlelength=1.2)
    a.set_ylim(-5.5, 11.5)
    a.grid(True, axis="y")
    a.set_title("(a) Product condition (reverberant, verified overlap)")

    for name, col in zip(order, colours, strict=True):
        b.plot(indomain[name], prod[name][2]["mean"], "o", ms=5, color=col, mec="black", mew=0.4)
        b.annotate(
            name,
            (indomain[name], prod[name][2]["mean"]),
            xytext=(5, -2),
            textcoords="offset points",
            fontsize=7,
        )
    b.axhline(0, color="black", lw=0.6)
    b.set_xlabel("in-domain validation SI-SDRi (dB)")
    b.set_ylabel("product SI-SDRi, 2 spk (dB)")
    b.set_xlim(7.5, 12.6)
    b.set_ylim(-3.5, 10)
    b.grid(True)
    b.set_title("(b) In-domain vs. product score")
    save(fig, "product_condition")
    return prod


# --------------------------------------------------------------------------
# 3. Phase-1 baseline: blind separation + naive stitching on AMI.
#    Transcribed from docs/27 §2-§3.
# --------------------------------------------------------------------------
def fig_baseline() -> None:
    meetings = ["ES\n2002a", "IS\n1000a", "TS\n3003a"]
    rate = [23.9, 34.7, 28.4]
    ci = [5.2, 4.5, 4.3]
    naive = [-17.14, -1.47, -13.48]
    oracle = [-3.20, 8.52, 2.34]
    fig, (a, b) = plt.subplots(1, 2, figsize=(COL, 1.6), constrained_layout=True)
    a.bar(
        meetings,
        rate,
        yerr=ci,
        color=C["red"],
        edgecolor="black",
        linewidth=0.4,
        error_kw={"elinewidth": 0.6, "capsize": 2},
    )
    a.axhline(29.8, color="black", ls="--", lw=0.6)
    a.text(-0.45, 41.5, "pooled: 29.8% (dashed)", ha="left", fontsize=6.3)
    a.set_ylabel("identity flips (%)")
    a.set_ylim(0, 45)
    a.set_title("(a) Permutation rate")
    a.tick_params(axis="x", labelsize=6.5)
    x = np.arange(3)
    a.grid(True, axis="y")
    b.bar(x - 0.19, naive, 0.38, color=C["orange"], edgecolor="black", linewidth=0.4, label="naive")
    b.bar(x + 0.19, oracle, 0.38, color=C["blue"], edgecolor="black", linewidth=0.4, label="oracle")
    b.axhline(0, color="black", lw=0.6)
    b.set_xticks(x, meetings)
    b.tick_params(axis="x", labelsize=6.5)
    b.set_ylabel("SI-SDRi (dB)")
    b.set_title("(b) Cost of stitching")
    b.legend(frameon=False, loc="lower left", handlelength=1)
    b.grid(True, axis="y")
    save(fig, "baseline_permutation")


# --------------------------------------------------------------------------
# 4. Production latency, from the jobs/job_stages tables (production-jobs.csv)
# --------------------------------------------------------------------------
STAGES = [
    ("s0", "Ingest (S0)"),
    ("s5", "AV-TSE (S5)"),
    ("s3", "Fuse (S3)"),
    ("s7", "Captions (S7)"),
    ("s9", "Package (S9)"),
]


def fig_latency() -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    with (DATA / "production-jobs.csv").open() as f:
        for raw in csv.DictReader(f):
            if not raw.get("dur_ms") or not raw["dur_ms"].strip().isdigit():
                continue
            rows.append(
                {k: float(v) if v not in ("", None) else float("nan") for k, v in raw.items()}
            )
    by_dur = {r["dur_ms"]: r for r in rows if r["width"] == r["width"] and r["width"]}
    for r in rows:
        if not (r["width"] == r["width"] and r["width"]) and r["dur_ms"] in by_dur:
            r["width"], r["height"] = by_dur[r["dur_ms"]]["width"], by_dur[r["dur_ms"]]["height"]
    fig, (a, b) = plt.subplots(
        1, 2, figsize=(FULL, 2.15), constrained_layout=True, gridspec_kw={"width_ratios": [1, 1.25]}
    )
    d = np.array([r["dur_ms"] / 1000 for r in rows])
    w = np.array([r["wall_s"] for r in rows])
    px = np.array([(r["width"] or 0) * (r["height"] or 0) for r in rows])
    hd = px > 1.5e6
    a.scatter(
        d[~hd],
        w[~hd],
        s=12,
        color=C["blue"],
        edgecolor="black",
        linewidth=0.3,
        label="$\\leq$ 1280$\\times$720",
        zorder=3,
    )
    a.scatter(
        d[hd],
        w[hd],
        s=12,
        color=C["red"],
        marker="s",
        edgecolor="black",
        linewidth=0.3,
        label="1080p and above",
        zorder=3,
    )
    xx = np.linspace(0, 35, 10)
    for slope, ls in ((1, ":"), (2, "--"), (4, "-.")):
        a.plot(xx, slope * xx, color=C["grey"], ls=ls, lw=0.7)
        # Each label sits just right of / below its line, in empty space.
        xl, yl = {4: (24.6, 95.0), 2: (26.5, 47.0), 1: (8.5, 3.5)}[slope]
        a.text(xl, yl, f"RTF {slope}", fontsize=6, ha="left", va="center", color=C["grey"])
    a.set_xlim(0, 36)
    a.set_ylim(0, 112)
    a.set_xlabel("video duration (s)")
    a.set_ylabel("end-to-end job time (s)")
    a.legend(
        frameon=True,
        loc="lower right",
        framealpha=1,
        edgecolor="0.7",
        fancybox=False,
        borderpad=0.3,
    )
    a.grid(True)
    a.set_title(f"(a) {len(rows)} production jobs")

    # Mean stage breakdown per distinct video, ordered by duration.
    groups: dict[tuple[float, float], list[dict[str, float]]] = {}
    for job in rows:
        groups.setdefault((job["dur_ms"], (job["width"] or 0) * (job["height"] or 0)), []).append(
            job
        )
    keys = sorted(groups, key=lambda k: (k[0], k[1]))
    labels = []
    left = np.zeros(len(keys))
    cols = [C["grey"], C["purple"], C["sky"], C["green"], C["orange"]]
    for (sk, sl), col in zip(STAGES, cols, strict=True):
        vals = np.array([np.nanmean([j[sk] for j in groups[key]]) / 1000 for key in keys])
        b.barh(
            range(len(keys)), vals, left=left, color=col, edgecolor="black", linewidth=0.3, label=sl
        )
        left += vals
    for key in keys:
        g = groups[key][0]
        res = (
            f"{int(g['width'] or 0)}$\\times${int(g['height'] or 0)}"
            if g["width"] == g["width"] and g["width"]
            else "n/a"
        )
        labels.append(f"{key[0] / 1000:.2f} s, {res} ($n$={len(groups[key])})")
    b.set_yticks(range(len(keys)), labels)
    b.tick_params(axis="y", labelsize=6.5)
    b.set_xlabel("mean time per stage (s)")
    b.legend(
        frameon=False,
        ncol=5,
        loc="upper center",
        bbox_to_anchor=(0.42, -0.2),
        fontsize=6.3,
        columnspacing=0.8,
        handlelength=1,
    )
    b.grid(True, axis="x")
    b.set_title("(b) Where the time goes")
    save(fig, "latency")
    return rows


# --------------------------------------------------------------------------
# 5. AV-MossFormer2 on synthetic side-by-side VoxCeleb2 overlaps
# --------------------------------------------------------------------------
def moss_summary() -> list[dict[str, float]]:
    rows: list[dict[str, float]] = json.loads(
        (DATA / "avmossformer2-voxceleb2-pairs.json").read_text()
    )
    return rows


if __name__ == "__main__":
    fig_training()
    prod = fig_product()
    fig_baseline()
    jobs = fig_latency()
    moss = moss_summary()
    summary = {
        "product": prod,
        "moss_mean_si_sdri": float(np.mean([r["si_sdri"] for r in moss])),
        "moss_rows": moss,
        "jobs": len(jobs),
        "rtf": {
            "mean": float(np.mean([r["wall_s"] / (r["dur_ms"] / 1000) for r in jobs])),
            "min": float(np.min([r["wall_s"] / (r["dur_ms"] / 1000) for r in jobs])),
            "max": float(np.max([r["wall_s"] / (r["dur_ms"] / 1000) for r in jobs])),
        },
        "s5_share": float(np.mean([r["s5"] / (r["wall_s"] * 1000) for r in jobs])),
    }
    (HERE / "data" / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "moss_rows"}, indent=1))
    for r in moss:
        print(r)
