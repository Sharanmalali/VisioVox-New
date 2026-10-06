"""Aggregate AMI-AV results into the numbers the paper reports.

Reads the per-meeting caches and the JSON written by bench_ami_av.py,
bench_ami_gapfill.py and bench_ami_asr.py; writes ~/runs/ami-av-bench/summary.json
and prints a readable report. Every number here is recomputable from the caches.

Conventions:
  * Signal metrics use only reliable references (see bench_ami_av.py).
    Visibility and identity metrics need no headset, so they use every speaker.
  * A system that outputs nothing for a speaker scores -inf SI-SDR. Those are
    counted and reported, never silently dropped into a mean.
  * cpWER is pooled (total errors / total reference words), with a 95 %
    bootstrap over meetings.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml"))
sys.path.insert(0, str(ROOT / "scripts"))

from bench_ami_av import N_SEATS, OUT, VAD_RATE, meetings  # noqa: E402

from eval.harness import bootstrap_ci  # noqa: E402
from pipeline.vad import FRAME_SAMPLES  # noqa: E402

RESAMPLES = 10_000
SEED = 0


def ci(values: list[float]) -> dict[str, float]:
    finite = [v for v in values if np.isfinite(v)]
    mean, lo, hi = bootstrap_ci(finite)
    return {
        "mean": mean,
        "lo": lo,
        "hi": hi,
        "n": len(finite),
        # -inf: the system output nothing there. NaN: too little audio to score.
        "n_no_output": sum(1 for v in values if v == float("-inf")),
        "n_unscorable": sum(1 for v in values if np.isnan(v)),
    }


def frame_db(x: np.ndarray, frames: np.ndarray) -> float:
    n = min(len(frames), len(x) // FRAME_SAMPLES)
    sel = frames[:n]
    if not sel.any():
        return float("nan")
    e = (x[: n * FRAME_SAMPLES].reshape(n, FRAME_SAMPLES) ** 2).mean(1)
    return float(10 * np.log10(e[sel].mean() + 1e-12))


def visibility() -> dict[str, Any]:
    """How much meeting speech happens with the speaker's face untracked."""
    speech = hidden = 0.0
    per_speaker: list[float] = []
    fragmented = merged_wrong = seats = people = 0
    suppression: list[float] = []  # output vs mixture where the seat is silent and others speak
    for _split, meeting, _start in meetings():
        work = OUT / meeting.name
        if not (work / "av_tracks.npz").exists():
            continue
        eq = np.load(work / "equalised.npz")
        av = np.load(work / "av_tracks.npz")
        masks = eq["masks"].astype(bool)
        vis = av["visible"].astype(bool)
        n = min(masks.shape[1], vis.shape[1])
        for k in range(N_SEATS):
            act = masks[k, :n]
            if act.sum() >= VAD_RATE:  # at least 1 s of words
                speech += act.sum() / VAD_RATE
                hidden += (act & ~vis[k, :n]).sum() / VAD_RATE
                per_speaker.append(float((act & vis[k, :n]).sum() / act.sum()))
            silent_others = ~act & (masks[:, :n].sum(0) - act >= 1) & vis[k, :n]
            est = av["estimates"][k]
            if silent_others.sum() >= VAD_RATE and np.any(est):
                suppression.append(
                    frame_db(est, silent_others) - frame_db(eq["mixture"], silent_others)
                )
        owners = av["owners"]
        seats += N_SEATS
        people += int(av["n_people"])
        fragmented += int((owners > 1).sum())
        merged_wrong += int((av["purity"] < 0.999).sum())
    cov = np.asarray(per_speaker)
    return {
        "speakers_with_speech": len(per_speaker),
        "speech_s": speech,
        "hidden_speech_s": hidden,
        "hidden_fraction": hidden / speech if speech else float("nan"),
        "speakers_coverage_below_90": int((cov < 0.9).sum()),
        "speakers_coverage_zero": int((cov == 0).sum()),
        "seats": seats,
        "people_found": people,
        "seats_fragmented": fragmented,
        "people_spanning_seats": merged_wrong,
        "visible_silent_suppression_db": ci(suppression),
    }


def signal(rows: list[dict[str, Any]], system: str) -> dict[str, Any]:
    ok = [r for r in rows if r["reliable"] and r["system"] == system]
    full = [r for r in ok if r["coverage"] >= 0.9]
    return {
        "n_reliable": len(ok),
        "no_output": sum(1 for r in ok if not np.isfinite(r["si_sdri"])),
        "si_sdri": ci([r["si_sdri"] for r in ok]),
        "si_sdri_overlap": ci([r["si_sdri_overlap"] for r in ok]),
        "si_sdri_overlap_visible_speakers": ci([r["si_sdri_overlap"] for r in full]),
        "leak_db": ci([r["leak_db"] for r in ok]),
        "leak_db_mixture": ci([r["leak_db_mixture"] for r in ok]),
        "stoi": ci([r["stoi"] for r in ok]),
        "stoi_mixture": ci([r["stoi_mixture"] for r in ok]),
    }


def gapfill(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    hidden = [r for r in rows if r["reliable"] and r["hidden_s"] >= 1.0]
    for system in sorted({r["system"] for r in rows}):
        rs = [r for r in hidden if r["system"] == system]
        out[system] = {
            "n": len(rs),
            "si_sdr_hidden": ci([r["si_sdr_hidden"] for r in rs]),
            "mix_si_sdr_hidden": ci([r["mix_si_sdr_hidden"] for r in rs]),
            "si_sdri": ci([r["si_sdri"] for r in rs]),
            "stoi": ci([r["stoi"] for r in rs]),
        }
    cos = [
        r["enrol_cos"] for r in rows if r["system"] == "seave_self" and np.isfinite(r["enrol_cos"])
    ]
    out["self_enrolment_cosine"] = ci(cos)
    return out


def paired(rows: list[dict[str, Any]], a: str, b: str, key: str) -> dict[str, float]:
    """Mean of a - b over reliable speakers both systems scored, with 95 % CI.

    Pairs where b output nothing (-inf) cannot give a finite difference; they
    are counted as `b_no_output` so a gain from filling silence is not hidden.
    """
    by = {(r["meeting"], r["speaker"], r["system"]): r for r in rows if r["reliable"]}
    diffs: list[float] = []
    b_no_output = 0
    for (m, s, sys_), r in by.items():
        if sys_ != a or (m, s, b) not in by:
            continue
        va, vb = r[key], by[(m, s, b)][key]
        if vb == float("-inf") and np.isfinite(va):
            b_no_output += 1
        elif np.isfinite(va) and np.isfinite(vb):
            diffs.append(va - vb)
    mean, lo, hi = bootstrap_ci(diffs)
    return {"mean": mean, "lo": lo, "hi": hi, "n": len(diffs), "b_no_output": b_no_output}


def asr(rows: list[dict[str, Any]]) -> dict[str, Any]:
    systems = sorted({r["system"] for r in rows})
    by: dict[str, dict[str, tuple[int, int]]] = {s: {} for s in systems}
    for r in rows:
        by[r["system"]][r["meeting"]] = (r["errors"], r["length"])
    meetings_all = sorted(set.intersection(*(set(v) for v in by.values()))) if by else []
    rng = np.random.default_rng(SEED)
    draws = (
        rng.integers(0, len(meetings_all), size=(RESAMPLES, len(meetings_all)))
        if meetings_all
        else None
    )
    out: dict[str, Any] = {"meetings": len(meetings_all)}
    arrays = {}
    for s in systems:
        e = np.array([by[s][m][0] for m in meetings_all], dtype=float)
        n = np.array([by[s][m][1] for m in meetings_all], dtype=float)
        arrays[s] = (e, n)
        pooled = e.sum() / n.sum() if n.sum() else float("nan")
        boot = (e[draws].sum(1) / n[draws].sum(1)) if draws is not None else np.array([np.nan])
        out[s] = {
            "cpwer": pooled,
            "lo": float(np.percentile(boot, 2.5)),
            "hi": float(np.percentile(boot, 97.5)),
        }
    # Paired differences against the audio-only baseline.
    if "mixture_diarized" in arrays and draws is not None:
        be, bn = arrays["mixture_diarized"]
        for s in systems:
            if s == "mixture_diarized":
                continue
            e, n = arrays[s]
            diff = e[draws].sum(1) / n[draws].sum(1) - be[draws].sum(1) / bn[draws].sum(1)
            out[s]["vs_diarized"] = {
                "delta": float(e.sum() / n.sum() - be.sum() / bn.sum()),
                "lo": float(np.percentile(diff, 2.5)),
                "hi": float(np.percentile(diff, 97.5)),
            }
    return out


def main() -> int:
    summary: dict[str, Any] = {"visibility": visibility()}
    if (OUT / "results.json").exists():
        rows = json.loads((OUT / "results.json").read_text())
        summary["av_mossformer2"] = signal(rows, "av_mossformer2")
        summary["meetings_scored"] = len({r["meeting"] for r in rows})
    if (OUT / "gapfill.json").exists():
        g = json.loads((OUT / "gapfill.json").read_text())
        summary["gapfill"] = gapfill(g)
        summary["gapfill_signal"] = {s: signal(g, s) for s in sorted({r["system"] for r in g})}
        summary["gapfill_paired"] = {
            f"{a}-{b}:{k}": paired(g, a, b, k)
            for a, b in (("hybrid_self", "av"), ("hybrid_oracle", "av"), ("seave_self", "av"))
            for k in ("si_sdri", "si_sdri_overlap", "stoi")
        }
    if (OUT / "asr.json").exists():
        summary["asr"] = asr(json.loads((OUT / "asr.json").read_text()))
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1, default=float))
    print(json.dumps(summary, indent=1, default=lambda x: round(float(x), 3)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
