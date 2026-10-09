"""S11: meeting summary and action items, each tied to where it was said (ADR-0021).

A local language model (Qwen2.5-7B-Instruct via Ollama, on the workstation's
GPU) reads the speaker-attributed transcript and writes an overview, key
points, decisions and action items with their owners. Language models invent
things, so nothing is shown on the model's word alone: every sentence - the
overview included - must cite transcript line ids, and the citations are
checked here:

- a cited id that is not a real line is dropped;
- an item left with no real citation is dropped (and counted);
- an action item's owner must be one of the speakers, or it has none.

The page shows each item with a play button for the lines it cites, so a
reader can hear the evidence in one click. A citation proves the item is
*about* those lines, not that it is a faithful reading of them; the page says
so.
"""

from __future__ import annotations

import json
import re
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

MODEL = "qwen2.5:7b-instruct"
# Lines per model call. A long meeting is summarised in parts, each part's
# cited items kept as they are: a 7B model's attention degrades well before
# its context limit.
CHUNK_LINES = 220
# A chunk the model cannot answer is halved until this size.
MIN_SPLIT_LINES = 30
TIMEOUT_S = 600
CITATION = re.compile(r"L(\d+)")


@dataclass(frozen=True)
class Line:
    id: str  # "L12"
    speaker: int  # ordinal
    start_ms: int
    text: str


def lines_from_captions(captions: dict[int, dict[str, Any]]) -> list[Line]:
    """Every speaker's caption segments as one conversation, earliest first.

    The same merge the browser does (lib/playback/transcript.ts): blank
    segments dropped, ordered by start then speaker. Ids are L1, L2, ... in
    that order.
    """
    raw: list[tuple[int, int, str]] = []
    for ordinal, body in captions.items():
        for seg in body.get("segments", []):
            text = str(seg.get("text", "")).strip()
            if text:
                raw.append((int(seg.get("start_ms", 0)), ordinal, text))
    raw.sort(key=lambda r: (r[0], r[1]))
    return [Line(f"L{i}", ordinal, start, text) for i, (start, ordinal, text) in enumerate(raw, 1)]


def _clock(ms: int) -> str:
    s = ms // 1000
    if s < 3600:
        return f"{s // 60:02d}:{s % 60:02d}"
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


# Bounded lists keep the answer short enough to finish: the schema is
# enforced while the model writes, so it cannot list forever.
_LINES = {"type": "array", "items": {"type": "string"}, "maxItems": 6}


def _cited(most: int) -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": most,
        "items": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "lines": _LINES},
            "required": ["text", "lines"],
        },
    }


SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "overview": _cited(3),
        "key_points": _cited(8),
        "decisions": _cited(8),
        "action_items": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "owner": {"type": ["string", "null"]},
                    "task": {"type": "string"},
                    "due": {"type": ["string", "null"]},
                    "lines": _LINES,
                },
                "required": ["owner", "task", "due", "lines"],
            },
        },
    },
    "required": ["overview", "key_points", "decisions", "action_items"],
}

SYSTEM = """You summarise meeting transcripts. Each transcript line looks like
[L12 03:41 S2 Priya] what was said
where L12 is the line id and S2 is the speaker code.

Write JSON with:
- overview: 1-3 short sentences on what the conversation was about.
- key_points: the main points made.
- decisions: things the participants agreed or decided. Empty if none.
- action_items: concrete things someone said they or another person will do.
  owner is the speaker code (like "S2") of the person who will do it, or null if
  unclear; due is the deadline as said ("by Friday"), or null.
  Empty if nobody committed to anything.

Rules: use only what is in the transcript. Every sentence and item lists the
ids of the lines that support it, written like "L12". Do not guess at
feelings, motives or anything not said. Do not invent names, numbers, dates
or tasks. Write in English, briefly."""


def _render(lines: Sequence[Line], names: dict[int, str]) -> str:
    def who(ordinal: int) -> str:
        return names.get(ordinal, f"Speaker {ordinal}")

    return "\n".join(
        f"[{ln.id} {_clock(ln.start_ms)} S{ln.speaker} {who(ln.speaker)}] {ln.text}" for ln in lines
    )


# (temperature, token budget) per attempt. The budget bounds a model that
# starts repeating itself, which would otherwise write until the context is
# full; a cut-off answer is invalid JSON, and the warmer second attempt
# usually breaks the loop.
ATTEMPTS = ((0.1, 3000), (0.4, 3000))


def _chat(host: str, system: str, user: str, model: str) -> dict[str, Any]:
    for temperature, budget in ATTEMPTS:
        body = json.dumps(
            {
                "model": model,
                "stream": False,
                "format": SCHEMA,
                "keep_alive": "2m",  # give the GPU back to separation soon after
                "options": {"temperature": temperature, "num_ctx": 16384, "num_predict": budget},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        ).encode()
        request = urllib.request.Request(  # noqa: S310 - the local model server
            f"{host}/api/chat", data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
            reply = json.loads(response.read())
        try:
            parsed: dict[str, Any] = json.loads(reply["message"]["content"])
        except json.JSONDecodeError:
            continue
        return parsed
    raise RuntimeError("the summary model's answer was cut off twice")


def citations(entries: Any, valid: set[str]) -> list[str]:
    """The real line ids an item cites, in order.

    Models write "L12", "12", "[L12 00:41 S2 Priya]" or "L12-L14"; every
    L<number> in an entry counts, a bare number counts, and only ids that are
    real lines survive.
    """
    ids: set[str] = set()
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, int):
            ids.add(f"L{entry}")
        elif isinstance(entry, str):
            found = CITATION.findall(entry)
            ids.update(f"L{n}" for n in found)
            if not found and entry.strip().isdigit():
                ids.add(f"L{entry.strip()}")
    return sorted((i for i in ids if i in valid), key=lambda i: int(i[1:]))


def grounded(raw: dict[str, Any], valid: set[str], speakers: set[int]) -> dict[str, Any]:
    """Keep only what cites real lines; owners must be real speakers."""

    def owner(value: Any) -> int | None:
        if not isinstance(value, str):
            return None
        code = value.strip().upper().lstrip("S")
        return int(code) if code.isdigit() and int(code) in speakers else None

    out: dict[str, Any] = {}
    dropped = 0
    for key in ("overview", "key_points", "decisions"):
        kept = []
        for item in raw.get(key, []) or []:
            if not isinstance(item, dict) or not str(item.get("text", "")).strip():
                continue
            cited = citations(item.get("lines"), valid)
            if cited:
                kept.append({"text": str(item["text"]).strip(), "lines": cited})
            else:
                dropped += 1
        out[key] = kept
    actions = []
    for item in raw.get("action_items", []) or []:
        if not isinstance(item, dict) or not str(item.get("task", "")).strip():
            continue
        cited = citations(item.get("lines"), valid)
        if not cited:
            dropped += 1
            continue
        due = item.get("due")
        actions.append(
            {
                "owner": owner(item.get("owner")),
                "task": str(item["task"]).strip(),
                "due": str(due).strip() if isinstance(due, str) and due.strip() else None,
                "lines": cited,
            }
        )
    out["action_items"] = actions
    out["dropped_ungrounded"] = dropped
    return out


def summarise(
    lines: Sequence[Line], names: dict[int, str], host: str, model: str = MODEL
) -> dict[str, Any]:
    """Summary of a conversation, every item citing real line ids."""
    valid = {ln.id for ln in lines}
    speakers = {ln.speaker for ln in lines} | set(names)
    roster = ", ".join(f"S{o} = {n}" for o, n in sorted(names.items()))
    chunks = [lines[i : i + CHUNK_LINES] for i in range(0, len(lines), CHUNK_LINES)]

    def part(chunk: Sequence[Line]) -> list[dict[str, Any]]:
        """One chunk's grounded summary - or, if the model cannot finish an
        answer for it, its two halves' (down to MIN_SPLIT_LINES)."""
        try:
            raw = _chat(
                host, SYSTEM, f"Speakers: {roster}\n\nTranscript:\n{_render(chunk, names)}", model
            )
        except RuntimeError:
            if len(chunk) < 2 * MIN_SPLIT_LINES:
                raise
            half = len(chunk) // 2
            return part(chunk[:half]) + part(chunk[half:])
        return [grounded(raw, valid, speakers)]

    parts = [p for c in chunks for p in part(c)]
    result: dict[str, Any] = {
        # A long meeting's overview is each part's first sentence, in order,
        # so every sentence still points at its evidence.
        "overview": (
            parts[0]["overview"]
            if len(parts) == 1
            else [p["overview"][0] for p in parts if p["overview"]]
        )
        if parts
        else [],
        "key_points": [k for p in parts for k in p["key_points"]],
        "decisions": [d for p in parts for d in p["decisions"]],
        "action_items": [a for p in parts for a in p["action_items"]],
        "dropped_ungrounded": sum(p["dropped_ungrounded"] for p in parts),
        "model": model,
        "line_count": len(lines),
        # id -> [start_ms, speaker], so the page can play an item's evidence.
        "lines": {ln.id: [ln.start_ms, ln.speaker] for ln in lines},
    }
    return result
