"""CPU-only against GPU (Sec. VII-E): tables/cpu_gpu.tex and data/cpu-gpu-numbers.json.

Inputs, in data/cpu-gpu/ (copied from ~/runs/cpu-gpu-bench/yt, see README):

    gpu.json, cpu.json        bench_video.py: the production pipeline on one clip
    gpu.npz, cpu.npz          the separated tracks of each run
    cpu-unmodified.json       the same, GPU hidden, no workarounds: how it fails
    dub-gpu.json, dub-cpu.json   bench_dub.py: dubbing that project into Hindi
    dub-gpu-run1.json         the first GPU dub (cold disk cache), for repeatability
    dub-compare.json          compare_dub.py: Whisper round trip of every voiced line

Numbers quoted in the text are written to data/cpu-gpu-numbers.json, so the
prose can be checked against the same files as the table.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

HERE = Path(__file__).resolve().parents[1]
DATA = HERE / "data" / "cpu-gpu"
OUT = HERE / "tables"
sys.path.insert(0, str(HERE.parent / "ml"))
from eval.metrics import si_sdr  # noqa: E402


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((DATA / f"{name}.json").read_text())
    return data


cpu, gpu, unmod = load("cpu"), load("gpu"), load("cpu-unmodified")
dc, dg, dg1, cmp_ = load("dub-cpu"), load("dub-gpu"), load("dub-gpu-run1"), load("dub-compare")
acpu, agpu = np.load(DATA / "cpu.npz")["audio"], np.load(DATA / "gpu.npz")["audio"]


# ---- does the CPU run produce the same result? ---------------------------
def tokens(text: str) -> list[str]:
    return re.sub(r"[^\w' ]+", " ", text.lower()).split()


def edit(a: list[str], b: list[str]) -> int:
    d = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, y in enumerate(b, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (x != y))
    return d[len(b)]


# Speakers are paired across the runs by where their face is, never by audio.
sc, sg = cpu["speakers"], gpu["speakers"]
cost = np.array([[np.hypot(*(np.array(a["center"]) - b["center"])) for b in sg] for a in sc])
rows, cols = linear_sum_assignment(cost)
n = min(acpu.shape[1], agpu.shape[1])
agree = [float(si_sdr(acpu[i][:n], agpu[j][:n])) for i, j in zip(rows, cols, strict=True)]
words = sum(len(tokens(sg[j]["text"])) for j in cols)
word_diff = sum(
    edit(tokens(sg[j]["text"]), tokens(sc[i]["text"])) for i, j in zip(rows, cols, strict=True)
)

# ---- dubbing: same translation, same intelligibility? --------------------
same = [
    i
    for i, (a, b) in enumerate(zip(dc["lines"], dg["lines"], strict=True))
    if a["text"] == b["text"]
]


def cer_on(run: str, ids: list[int]) -> float:
    per = cmp_[run]["per_line"]
    return float(sum(per[i]["errors"] for i in ids) / max(sum(per[i]["chars"] for i in ids), 1))


repeat = all(
    a["duration_ms"] == b["duration_ms"] for a, b in zip(dg["lines"], dg1["lines"], strict=True)
)

numbers = {
    "video_s": gpu["duration_s"],
    "overlap_ratio": gpu["overlap_ratio"],
    "dub_gpu_util_mean_pct": dg["gpu_util_mean_pct"],
    "proc_gpu_s": gpu["wall_s"],
    "proc_cpu_s": cpu["wall_s"],
    "proc_ratio": cpu["wall_s"] / gpu["wall_s"],
    "rtf_gpu": gpu["rtf"],
    "rtf_cpu": cpu["rtf"],
    "s5_ratio": cpu["stage_s"]["S5_extract"] / gpu["stage_s"]["S5_extract"],
    "s7_ratio": cpu["stage_s"]["S7_transcribe"] / gpu["stage_s"]["S7_transcribe"],
    "cpu_seconds_gpu": gpu["process_cpu_s"],
    "cpu_seconds_cpu": cpu["process_cpu_s"],
    "cores_cpu": cpu["mean_cores_busy"],
    "gpu_mem_extra_mib_cpu_run": cpu["gpu_mem_peak_extra_mib"],
    "track_agreement_sisdr_mean": float(np.mean(agree)),
    "track_agreement_sisdr_min": float(np.min(agree)),
    "caption_word_diff": word_diff / max(words, 1),
    "caption_words": words,
    "unmodified_outcome": unmod["outcome"],
    "unmodified_seconds": unmod["seconds_until_end"],
    "dub_language": dg["language"],
    "dub_lines": dg["n_lines"],
    "dub_gpu_s": dg["wall_s"],
    "dub_cpu_s": dc["wall_s"],
    "dub_ratio": dc["wall_s"] / dg["wall_s"],
    "dub_gpu_first_run_s": dg1["wall_s"],
    "tts_generate_gpu_s": dg["tts_generate_s"],
    "tts_generate_cpu_s": dc["tts_generate_s"],
    "tts_load_gpu_s": dg["tts_load_s"],
    "tts_load_cpu_s": dc["tts_load_s"],
    "dub_gpu_repeat_identical": repeat,
    "translation_same_lines": len(same),
    "dub_cer_same_lines_cpu": cer_on("cpu", same),
    "dub_cer_same_lines_gpu": cer_on("gpu", same),
}
(HERE / "data" / "cpu-gpu-numbers.json").write_text(json.dumps(numbers, indent=1))


# ---- table -----------------------------------------------------------------
def t(s: float) -> str:
    return f"{s / 60:.1f}\\,min" if s >= 120 else f"{s:.1f}\\,s"


def x(ratio: float) -> str:
    return f"{ratio:.1f}$\\times$" if ratio >= 1 else f"{ratio:.2f}$\\times$"


def stage_s(run: dict[str, Any], stage: str) -> float:
    return float(run["stage_s"][stage])


body = [
    ("\\textit{Processing (S0--S9)}", t(cpu["wall_s"]), t(gpu["wall_s"]), x(cpu["wall_s"] / gpu["wall_s"])),
    ("\\quad real-time factor", f"{cpu['rtf']:.2f}", f"{gpu['rtf']:.2f}", ""),
    ("\\quad S5 faces + AV-TSE", t(stage_s(cpu, "S5_extract")), t(stage_s(gpu, "S5_extract")),
     x(stage_s(cpu, "S5_extract") / stage_s(gpu, "S5_extract"))),
    ("\\quad S7 captions + diarization", t(stage_s(cpu, "S7_transcribe")), t(stage_s(gpu, "S7_transcribe")),
     x(stage_s(cpu, "S7_transcribe") / stage_s(gpu, "S7_transcribe"))),
    ("\\quad CPU time used", f"{cpu['process_cpu_s']:,.0f}\\,s", f"{gpu['process_cpu_s']:,.0f}\\,s", ""),
    (f"\\textit{{Dubbing into Hindi ({dg['n_lines']} lines)}}", t(dc["wall_s"]), t(dg["wall_s"]),
     x(dc["wall_s"] / dg["wall_s"])),
    ("\\quad loading the voice model", t(dc["tts_load_s"]), t(dg["tts_load_s"]),
     x(dc["tts_load_s"] / dg["tts_load_s"])),
    ("\\quad speaking the lines", t(dc["tts_generate_s"]), t(dg["tts_generate_s"]),
     x(dc["tts_generate_s"] / dg["tts_generate_s"])),
    ("\\quad CPU time used", f"{dc['process_cpu_s']:,.0f}\\,s", f"{dg['process_cpu_s']:,.0f}\\,s", ""),
]  # fmt: skip
lines = [f"{a} & {b} & {c} & {d} \\\\" for a, b, c, d in body]
lines.insert(5, "\\midrule")
table = (
    "\\begin{tabular}{@{}lrrr@{}}\n\\toprule\n"
    " & CPU only & GPU & ratio \\\\\n\\midrule\n"
    + "\n".join(lines)
    + "\n\\bottomrule\n\\end{tabular}\n"
)
(OUT / "cpu_gpu.tex").write_text(table)
print(json.dumps(numbers, indent=1))
