"""Speak translated caption lines with Indic Parler-TTS (ADR-0024).

Runs in its own environment (~/venvs/tts: parler-tts pins a transformers
version the worker's environment does not share), the way the AV-TSE runner
does. The worker writes a request, this writes one WAV per line and a result
file:

    request: {"lines": [{"id": 0, "text": "...", "description": "..."}], "out_dir": "..."}
    result:  {"sample_rate": 44100, "lines": [{"id": 0, "wav": "...", "duration_ms": 1830}]}

Lines are generated in small batches, each with its own description (the
voice), and cut back to their own length from the padded batch output. The
model pads every line with 0.3-0.9 s of silence at each end, which would
otherwise eat into the time the line has to fit; that is trimmed off.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

MODEL = "ai4bharat/indic-parler-tts"
BATCH = 8
# Edge trimming: 10 ms frames quieter than this fraction of the line's loudest
# frame are silence; a little is kept either side so no consonant is clipped.
SILENCE = 0.02
MARGIN_S = 0.05


def trim(wav: Any, rate: int) -> Any:
    """`wav` without its leading and trailing silence."""
    import numpy as np

    frame = max(1, rate // 100)
    n = len(wav) // frame
    if n == 0:
        return wav
    rms = np.sqrt(np.mean(np.square(wav[: n * frame].reshape(n, frame)), axis=1))
    loud = np.flatnonzero(rms > SILENCE * float(rms.max()))
    if loud.size == 0:
        return wav
    margin = round(MARGIN_S * rate)
    start = max(0, int(loud[0]) * frame - margin)
    end = min(len(wav), (int(loud[-1]) + 1) * frame + margin)
    return wav[start:end]


def log(message: str) -> None:
    sys.stdout.write(f"{time.strftime('%H:%M:%S')} tts: {message}\n")
    sys.stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--request", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()

    import soundfile as sf
    import torch
    from parler_tts import ParlerTTSForConditionalGeneration
    from transformers import AutoTokenizer, set_seed

    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    out_dir = Path(request["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [ln for ln in request["lines"] if str(ln.get("text", "")).strip()]

    t0 = time.time()
    device = args.device if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    model = ParlerTTSForConditionalGeneration.from_pretrained(MODEL, torch_dtype=dtype).to(device)
    prompt_tok = AutoTokenizer.from_pretrained(MODEL)
    desc_tok = AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path)
    rate = int(model.config.sampling_rate)
    log(f"model loaded in {time.time() - t0:.1f}s on {device}; {len(lines)} line(s)")

    # The same request gives the same voices.
    set_seed(0)
    results = []
    t1 = time.time()
    for start in range(0, len(lines), BATCH):
        batch = lines[start : start + BATCH]
        d = desc_tok([ln["description"] for ln in batch], return_tensors="pt", padding=True).to(
            device
        )
        p = prompt_tok([ln["text"] for ln in batch], return_tensors="pt", padding=True).to(device)
        with torch.no_grad():
            gen = model.generate(
                input_ids=d.input_ids,
                attention_mask=d.attention_mask,
                prompt_input_ids=p.input_ids,
                prompt_attention_mask=p.attention_mask,
                do_sample=True,
                return_dict_in_generate=True,
            )
        for k, ln in enumerate(batch):
            length = int(gen.audios_length[k]) if hasattr(gen, "audios_length") else None
            audio = gen.sequences[k, :length] if length else gen.sequences[k]
            wav = trim(audio.to(torch.float32).cpu().numpy().reshape(-1), rate)
            path = out_dir / f"line_{ln['id']}.wav"
            sf.write(path, wav, rate)
            results.append(
                {"id": ln["id"], "wav": str(path), "duration_ms": round(len(wav) * 1000 / rate)}
            )
        log(f"{min(start + BATCH, len(lines))}/{len(lines)} lines")

    spoken = sum(r["duration_ms"] for r in results) / 1000
    took = time.time() - t1
    log(f"synthesised {spoken:.1f}s of speech in {took:.1f}s")
    Path(args.result).write_text(
        json.dumps(
            {"sample_rate": rate, "lines": results, "seconds": took, "speech_s": spoken}, indent=1
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
