"""Meeting summaries on AMI: are the action items and decisions the ones people noted?

AMI's annotators wrote, for every scenario meeting, the actions agreed and the
decisions taken (`abstractive/<meeting>.abssumm.xml`), naming owners by role
("The industrial designer will work on the working design"). This runs the
deployed summariser (ml/pipeline/s11_summarize.py, Qwen2.5-7B via Ollama) on
each whole meeting's speaker-attributed transcript and compares.

The transcript here is AMI's own (hand-aligned words per participant, speakers
named by role): it measures the summariser given a correctly attributed
transcript - the input VisioVox aims to produce - not the separation, which
is measured in docs/29 sections 2-3 on 120 s windows.

Matching is lexical, and deliberately simple: content words (lower-case, stop
words and role names removed, crude suffix stripping), Dice overlap >=
THRESHOLD, greedy one-to-one. Role names are removed because AMI writes the
owner into the sentence ("The industrial designer will ...") while the
product states it separately - and owners are scored on their own. Lexical
matching still undercounts paraphrases; the numbers are a floor.

Each meeting's model output is cached (`summary_output.json`) so that
rescoring needs no GPU.

    uv run python scripts/bench_ami_summary.py   (needs `ollama serve` running)
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import parse as parse_xml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml"))
sys.path.insert(0, str(ROOT / "scripts"))

from bench_ami_av import OUT, meetings  # noqa: E402

from eval.ami_words import UNPACKED, words  # noqa: E402
from pipeline.s11_summarize import Line, summarise  # noqa: E402

HOST = "http://127.0.0.1:11434"
THRESHOLD = 0.35
GAP_S = 1.0
MAX_WORDS = 40
ROLES = {
    "PM": "Project Manager",
    "ID": "Industrial Designer",
    "UI": "User Interface Designer",
    "ME": "Marketing Expert",
}
ROLE_WORDS = {
    "PM": ("project manager",),
    "ID": ("industrial designer",),
    "UI": ("user interface", "interface designer"),
    "ME": ("marketing",),
}
STOP = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "by",
    "is",
    "are",
    "be",
    "will",
    "would",
    "should",
    "can",
    "could",
    "at",
    "as",
    "that",
    "this",
    "it",
    "its",
    "they",
    "them",
    "their",
    "he",
    "she",
    "his",
    "her",
    "we",
    "our",
    "you",
    "your",
    "from",
    "about",
    "into",
    "than",
    "then",
    "also",
    "which",
    "who",
    "what",
    "when",
    "how",
    "all",
    "any",
    "each",
    "some",
    "more",
    "most",
    "not",
    "no",
    "do",
    "does",
    "did",
    "have",
    "has",
    "had",
    "was",
    "were",
    "been",
    "being",
    "work",
    "working",
    "team",
    "meeting",
    "next",
}


ROLE_PHRASES = re.compile(
    r"project manager|industrial designer|user interface designer|interface designer|"
    r"marketing expert|marketing executive|\bpm\b|\bid\b|\bui\b|\bme\b"
)


def content(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", ROLE_PHRASES.sub(" ", text.lower())):
        if w in STOP or len(w) < 3:
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                w = w[: -len(suffix)]
                break
        out.add(w)
    return out


def dice(a: set[str], b: set[str]) -> float:
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


def match(hyp: list[str], ref: list[str]) -> list[tuple[int, int]]:
    pairs = sorted(
        (
            (dice(content(h), content(r)), i, j)
            for i, h in enumerate(hyp)
            for j, r in enumerate(ref)
        ),
        reverse=True,
    )
    used_h: set[int] = set()
    used_r: set[int] = set()
    out = []
    for score, i, j in pairs:
        if score < THRESHOLD:
            break
        if i not in used_h and j not in used_r:
            used_h.add(i)
            used_r.add(j)
            out.append((i, j))
    return out


def roster(meeting: str) -> dict[int, tuple[str, str]]:
    """channel -> (agent letter, role code), from AMI's meeting metadata."""
    root = parse_xml(UNPACKED / "corpusResources" / "meetings.xml").getroot()  # noqa: S314
    for m in root.iter("meeting"):
        if m.get("observation") == meeting:
            return {
                int(s.get("channel", "0")): (s.get("nxt_agent", ""), s.get("role", ""))
                for s in m.iter("speaker")
            }
    raise KeyError(meeting)


def transcript(meeting: str, channels: dict[int, tuple[str, str]]) -> list[Line]:
    """Words per participant, joined into lines at pauses > GAP_S."""
    chunks: list[tuple[float, int, str]] = []
    for channel in channels:
        current: list[str] = []
        start = last = 0.0
        for w in words(meeting, channel):
            if current and (w.start - last > GAP_S or len(current) >= MAX_WORDS):
                chunks.append((start, channel + 1, " ".join(current)))
                current = []
            if not current:
                start = w.start
            current.append(w.text)
            last = w.end
        if current:
            chunks.append((start, channel + 1, " ".join(current)))
    chunks.sort()
    return [Line(f"L{i}", spk, round(t * 1000), text) for i, (t, spk, text) in enumerate(chunks, 1)]


def reference(meeting: str) -> dict[str, list[str]]:
    path = UNPACKED / "abstractive" / f"{meeting}.abssumm.xml"
    root = parse_xml(path).getroot()  # noqa: S314 - local corpus
    out: dict[str, list[str]] = {"actions": [], "decisions": []}
    for section in ("actions", "decisions"):
        for node in root.iter(section):
            out[section] += [(s.text or "").strip() for s in node.iter("sentence") if s.text]
    return out


def role_named(sentence: str) -> str | None:
    lowered = sentence.lower()
    for code, phrases in ROLE_WORDS.items():
        if any(p in lowered for p in phrases):
            return code
    return None


def main() -> int:
    rows: list[dict[str, Any]] = []
    for split, meeting_dir, _ in meetings():
        meeting = meeting_dir.name
        if not (UNPACKED / "abstractive" / f"{meeting}.abssumm.xml").exists():
            continue
        channels = roster(meeting)
        lines = transcript(meeting, channels)
        names = {ch + 1: ROLES.get(role, f"Speaker {ch + 1}") for ch, (_, role) in channels.items()}
        roles = {ch + 1: role for ch, (_, role) in channels.items()}
        cache = OUT / meeting / "summary_output.json"
        if cache.exists():
            cached = json.loads(cache.read_text())
            out, seconds = cached["output"], cached["seconds"]
        else:
            t0 = time.time()
            out = summarise(lines, names, HOST)
            seconds = time.time() - t0
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"output": out, "seconds": seconds}))
        ref = reference(meeting)

        hyp_actions = [a["task"] for a in out["action_items"]]
        hyp_decisions = [d["text"] for d in out["decisions"]]
        action_pairs = match(hyp_actions, ref["actions"])
        decision_pairs = match(hyp_decisions, ref["decisions"])
        owner_checked = owner_right = 0
        for i, j in action_pairs:
            want = role_named(ref["actions"][j])
            if want is None:
                continue
            owner_checked += 1
            got = out["action_items"][i]["owner"]
            owner_right += int(got is not None and roles.get(got) == want)
        kept = sum(len(out[k]) for k in ("overview", "key_points", "decisions", "action_items"))
        row = {
            "meeting": meeting,
            "split": split,
            "lines": len(lines),
            "seconds": round(seconds, 1),
            "kept_items": kept,
            "dropped_ungrounded": out["dropped_ungrounded"],
            "actions": {
                "hyp": len(hyp_actions),
                "ref": len(ref["actions"]),
                "tp": len(action_pairs),
            },
            "decisions": {
                "hyp": len(hyp_decisions),
                "ref": len(ref["decisions"]),
                "tp": len(decision_pairs),
            },
            "owner": {"checked": owner_checked, "right": owner_right},
            "example": {
                "actions": out["action_items"][:3],
                "reference_actions": ref["actions"][:3],
            },
        }
        rows.append(row)
        print(
            f"{meeting}: {len(lines)} lines {seconds:.0f}s | actions {len(action_pairs)}/"
            f"{len(hyp_actions)} hyp, {len(ref['actions'])} ref | decisions {len(decision_pairs)}/"
            f"{len(hyp_decisions)}, {len(ref['decisions'])} ref | owner {owner_right}/{owner_checked}"
            f" | dropped {out['dropped_ungrounded']}",
            flush=True,
        )
        (OUT / "summary.json").write_text(json.dumps(rows, indent=1, default=str))

    def prf(key: str) -> str:
        tp = sum(r[key]["tp"] for r in rows)
        hyp = sum(r[key]["hyp"] for r in rows)
        ref = sum(r[key]["ref"] for r in rows)
        p = tp / hyp if hyp else float("nan")
        rc = tp / ref if ref else float("nan")
        f = 2 * p * rc / (p + rc) if hyp and ref and p + rc else 0.0
        return f"P {p:.2f} R {rc:.2f} F1 {f:.2f} (tp {tp}, hyp {hyp}, ref {ref})"

    checked = sum(r["owner"]["checked"] for r in rows)
    right = sum(r["owner"]["right"] for r in rows)
    kept = sum(r["kept_items"] for r in rows)
    dropped = sum(r["dropped_ungrounded"] for r in rows)
    print(f"\n{len(rows)} meetings")
    print("actions   ", prf("actions"))
    print("decisions ", prf("decisions"))
    print(f"owner right on matched actions naming a role: {right}/{checked}")
    print(f"items dropped for citing no real line: {dropped} of {kept + dropped}")
    print(f"median seconds per meeting: {sorted(r['seconds'] for r in rows)[len(rows) // 2]}")
    print("SUMMARY_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
