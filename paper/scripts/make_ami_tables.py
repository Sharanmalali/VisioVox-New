"""AMI-AV tables for the paper (tables/ami_*.tex) and the numbers quoted in the text.

Reads paper/data/ami/*.json, copied from the benchmark runs
(scripts/bench_ami_*.py). Signal statistics come from scripts/analyse_ami.py's
summary.json; caption statistics are pooled here (errors over reference words)
with 95 % bootstrap intervals over meetings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parents[1]
DATA = HERE / "data" / "ami"
OUT = HERE / "tables"
RESAMPLES = 10_000


def load(name: str) -> Any:
    return json.loads((DATA / f"{name}.json").read_text())


summary = load("summary")
numbers: dict[str, Any] = {}

# ---- captions: one row per (meeting, system) with sa / cp errors ---------------
rows: list[dict[str, Any]] = []
for r in load("seat"):
    if r["system"] in ("attrib_av", "diar_faces", "attrib_oracle"):
        rows.append({**r, "ins": None})
for r in load("dedup"):
    rows.append({**r, "ins": r["insertions"]})
for r in load("asr"):
    if r["system"] == "mixture_diarized":
        rows.append(
            {
                "meeting": r["meeting"],
                "split": r["split"],
                "system": "pyannote",
                "sa_errors": float("nan"),
                "cp_errors": r["errors"],
                "length": r["length"],
                "ins": r["insertions"],
            }
        )
    if r["system"] == "headset":
        rows.append(
            {
                "meeting": r["meeting"],
                "split": r["split"],
                "system": "headset",
                "sa_errors": float("nan"),
                "cp_errors": r["errors"],
                "length": r["length"],
                "ins": r["insertions"],
            }
        )
    if r["system"] == "mixture_unattributed_wer":
        rows.append(
            {
                "meeting": r["meeting"],
                "split": r["split"],
                "system": "whisper_mixture",
                "sa_errors": float("nan"),
                "cp_errors": r["errors"],
                "length": r["length"],
                "ins": None,
            }
        )


def pooled(system: str, split: str | None, key: str) -> dict[str, float]:
    rs = sorted(
        (r for r in rows if r["system"] == system and (split is None or r["split"] == split)),
        key=lambda r: r["meeting"],
    )
    e = np.array([r[key] for r in rs], dtype=float)
    n = np.array([r["length"] for r in rs], dtype=float)
    rng = np.random.default_rng(0)
    idx = rng.integers(0, len(rs), size=(RESAMPLES, len(rs)))
    boot = e[idx].sum(1) / n[idx].sum(1)
    out = {
        "value": float(e.sum() / n.sum()),
        "lo": float(np.percentile(boot, 2.5)),
        "hi": float(np.percentile(boot, 97.5)),
        "meetings": len(rs),
    }
    ins = [r["ins"] for r in rs]
    if all(i is not None for i in ins):
        out["ins"] = float(sum(ins) / n.sum())
    return out


def paired(a: str, b: str, split: str | None, key: str) -> dict[str, float]:
    ra = {
        r["meeting"]: r for r in rows if r["system"] == a and (split is None or r["split"] == split)
    }
    rb = {
        r["meeting"]: r for r in rows if r["system"] == b and (split is None or r["split"] == split)
    }
    ms = sorted(set(ra) & set(rb))
    ea = np.array([ra[m][key] for m in ms], dtype=float)
    na = np.array([ra[m]["length"] for m in ms], dtype=float)
    eb = np.array([rb[m][key] for m in ms], dtype=float)
    nb = np.array([rb[m]["length"] for m in ms], dtype=float)
    rng = np.random.default_rng(0)
    idx = rng.integers(0, len(ms), size=(RESAMPLES, len(ms)))
    d = ea[idx].sum(1) / na[idx].sum(1) - eb[idx].sum(1) / nb[idx].sum(1)
    return {
        "delta": float(ea.sum() / na.sum() - eb.sum() / nb.sum()),
        "lo": float(np.percentile(d, 2.5)),
        "hi": float(np.percentile(d, 97.5)),
    }


def pct(x: float) -> str:
    return f"{100 * x:.1f}"


def ci(p: dict[str, float]) -> str:
    return f"{pct(p['value'])} {{\\scriptsize[{pct(p['lo'])}, {pct(p['hi'])}]}}"


SYSTEMS = [
    ("whisper_mixture", "Whisper, mixture$^\\dagger$"),
    ("pyannote", "pyannote + Whisper"),
    ("headset", "Per-headset$^\\ddagger$"),
    ("av_stream", "Per-track (before)"),
    ("av_dedup", "\\textbf{+ dedup (deployed)}"),
    ("hybrid_dedup", "+ dedup, SEAVE fill"),
    ("attrib_av", "Transcribe once"),
    ("diar_faces", "pyannote, by face"),
    ("attrib_oracle", "Oracle attribution"),
]
lines = []
for key, label in SYSTEMS:
    sa = pooled(key, "eval", "sa_errors")
    cp = pooled(key, "eval", "cp_errors")
    numbers[f"captions_{key}"] = {"sa": sa, "cp": cp}
    sa_cell = "--" if np.isnan(sa["value"]) else ci(sa)
    ins = cp.get("ins")
    ins_cell = "--" if ins is None else pct(ins)
    lines.append(f"{label} & {sa_cell} & {ci(cp)} & {ins_cell} \\\\")
    if key in ("headset", "av_dedup", "diar_faces"):
        lines.append("\\midrule")
numbers["dedup_vs_stream_sa_eval"] = paired("av_dedup", "av_stream", "eval", "sa_errors")
numbers["dedup_vs_pyannote_cp_eval"] = paired("av_dedup", "pyannote", "eval", "cp_errors")
numbers["dedup_vs_attrib_sa_eval"] = paired("av_dedup", "attrib_av", "eval", "sa_errors")
numbers["dedup_vs_attrib_sa_all"] = paired("av_dedup", "attrib_av", None, "sa_errors")
numbers["stream_vs_pyannote_cp_eval"] = paired("av_stream", "pyannote", "eval", "cp_errors")
numbers["diarfaces_vs_dedup_sa_eval"] = paired("diar_faces", "av_dedup", "eval", "sa_errors")
numbers["pyannote_cp_all"] = pooled("pyannote", None, "cp_errors")
numbers["dedup_cp_all"] = pooled("av_dedup", None, "cp_errors")
numbers["dedup_sa_all"] = pooled("av_dedup", None, "sa_errors")
numbers["stream_sa_all"] = pooled("av_stream", None, "sa_errors")

(OUT / "ami_captions.tex").write_text(
    "\\begin{tabular}{@{}lccr@{}}\n\\toprule\n"
    "System & saWER (\\%) & cpWER (\\%) & Ins. \\\\\n\\midrule\n"
    + "\n".join(lines).removesuffix("\\midrule")
    + "\n\\bottomrule\n\\end{tabular}\n"
)

# ---- the extractor on real meetings -----------------------------------------
vis = summary["visibility"]
av = summary["av_mossformer2"]


def sig(c: dict[str, float], nd: int = 2) -> str:
    return f"${c['mean']:+.{nd}f}$ {{\\scriptsize$[{c['lo']:+.{nd}f}, {c['hi']:+.{nd}f}]$}}"


ami_rows = [
    (
        "Speech while face untracked",
        f"{pct(vis['hidden_fraction'])}\\% of {vis['speech_s'] / 60:.0f} min",
    ),
    (
        "Speakers $<$90\\% / never covered",
        f"{vis['speakers_coverage_below_90']} / {vis['speakers_coverage_zero']} of {vis['speakers_with_speech']}",
    ),
    ("People found for 120 seats", f"{vis['people_found']}"),
    ("Seats split into $>$1 person", f"{vis['seats_fragmented']}"),
    ("People spanning two seats", f"{vis['people_spanning_seats']}"),
    (
        f"Overlap SI-SDRi, face seen $\\ge$90\\% ($n$={av['si_sdri_overlap_visible_speakers']['n']})",
        sig(av["si_sdri_overlap_visible_speakers"]),
    ),
    (
        f"Overlap SI-SDRi, all reliable ($n$={av['si_sdri_overlap']['n']})",
        sig(av["si_sdri_overlap"]),
    ),
    ("Output vs.\\ mixture, own person silent (dB)", sig(vis["visible_silent_suppression_db"], 1)),
    ("STOI, output / mixture", f"{av['stoi']['mean']:.2f} / {av['stoi_mixture']['mean']:.2f}"),
]
(OUT / "ami_av.tex").write_text(
    "\\begin{tabular}{@{}lr@{}}\n\\toprule\nMeasure & Value \\\\\n\\midrule\n"
    + "\n".join(f"{a} & {b} \\\\" for a, b in ami_rows)
    + "\n\\bottomrule\n\\end{tabular}\n"
)

# ---- heavy-overlap check and signal-side results quoted in the text ----------
ov = load("overlap-captions")
for design in ("stream", "stream_dedup", "attrib"):
    rs = [r for r in ov if r["design"] == design]
    numbers[f"overlap_wer_{design}"] = sum(r["errors"] for r in rs) / sum(r["length"] for r in rs)
numbers["arbitration_signal"] = load("arbitration_signal")["summary"]
numbers["arbitration_fit"] = {k: load(k) for k in ("arbitration-av", "arbitration-hybrid")}
numbers["gapfill"] = summary["gapfill"]
numbers["gapfill_paired"] = summary["gapfill_paired"]
numbers["visibility"] = vis
numbers["av_mossformer2"] = av
(HERE / "data" / "ami-numbers.json").write_text(json.dumps(numbers, indent=1, default=float))
print("tables written: ami_captions.tex, ami_av.tex; numbers in data/ami-numbers.json")
