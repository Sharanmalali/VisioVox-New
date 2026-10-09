"""Score and analyse the user study (docs/user-study/README.md, section 7).

Reads the CSV exported from /study:

    uv run python scripts/analyse_user_study.py visiovox-user-study.csv [--json out.json]

The analysis is the one written down in the protocol before any data; the
script has no options that change it. Pilot codes (anything but "P" and
digits) and participants who did not finish both parts are excluded.
Rows whose ``correct`` column is filled in by hand use that score; otherwise
T1 is scored by token F1 >= 0.5 and T2 by +-5 s, and T3 must be hand-scored -
unscored T3 rows are reported, not guessed.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Any

import numpy as np
from scipy import stats

CONDITIONS = ("visiovox", "baseline")
TASKS_PER_PART = 3
F1_PASS = 0.5
TIME_TOLERANCE_S = 5.0
BOOTSTRAP = 10_000


@dataclass
class Row:
    participant: str
    order: str
    task: str
    condition: str
    recording: str
    seconds: float
    answer: str
    key: str
    correct: int | None
    f1: float | None
    sus: float
    preference: str


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def token_f1(answer: str, key: str) -> float:
    a, k = Counter(tokens(answer)), Counter(tokens(key))
    common = sum((a & k).values())
    if common == 0:
        return 0.0
    precision = common / sum(a.values())
    recall = common / sum(k.values())
    return 2 * precision * recall / (precision + recall)


def parse_time(text: str) -> float | None:
    """'2:37', '02:37', '1:02:37' or '157' -> seconds; None when no time is there."""
    m = re.search(r"(\d+(?::\d{1,2}){0,2})", text)
    if m is None:
        return None
    seconds = 0.0
    for part in m.group(1).split(":"):
        seconds = seconds * 60 + int(part)
    return seconds


def score(task: str, answer: str, key: str, hand: str) -> tuple[int | None, float | None]:
    """(correct, f1). A hand score always wins; T3 has no automatic score."""
    f1 = token_f1(answer, key) if task == "T1" and key else None
    if hand.strip() in ("0", "1"):
        return int(hand.strip()), f1
    if task == "T1" and f1 is not None:
        return int(f1 >= F1_PASS), f1
    if task == "T2" and key:
        got, want = parse_time(answer), parse_time(key)
        if want is not None:
            return int(got is not None and abs(got - want) <= TIME_TOLERANCE_S), None
    return None, f1


def load(path: Path) -> list[Row]:
    with path.open(newline="", encoding="utf-8") as f:
        records = list(csv.DictReader(f))
    rows = []
    for r in records:
        correct, f1 = score(r["task"], r["answer"], r["answer_key"], r.get("correct", ""))
        rows.append(
            Row(
                participant=r["participant"],
                order=r["order"],
                task=r["task"],
                condition=r["condition"],
                recording=r["recording"],
                seconds=float(r["seconds"]),
                answer=r["answer"],
                key=r["answer_key"],
                correct=correct,
                f1=f1,
                sus=float(r["sus_score"]),
                preference=r["preference"],
            )
        )
    return rows


def included(rows: list[Row]) -> tuple[list[Row], list[str]]:
    """Drop pilots and anyone without a full set of tasks in both conditions."""
    by: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by[r.participant].append(r)
    kept, excluded = [], []
    for code, mine in sorted(by.items()):
        complete = all(sum(r.condition == c for r in mine) >= TASKS_PER_PART for c in CONDITIONS)
        if re.fullmatch(r"[Pp]\d+", code) is None or not complete:
            excluded.append(code)
            continue
        kept.extend(mine)
    return kept, excluded


def rank_biserial(diffs: np.ndarray) -> float:
    """Matched-pairs rank-biserial correlation (Kerby, 2014); zero differences dropped."""
    d = diffs[diffs != 0]
    if len(d) == 0:
        return 0.0
    ranks = stats.rankdata(np.abs(d))
    positive = ranks[d > 0].sum()
    negative = ranks[d < 0].sum()
    return float((positive - negative) / ranks.sum())


def paired(visiovox: list[float], baseline: list[float]) -> dict[str, Any]:
    v, b = np.asarray(visiovox), np.asarray(baseline)
    diffs = v - b
    out: dict[str, Any] = {
        "n": len(v),
        "visiovox_median": float(np.median(v)),
        "baseline_median": float(np.median(b)),
        "visiovox_mean": float(np.mean(v)),
        "baseline_mean": float(np.mean(b)),
        "rank_biserial": rank_biserial(diffs),
    }
    if np.all(diffs == 0):
        out.update(statistic=None, p=1.0)
    else:
        test = stats.wilcoxon(v, b, zero_method="wilcox", alternative="two-sided")
        out.update(statistic=float(test.statistic), p=float(test.pvalue))
    return out


def holm(pvalues: list[float]) -> list[float]:
    order = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    adjusted = [0.0] * len(pvalues)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(pvalues) - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def bootstrap_ci(values: list[float], seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    data = np.asarray(values)
    means = rng.choice(data, size=(BOOTSTRAP, len(data)), replace=True).mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def analyse(rows: list[Row]) -> dict[str, Any]:
    kept, excluded = included(rows)
    if not kept:
        raise SystemExit("No complete, non-pilot participants in this export - nothing to analyse.")
    participants = sorted({r.participant for r in kept})

    def per_participant(cond: str, value: str) -> list[float]:
        out = []
        for p in participants:
            mine = [r for r in kept if r.participant == p and r.condition == cond]
            if value == "time":
                out.append(mean(r.seconds for r in mine))
            else:
                scored = [r.correct for r in mine if r.correct is not None]
                out.append(mean(scored) if scored else float("nan"))
        return out

    time = paired(per_participant("visiovox", "time"), per_participant("baseline", "time"))

    unscored = [(r.participant, r.task, r.recording) for r in kept if r.correct is None]
    accuracy: dict[str, Any] | None = None
    if not unscored:
        accuracy = paired(
            per_participant("visiovox", "accuracy"), per_participant("baseline", "accuracy")
        )
    adjusted = holm([time["p"]] + ([accuracy["p"]] if accuracy else []))
    time["p_holm"] = adjusted[0]
    if accuracy:
        accuracy["p_holm"] = adjusted[1]

    by_task: dict[str, dict[str, Any]] = {}
    for task in sorted({r.task for r in kept}):
        by_task[task] = {}
        for cond in CONDITIONS:
            mine = [r for r in kept if r.task == task and r.condition == cond]
            scored = [r.correct for r in mine if r.correct is not None]
            f1s = [r.f1 for r in mine if r.f1 is not None]
            by_task[task][cond] = {
                "median_seconds": median(r.seconds for r in mine),
                "accuracy": mean(scored) if scored else None,
                "scored": len(scored),
                "of": len(mine),
                "mean_f1": mean(f1s) if f1s else None,
            }

    sus = [next(r.sus for r in kept if r.participant == p) for p in participants]
    sus_lo, sus_hi = bootstrap_ci(sus)
    prefs = Counter(next(r.preference for r in kept if r.participant == p) for p in participants)
    decided = prefs["visiovox"] + prefs["baseline"]
    pref_p = float(stats.binomtest(prefs["visiovox"], decided, 0.5).pvalue) if decided else None
    orders = Counter(next(r.order for r in kept if r.participant == p) for p in participants)

    return {
        "participants": len(participants),
        "excluded": excluded,
        "orders": dict(orders),
        "time_seconds": time,
        "accuracy": accuracy,
        "unscored": unscored,
        "by_task": by_task,
        "sus": {"mean": mean(sus), "ci95": [sus_lo, sus_hi], "n": len(sus)},
        "preference": {
            **{k: prefs[k] for k in ("visiovox", "baseline", "no-preference")},
            "binomial_p": pref_p,
        },
    }


def report(result: dict[str, Any]) -> str:
    lines = [f"Participants analysed: {result['participants']}"]
    if result["excluded"]:
        lines.append(f"Excluded (pilot or incomplete): {', '.join(result['excluded'])}")
    lines.append(f"Orders: {result['orders']}")
    if result["participants"] % 4:
        lines.append("Note: not a multiple of four - the Latin square is unbalanced.")

    t = result["time_seconds"]
    lines += [
        "",
        "Time per task (participant means, s)",
        f"  VisioVox median {t['visiovox_median']:.1f}   baseline median {t['baseline_median']:.1f}",
        f"  Wilcoxon W={t['statistic']}, p={t['p']:.4f} (Holm {t['p_holm']:.4f}), "
        f"rank-biserial r={t['rank_biserial']:.2f}  (negative = VisioVox faster)",
    ]
    a = result["accuracy"]
    if a is None:
        lines += [
            "",
            f"Accuracy: NOT computed - {len(result['unscored'])} rows still need a hand score:",
        ]
        lines += [f"  {p} {task} (recording {rec})" for p, task, rec in result["unscored"]]
    else:
        lines += [
            "",
            "Accuracy (participant proportion correct)",
            f"  VisioVox mean {a['visiovox_mean']:.2f}   baseline mean {a['baseline_mean']:.2f}",
            f"  Wilcoxon W={a['statistic']}, p={a['p']:.4f} (Holm {a['p_holm']:.4f}), "
            f"rank-biserial r={a['rank_biserial']:.2f}  (positive = VisioVox more accurate)",
        ]
    lines += ["", "By task          condition   median s   accuracy   mean F1"]
    for task, conds in result["by_task"].items():
        for cond, c in conds.items():
            acc = "-" if c["accuracy"] is None else f"{c['accuracy']:.2f} ({c['scored']}/{c['of']})"
            f1 = "-" if c["mean_f1"] is None else f"{c['mean_f1']:.2f}"
            lines.append(f"  {task:<14} {cond:<11} {c['median_seconds']:>8.1f}   {acc:<14} {f1}")
    s = result["sus"]
    lines += [
        "",
        f"SUS: mean {s['mean']:.1f}, 95% CI [{s['ci95'][0]:.1f}, {s['ci95'][1]:.1f}], n={s['n']}",
    ]
    p = result["preference"]
    pp = "n/a" if p["binomial_p"] is None else f"{p['binomial_p']:.4f}"
    lines.append(
        f"Preference: VisioVox {p['visiovox']}, baseline {p['baseline']}, "
        f"none {p['no-preference']}  (exact binomial p={pp})"
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv", type=Path, help="the export from /study")
    parser.add_argument("--json", type=Path, help="also write the numbers here")
    args = parser.parse_args()
    result = analyse(load(args.csv))
    sys.stdout.write(report(result) + "\n")
    if args.json:
        args.json.write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
