"""Cross-stream arbitration tests.

The load-bearing case is `test_weak_suppression_still_arbitrates`: each
extractor keeps the other person only 3 dB down, which is what AV-MossFormer2
does on AMI. Alone, no fixed threshold can tell its person's speech from the
leak. Compared against the other stream, it can.
"""

from __future__ import annotations

import numpy as np

from pipeline.arbitration import FRAME, ArbitrationConfig, activity, apply

RATE = 16_000


def _voice(seconds: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (0.1 * rng.standard_normal(int(seconds * RATE))).astype(np.float32)


def _turns() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Person A speaks 0-2 s, person B 2-4 s."""
    a = np.concatenate([_voice(2, 1), np.zeros(2 * RATE, np.float32)])
    b = np.concatenate([np.zeros(2 * RATE, np.float32), _voice(2, 2)])
    return a, b, a + b


def test_weak_suppression_still_arbitrates() -> None:
    a, b, mix = _turns()
    leak = 10 ** (-3 / 20)
    streams = np.stack([a + leak * b, b + leak * a])
    act = activity(streams, mix, ArbitrationConfig(margin_db=2.0, floor_db=-20.0, hold_s=0.0))
    first, second = slice(20, 180), slice(220, 380)  # away from the turn change
    assert act[0, first].all() and not act[0, second].any()
    assert act[1, second].all() and not act[1, first].any()


def test_empty_stream_is_never_active() -> None:
    a, _b, mix = _turns()
    streams = np.stack([a, np.zeros_like(a)])
    act = activity(streams, mix)
    assert not act[1].any()


def test_silence_is_never_active() -> None:
    a, b, mix = _turns()
    pad = np.zeros(RATE, np.float32)
    streams = np.stack([np.concatenate([a, pad]), np.concatenate([b, pad])])
    act = activity(streams, np.concatenate([mix, pad]), ArbitrationConfig(hold_s=0.0))
    assert not act[:, -RATE // FRAME + 20 :].any()


def test_overlap_keeps_both() -> None:
    a, b = _voice(2, 3), _voice(2, 4)
    act = activity(np.stack([a, b]), a + b, ArbitrationConfig(margin_db=3.0, floor_db=-10.0))
    assert act[:, 20:180].all()


def test_apply_gates_and_preserves_shape() -> None:
    a, b, mix = _turns()
    streams = np.stack([a + 0.7 * b, b + 0.7 * a])
    gated, _act = apply(streams, mix, ArbitrationConfig(margin_db=2.0, floor_db=-20.0, hold_s=0.0))
    assert gated.shape == streams.shape
    # Stream 0 is muted during B's turn.
    assert np.abs(gated[0, int(2.5 * RATE) : int(3.5 * RATE)]).max() < 1e-6
    assert np.allclose(
        gated[0, int(0.5 * RATE) : int(1.5 * RATE)], streams[0, int(0.5 * RATE) : int(1.5 * RATE)]
    )
