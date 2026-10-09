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

On very short prompts ("तो.", "So.") the model sometimes keeps talking -
seconds of babble after the word. A line far longer than its text allows
(`limit_ms`) is generated again with other seeds; if every try is too long,
the shortest is cut at the limit (the word comes first, the babble after).
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


# Duration guard: generously above real speech (~80-140 ms per character
# measured on AMI lines), so only babble trips it.
BASE_MS = 400
PER_CHAR_MS = 200
FLOOR_MS = 1000
RETRY_SEEDS = (1, 2)
FADE_S = 0.03


def limit_ms(text: str) -> int:
    """The longest a line of `text` may plausibly take to say."""
    import unicodedata

    chars = sum(1 for c in text if not unicodedata.category(c).startswith(("P", "Z", "S", "C")))
    return max(FLOOR_MS, BASE_MS + PER_CHAR_MS * chars)


def cut(wav: Any, rate: int, ms: int) -> Any:
    """`wav` cut to `ms`, faded out so the cut does not click."""
    import numpy as np

    n = round(ms * rate / 1000)
    if len(wav) <= n:
        return wav
    out = np.array(wav[:n], dtype=np.float32)
    fade = min(len(out), round(FADE_S * rate))
    if fade:
        out[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return out


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

    def generate(batch: list[dict[str, Any]]) -> list[Any]:
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
        out = []
        for k in range(len(batch)):
            length = int(gen.audios_length[k]) if hasattr(gen, "audios_length") else None
            audio = gen.sequences[k, :length] if length else gen.sequences[k]
            out.append(trim(audio.to(torch.float32).cpu().numpy().reshape(-1), rate))
        return out

    def too_long(ln: dict[str, Any], wav: Any) -> bool:
        return len(wav) * 1000 / rate > limit_ms(ln["text"])

    # The same request gives the same voices.
    set_seed(0)
    t1 = time.time()
    audio: dict[int, Any] = {}
    for start in range(0, len(lines), BATCH):
        batch = lines[start : start + BATCH]
        for ln, wav in zip(batch, generate(batch), strict=True):
            audio[ln["id"]] = wav
        log(f"{min(start + BATCH, len(lines))}/{len(lines)} lines")

    # Babble: try again with other seeds, keep the first that fits, else cut.
    by_id = {ln["id"]: ln for ln in lines}
    flagged = [i for i in audio if too_long(by_id[i], audio[i])]
    retried, cut_lines = len(flagged), 0
    for seed in RETRY_SEEDS:
        if not flagged:
            break
        set_seed(seed)
        still = []
        for start in range(0, len(flagged), BATCH):
            ids = flagged[start : start + BATCH]
            for i, wav in zip(ids, generate([by_id[i] for i in ids]), strict=True):
                if not too_long(by_id[i], wav):
                    audio[i] = wav
                elif len(wav) < len(audio[i]):
                    audio[i] = wav
                    still.append(i)
                else:
                    still.append(i)
        flagged = still
    for i in flagged:
        audio[i] = cut(audio[i], rate, limit_ms(by_id[i]["text"]))
        cut_lines += 1
    if retried:
        log(f"{retried} line(s) ran long; {retried - cut_lines} regenerated, {cut_lines} cut")

    results = []
    for ln in lines:
        path = out_dir / f"line_{ln['id']}.wav"
        sf.write(path, audio[ln["id"]], rate)
        results.append(
            {
                "id": ln["id"],
                "wav": str(path),
                "duration_ms": round(len(audio[ln["id"]]) * 1000 / rate),
            }
        )

    spoken = sum(r["duration_ms"] for r in results) / 1000
    took = time.time() - t1
    log(f"synthesised {spoken:.1f}s of speech in {took:.1f}s")
    Path(args.result).write_text(
        json.dumps(
            {
                "sample_rate": rate,
                "lines": results,
                "seconds": took,
                "speech_s": spoken,
                "retried": retried,
                "cut": cut_lines,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
