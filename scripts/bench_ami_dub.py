"""Per-speaker dubbing on AMI-AV (ADR-0024, docs/29 section 11).

Three questions about the dub as shipped (ml/pipeline/s12_dub.py,
services/worker-gpu/tts/run_tts.py), on the same 30 meetings and 120 s
windows as the separation benchmark (scripts/bench_ami_av.py, whose cached
arrays this reuses). Nothing here is tuned: every constant is the product's.

1. Leakage - how much of each original voice is left under the dub.
   The room is the mixture minus the production AV-MossFormer2 tracks, with
   gains by least squares (s12_dub.room), exactly as the worker builds it.
   Against each participant's equalised headset (the ground truth), per
   reliable seat (bench_ami_av's rule):
     * solo_db    level of the room relative to the mixture in frames where
                  only this person speaks: how much quieter their original
                  voice is under the dub (0 dB = not removed at all);
     * active_db / overlap_db   s12_dub.residual_db - the energy along their
                  headset, room vs mixture - over the frames where they speak,
                  and where they speak over someone else.
   A seat the separator gave no track keeps its voice; those are counted.
   The AMI-AV mixture is the sum of the headsets, so it has no ambience of its
   own: whether the room's *other* sounds survive cannot be measured here.

2. Fit - how much of the dub stays in time.
   Lines are AMI's hand-aligned words per participant, split where they pause
   0.5 s or more, after sentence-final punctuation once a line has 4 words,
   or at 25 words (close to Whisper's caption segments); fillers (um, uh, mm)
   are dropped, as Whisper usually drops them. Translated by the deployed
   NLLB, voiced by the deployed runner, placed by s12_dub.plan, scored by
   s12_dub.fit_stats - into Hindi and Kannada, and re-voiced in English as a
   control: English needs no translation, so it separates the synthetic
   voice's own pace from the translation's length.

3. Intelligibility - does the synthetic speech say the words?
   Whisper large-v3 transcribes every voiced line with the language forced;
   character error rate against the text it was given (punctuation and
   spacing removed, NFC). Also Whisper's own language identification, not
   forced. Whisper's errors on Indian languages are part of these numbers, so
   CER is an upper bound on the voice's. Hindi, Kannada and English on every
   line; Bengali, Tamil, Telugu and Marathi on a 60-line sample.

Everything is cached under each meeting's `dub/` folder.

    uv run python scripts/bench_ami_dub.py                    # all phases
    uv run python scripts/bench_ami_dub.py --limit 1          # smoke test
    uv run python scripts/bench_ami_dub.py --phase report     # rescore from cache
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from bench_ami_av import MAX_BLEED_DB, MIN_SPEECH_S, N_SEATS, OUT, VAD_RATE, meetings  # noqa: E402
from defusedxml.ElementTree import parse as parse_xml  # noqa: E402

from eval.ami_words import UNPACKED, agents_by_channel  # noqa: E402
from pipeline import s12_dub  # noqa: E402
from pipeline.vad import FRAME_SAMPLES  # noqa: E402

WINDOW_S = 120.0
FIT_LANGS = ("hi", "kn", "en")
SAMPLE_LANGS = ("bn", "ta", "te", "mr")
SAMPLE_LINES = 60
PAUSE_S = 0.5
MAX_LINE_WORDS = 25
MIN_SENTENCE_WORDS = 4
FILLERS = {"um", "uh", "hmm", "mm", "mm-hmm", "uh-huh", "mhm", "er", "erm", "ah", "eh"}
SOURCE = "eng_Latn"
TTS_PYTHON = Path.home() / "venvs" / "tts" / "bin" / "python"
RUNNER = ROOT / "services" / "worker-gpu" / "tts" / "run_tts.py"
NLLB_DIR = Path.home() / "models" / "nllb-200-distilled-1.3B-ct2"
RESULTS = OUT / "dub_results.json"


def log(message: str) -> None:
    sys.stdout.write(f"{time.strftime('%H:%M:%S')} {message}\n")
    sys.stdout.flush()


# ---------------------------------------------------------------- leakage --


def level_db(room: np.ndarray, mixture: np.ndarray, s: np.ndarray) -> float:
    """Room level relative to the mixture over the samples `s`."""
    if not s.any():
        return float("nan")
    e_room = float(np.sum(np.square(room[s], dtype=np.float64)))
    e_mix = float(np.sum(np.square(mixture[s], dtype=np.float64)))
    return float(10 * np.log10(max(e_room, 1e-20) / max(e_mix, 1e-20)))


def along_db(room: np.ndarray, ref: np.ndarray, mixture: np.ndarray, s: np.ndarray) -> float:
    """s12_dub.residual_db over the samples `s`."""
    if not s.any():
        return float("nan")
    return s12_dub.residual_db(room[s], ref[s], mixture[s])


def leakage(work: Path) -> list[dict[str, Any]]:
    eq = np.load(work / "equalised.npz")
    av = np.load(work / "av_tracks.npz")
    refs, mixture, masks = eq["refs"], eq["mixture"], eq["masks"]
    estimates = np.asarray(av["estimates"], dtype=np.float32)
    has = [bool(np.any(estimates[k])) for k in range(N_SEATS)]
    tracks = [estimates[k] for k in range(N_SEATS) if has[k]]
    gains = s12_dub.track_gains(mixture, tracks)
    room = s12_dub.room(mixture, tracks, gains)

    def samples(frames: np.ndarray) -> np.ndarray:
        keep = np.repeat(frames, FRAME_SAMPLES)[: len(mixture)]
        return np.pad(keep, (0, len(mixture) - len(keep)))

    others = masks.sum(axis=0)
    rows = []
    for k in range(N_SEATS):
        active = masks[k]
        speech_s = float(active.sum()) / VAD_RATE
        bleed = float(eq["bleed_db"][k])
        reliable = speech_s >= MIN_SPEECH_S and np.isfinite(bleed) and bleed <= MAX_BLEED_DB
        solo = active & (others == 1)
        overlap = active & (others >= 2)

        rows.append(
            {
                "seat": k,
                "reliable": reliable,
                "has_track": has[k],
                "speech_s": speech_s,
                "solo_s": float(solo.sum()) / VAD_RATE,
                "solo_db": level_db(room, mixture, samples(solo)),
                "active_db": along_db(room, refs[k], mixture, samples(active)),
                "overlap_db": along_db(room, refs[k], mixture, samples(overlap)),
            }
        )
    return rows


# ------------------------------------------------------------------ lines --


def words_with_punctuation(
    meeting: str, channel: int, start_s: float
) -> list[tuple[float, float, str]]:
    """AMI words of one channel in the window, punctuation attached to the word before."""
    agent = agents_by_channel(meeting)[channel]
    root = parse_xml(UNPACKED / "words" / f"{meeting}.{agent}.words.xml").getroot()
    out: list[tuple[float, float, str]] = []
    end_s = start_s + WINDOW_S
    in_window = False  # punctuation belongs to the word before it, if that is in the window
    for w in root.iter("w"):
        text = (w.text or "").strip()
        if w.get("punc") == "true":
            if in_window and out and text:
                a, b, t = out[-1]
                out[-1] = (a, b, t + text)
            continue
        if w.get("starttime") is None or w.get("endtime") is None:
            continue
        a, b = float(w.get("starttime")), float(w.get("endtime"))
        in_window = not (b <= start_s or a >= end_s or text.lower() in FILLERS)
        if in_window:
            out.append((a - start_s, b - start_s, text))
    return out


def lines_of(meeting: str, start_s: float) -> list[s12_dub.Line]:
    lines: list[s12_dub.Line] = []
    for ch in range(N_SEATS):
        groups: list[list[tuple[float, float, str]]] = []
        for word in words_with_punctuation(meeting, ch, start_s):
            current = groups[-1] if groups else []
            if not current or (
                word[0] - current[-1][1] >= PAUSE_S
                or len(current) >= MAX_LINE_WORDS
                or (len(current) >= MIN_SENTENCE_WORDS and current[-1][2][-1:] in ".?!")
            ):
                groups.append([word])
            else:
                current.append(word)
        for group in groups:
            text = " ".join(t for _, _, t in group)
            text = text[:1].upper() + text[1:]
            lines.append(
                s12_dub.Line(ch, round(group[0][0] * 1000), round(group[-1][1] * 1000), text)
            )
    return sorted(lines, key=lambda ln: (ln.start_ms, ln.speaker))


# ------------------------------------------------------------- synthesis --

_TRANSLATOR: Any = None


def translate(texts: list[str], language: str) -> list[str]:
    global _TRANSLATOR
    from pipeline import s8_translate
    from visiovox_api.translation_languages import BY_CODE

    if language == "en":
        return list(texts)
    if _TRANSLATOR is None:
        _TRANSLATOR = s8_translate.load_translator(NLLB_DIR, "cuda")
    return s8_translate.translate(texts, SOURCE, BY_CODE[language].nllb, _TRANSLATOR)


def synthesise(folder: Path, texts: list[str], voices: list[str]) -> dict[int, dict[str, Any]]:
    """Run the deployed runner; {line id: {"wav", "duration_ms"}}. Cached."""
    result = folder / "tts-result.json"
    if not result.is_file():
        request = folder / "tts-request.json"
        request.write_text(
            json.dumps(
                {
                    "out_dir": str(folder / "tts"),
                    "lines": [
                        {"id": i, "text": t, "description": v}
                        for i, (t, v) in enumerate(zip(texts, voices, strict=True))
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        env = {**os.environ, "HF_HUB_OFFLINE": "1"}
        subprocess.run(  # noqa: S603
            [str(TTS_PYTHON), str(RUNNER), "--request", str(request), "--result", str(result)],
            check=True,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    body = json.loads(result.read_text(encoding="utf-8"))
    return {int(r["id"]): r for r in body["lines"]} | {-1: {"seconds": body["seconds"]}}


def voice_language(todo: list[tuple[str, Path, float]], language: str) -> None:
    """Translate and voice every meeting window in one runner call.

    Writes each meeting's dub/<lang>/lines.json; the runner's own timing for
    the whole batch goes to dub-batch/<lang>/tts-result.json.
    """
    pending = [
        (meeting.name, start_s)
        for _, meeting, start_s in todo
        if not (OUT / meeting.name / "dub" / language / "lines.json").is_file()
    ]
    if not pending:
        return
    per_meeting = {name: lines_of(name, start_s) for name, start_s in pending}
    flat = [(name, ln) for name, lines in per_meeting.items() for ln in lines]
    texts = translate([ln.text for _, ln in flat], language)
    voices = {
        name: s12_dub.assign_voices(language, sorted({ln.speaker for ln in lines}))
        for name, lines in per_meeting.items()
    }
    folder = OUT / "dub-batch" / language
    folder.mkdir(parents=True, exist_ok=True)
    synth = synthesise(folder, texts, [voices[name][ln.speaker].description for name, ln in flat])
    for name, _lines in per_meeting.items():
        rows = [
            {
                "speaker": ln.speaker,
                "start_ms": ln.start_ms,
                "end_ms": ln.end_ms,
                "source": ln.text,
                "text": texts[i],
                "wav": synth[i]["wav"] if i in synth else None,
                "duration_ms": synth[i]["duration_ms"] if i in synth else 0,
            }
            for i, (m, ln) in enumerate(flat)
            if m == name
        ]
        target = OUT / name / "dub" / language
        target.mkdir(parents=True, exist_ok=True)
        (target / "lines.json").write_text(
            json.dumps({"language": language, "lines": rows}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )


# --------------------------------------------------------------- ASR CER --

_WHISPER: Any = None
PUNCT = re.compile("[\\s\u0964\u0965.,?!;:\"'()\\-\u2013\u2014\u2018\u2019\u201c\u201d]")


def normalise(text: str, language: str) -> str:
    text = unicodedata.normalize("NFC", text)
    if language == "en":
        text = text.lower()
    return PUNCT.sub("", text)


def cer(reference: str, hypothesis: str) -> float:
    a, b = reference, hypothesis
    if not a:
        return 0.0 if not b else 1.0
    d = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, cb in enumerate(b, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (ca != cb))
    return d[len(b)] / len(a)


def transcribe(folder: Path, body: dict[str, Any], language: str) -> list[dict[str, Any]]:
    """Whisper per voiced line, cached as asr.json next to lines.json."""
    global _WHISPER
    cache = folder / "asr.json"
    if cache.is_file():
        return list(json.loads(cache.read_text(encoding="utf-8")))
    if _WHISPER is None:
        from faster_whisper import WhisperModel

        _WHISPER = WhisperModel("large-v3", device="cuda", compute_type="float16")
    out = []
    for ln in body["lines"]:
        if not ln["wav"] or not ln["text"].strip():
            continue
        segments, _ = _WHISPER.transcribe(ln["wav"], language=language, beam_size=5)
        heard = "".join(s.text for s in segments).strip()
        _, info = _WHISPER.transcribe(ln["wav"], beam_size=1, without_timestamps=True)
        out.append(
            {
                "text": ln["text"],
                "heard": heard,
                "cer": cer(normalise(ln["text"], language), normalise(heard, language)),
                "detected": info.language,
            }
        )
    cache.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


# ----------------------------------------------------------------- report --


def summarise_fit(bodies: list[dict[str, Any]], synthesis_s: float) -> dict[str, Any]:
    placements: list[s12_dub.Placement] = []
    spoken_ms = original_ms = 0
    for body in bodies:
        lines = [
            s12_dub.Line(ln["speaker"], ln["start_ms"], ln["end_ms"], ln["text"])
            for ln in body["lines"]
        ]
        durations = [max(1, int(ln["duration_ms"])) for ln in body["lines"]]
        placements += s12_dub.plan(lines, durations)
        spoken_ms += sum(durations)
        original_ms += sum(ln.end_ms - ln.start_ms for ln in lines)
    stats = s12_dub.fit_stats(placements)
    lags = np.asarray([p.lag_ms for p in placements], dtype=float)
    return {
        **stats,
        "p90_lag_ms": float(np.percentile(lags, 90)) if lags.size else 0.0,
        "length_ratio": spoken_ms / max(original_ms, 1),
        "speech_s": spoken_ms / 1000,
        "synthesis_s": synthesis_s,
    }


def summarise_asr(rows: list[dict[str, Any]], language: str) -> dict[str, Any]:
    c = np.asarray([r["cer"] for r in rows], dtype=float)
    return {
        "lines": len(rows),
        "mean_cer": float(c.mean()) if c.size else float("nan"),
        "median_cer": float(np.median(c)) if c.size else float("nan"),
        "share_cer_under_10": float((c < 0.10).mean()) if c.size else float("nan"),
        "language_id": float(np.mean([r["detected"] == language for r in rows])) if rows else 0.0,
    }


def nanmean(values: list[float]) -> float:
    v = np.asarray([x for x in values if np.isfinite(x)], dtype=float)
    return float(v.mean()) if v.size else float("nan")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--phase", choices=("all", "leak", "voice", "asr", "report"), default="all")
    args = ap.parse_args(argv)
    todo = meetings()[: args.limit or None]
    phases = {"leak", "voice", "asr", "report"} if args.phase == "all" else {args.phase}

    leak_rows: list[dict[str, Any]] = []
    for split, meeting, _ in todo:
        work = OUT / meeting.name
        if "leak" in phases or "report" in phases:
            for r in leakage(work):
                leak_rows.append({**r, "meeting": meeting.name, "split": split})
    if "leak" in phases:
        log(f"leakage scored on {len(todo)} meeting(s)")

    if "voice" in phases:
        for language in FIT_LANGS:
            t0 = time.time()
            voice_language(todo, language)
            log(f"{language}: {len(todo)} meeting(s) voiced in {time.time() - t0:.0f}s")
        # The sample languages: every k-th line across the meetings, voiced once.
        pool = [
            (meeting.name, ln)
            for _, meeting, start_s in todo
            for ln in lines_of(meeting.name, start_s)
            if len(ln.text.split()) >= 3
        ]
        step = max(1, len(pool) // SAMPLE_LINES)
        sample = pool[::step][:SAMPLE_LINES]
        for language in SAMPLE_LANGS:
            folder = OUT / "dub-sample" / language
            folder.mkdir(parents=True, exist_ok=True)
            cache = folder / "lines.json"
            if cache.is_file():
                continue
            texts = translate([ln.text for _, ln in sample], language)
            voices = s12_dub.assign_voices(language, sorted({ln.speaker for _, ln in sample}))
            synth = synthesise(folder, texts, [voices[ln.speaker].description for _, ln in sample])
            cache.write_text(
                json.dumps(
                    {
                        "language": language,
                        "synthesis_s": synth[-1]["seconds"],
                        "lines": [
                            {
                                "meeting": m,
                                "speaker": ln.speaker,
                                "start_ms": ln.start_ms,
                                "end_ms": ln.end_ms,
                                "source": ln.text,
                                "text": t,
                                "wav": synth[i]["wav"] if i in synth else None,
                                "duration_ms": synth[i]["duration_ms"] if i in synth else 0,
                            }
                            for i, ((m, ln), t) in enumerate(zip(sample, texts, strict=True))
                        ],
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
            log(f"sample {language}: {len(sample)} lines voiced")

    if "asr" in phases:
        for i, (_, meeting, _) in enumerate(todo, 1):
            for language in FIT_LANGS:
                folder = OUT / meeting.name / "dub" / language
                body = json.loads((folder / "lines.json").read_text(encoding="utf-8"))
                rows = transcribe(folder, body, language)
                log(f"[{i}/{len(todo)}] {meeting.name} {language}: ASR on {len(rows)} lines")
        for language in SAMPLE_LANGS:
            folder = OUT / "dub-sample" / language
            body = json.loads((folder / "lines.json").read_text(encoding="utf-8"))
            transcribe(folder, body, language)
            log(f"sample {language}: ASR done")

    if "report" in phases:
        reliable = [r for r in leak_rows if r["reliable"]]
        with_track = [r for r in reliable if r["has_track"]]
        report: dict[str, Any] = {
            "meetings": len(todo),
            "leakage": {
                "reliable_seats": len(reliable),
                "seats_without_track": len(reliable) - len(with_track),
                "solo_db_mean": nanmean([r["solo_db"] for r in with_track]),
                "solo_db_median": float(np.nanmedian([r["solo_db"] for r in with_track])),
                "active_db_mean": nanmean([r["active_db"] for r in with_track]),
                "overlap_db_mean": nanmean([r["overlap_db"] for r in with_track]),
                "active_db_mean_all_reliable": nanmean([r["active_db"] for r in reliable]),
            },
            "fit": {},
            "asr": {},
        }
        for language in FIT_LANGS:
            bodies, rows = [], []
            for _, meeting, _ in todo:
                folder = OUT / meeting.name / "dub" / language
                if (folder / "lines.json").is_file():
                    bodies.append(json.loads((folder / "lines.json").read_text(encoding="utf-8")))
                if (folder / "asr.json").is_file():
                    rows += json.loads((folder / "asr.json").read_text(encoding="utf-8"))
            batch = OUT / "dub-batch" / language / "tts-result.json"
            seconds = json.loads(batch.read_text())["seconds"] if batch.is_file() else float("nan")
            if bodies:
                report["fit"][language] = summarise_fit(bodies, seconds)
                if batch.is_file():
                    guard = json.loads(batch.read_text())
                    for key in ("retried_long", "retried_short", "cut", "still_short"):
                        report["fit"][language][key] = guard.get(key)
            if rows:
                report["asr"][language] = summarise_asr(rows, language)
        for language in SAMPLE_LANGS:
            path = OUT / "dub-sample" / language / "asr.json"
            if path.is_file():
                rows = json.loads(path.read_text(encoding="utf-8"))
                report["asr"][language] = summarise_asr(rows, language)
        report["leakage_rows"] = leak_rows
        RESULTS.write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
        printable = {k: v for k, v in report.items() if k != "leakage_rows"}
        sys.stdout.write(json.dumps(printable, indent=1, default=float) + "\n")
    log("BENCH_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
