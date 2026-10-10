"""The TTS runner's pure helpers (services/worker-gpu/tts/run_tts.py, ADR-0024)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "run_tts", ROOT / "services" / "worker-gpu" / "tts" / "run_tts.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_tts"] = module
    spec.loader.exec_module(module)
    return module


tts = _load()
RATE = 1000


def test_a_one_word_line_may_take_about_a_second() -> None:
    assert tts.limit_ms("तो.") == tts.FLOOR_MS
    assert tts.limit_ms("So.") == tts.FLOOR_MS


def test_the_limit_grows_with_the_text_and_ignores_punctuation() -> None:
    short, long = (
        tts.limit_ms("Okay."),
        tts.limit_ms("Okay, so we need to decide the budget today."),
    )
    assert long > short
    assert tts.limit_ms("Okay...!!") == tts.limit_ms("Okay")


def test_real_speech_rates_stay_under_the_limit() -> None:
    # About 130 ms per character was measured on AMI lines in Hindi and Kannada.
    text = "हमें शुक्रवार तक रिपोर्ट भेजनी है"
    letters = sum(1 for c in text if not c.isspace())
    assert 130 * letters < tts.limit_ms(text)


def test_cut_shortens_and_fades_out() -> None:
    wav = np.ones(3000, dtype=np.float32)
    out = tts.cut(wav, RATE, 1000)
    assert len(out) == 1000
    assert out[0] == 1.0
    assert out[-1] == 0.0
    assert len(tts.cut(wav[:500], RATE, 1000)) == 500


def test_trim_removes_silent_edges_and_keeps_a_margin() -> None:
    wav = np.concatenate([np.zeros(400), np.ones(300), np.zeros(600)]).astype(np.float32)
    out = tts.trim(wav, RATE)
    margin = round(tts.MARGIN_S * RATE)
    assert len(out) == 300 + 2 * margin


def test_babble_silence_and_good_takes_are_told_apart() -> None:
    rate = 1000
    word = "Yeah."
    speech = np.full(500, 0.3, dtype=np.float32)
    assert tts.fault(word, speech, rate) is None
    assert tts.fault(word, np.full(3000, 0.3, dtype=np.float32), rate) == "long"
    assert tts.fault(word, np.zeros(0, dtype=np.float32), rate) == "short"
    assert tts.fault(word, np.full(100, 0.3, dtype=np.float32), rate) == "short"
    assert tts.fault(word, np.full(500, 0.001, dtype=np.float32), rate) == "short"


def test_the_floor_stays_below_real_speech() -> None:
    # Real lines run 80-140 ms per character; the floor is 40.
    text = "हमें शुक्रवार तक रिपोर्ट भेजनी है"
    assert tts.floor_ms(text) < 80 * sum(1 for c in text if not c.isspace())
    assert tts.floor_ms("हाँ.") == tts.MIN_FLOOR_MS
