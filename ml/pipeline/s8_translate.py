"""S8: translate captions with NLLB-200 (ADR-0020).

Each caption segment is translated as one sentence, keeping its timing, so a
translated caption shows exactly when the original would. Word timings do not
survive translation - word order changes - and translated captions carry none.

Runs NLLB-200 distilled 1.3B through CTranslate2, the engine faster-whisper
already uses on this worker, so translation adds a model but no runtime
dependency. The model is converted once from facebook/nllb-200-distilled-1.3B
(see docs/adr/0020) and loaded from disk, never fetched during a job.

NLLB's input is SentencePiece pieces framed by language tags:
`[src_lang] + pieces + ["</s>"]`, decoding with the target tag as prefix.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODEL_NAME = "nllb-200-distilled-1.3B"
MAX_INPUT_PIECES = 400  # NLLB's positions stop at 512; captions are far shorter
BATCH_TOKENS = 4096
# SentencePiece's mark for a piece it cannot write. Seen for every sentence in
# Santali and Konkani with the released model; any language where it shows up
# this often is failing, and gets an error instead of a page of marks.
UNKNOWN = "\u2047"
MAX_UNKNOWN_SHARE = 0.2


class TranslationError(RuntimeError):
    """The model cannot produce a usable translation into this language."""


@dataclass
class Translator:
    model: Any  # ctranslate2.Translator
    pieces: Any  # sentencepiece.SentencePieceProcessor


def load_translator(model_dir: str | Path, device: str = "cuda") -> Translator:
    import ctranslate2  # type: ignore[import-untyped]
    import sentencepiece

    root = Path(model_dir).expanduser()
    if not (root / "model.bin").is_file():
        raise FileNotFoundError(f"no converted NLLB model at {root}")
    sp = sentencepiece.SentencePieceProcessor()
    sp.Load(str(root / "sentencepiece.bpe.model"))
    use = device if device == "cpu" or ctranslate2.get_cuda_device_count() > 0 else "cpu"
    model = ctranslate2.Translator(
        str(root), device=use, compute_type="int8_float16" if use == "cuda" else "int8"
    )
    return Translator(model=model, pieces=sp)


def translate(texts: Sequence[str], source: str, target: str, translator: Translator) -> list[str]:
    """Translate each text from FLORES-200 `source` to `target`, in order.

    Blank texts stay blank, and nothing is sent when the languages match.
    """
    if source == target:
        return list(texts)
    out = ["" for _ in texts]
    todo = [i for i, t in enumerate(texts) if t.strip()]
    if not todo:
        return out
    batch = []
    for i in todo:
        pieces = translator.pieces.EncodeAsPieces(texts[i].strip())[:MAX_INPUT_PIECES]
        batch.append([source, *pieces, "</s>"])
    results = translator.model.translate_batch(
        batch,
        target_prefix=[[target]] * len(batch),
        beam_size=4,
        max_batch_size=BATCH_TOKENS,
        batch_type="tokens",
        max_decoding_length=256,
        repetition_penalty=1.1,
    )
    for i, result in zip(todo, results, strict=True):
        hypothesis = result.hypotheses[0]
        # The first token is the target tag the decoder was primed with.
        if hypothesis and hypothesis[0] == target:
            hypothesis = hypothesis[1:]
        out[i] = translator.pieces.DecodePieces(hypothesis).strip()
    return out


def translate_transcript(
    body: dict[str, Any], source: str, target: str, target_code: str, translator: Translator
) -> dict[str, Any]:
    """A caption file (S9's spk_N.json shape) with its segments translated."""
    segments = [s for s in body.get("segments", []) if isinstance(s, dict)]
    texts = [str(s.get("text", "")) for s in segments]
    translated = translate(texts, source, target, translator)
    written = [t for t in translated if t]
    if written and sum(UNKNOWN in t for t in written) / len(written) > MAX_UNKNOWN_SHARE:
        raise TranslationError("the translation model cannot write this language")
    return {
        "language": target_code,
        "source_language": body.get("language"),
        "model": MODEL_NAME,
        "segments": [
            {
                "text": text,
                "start_ms": s.get("start_ms"),
                "end_ms": s.get("end_ms"),
                "words": [],
            }
            for s, text in zip(segments, translated, strict=True)
        ],
    }
