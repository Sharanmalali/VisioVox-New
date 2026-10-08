# ADR-0020: Translated captions with NLLB-200

- **Status:** accepted
- **Date:** 2026-10-09

## Context

Captions are written in the language each person spoke. Indian meetings mix
languages, and many readers need the conversation in a language other than
the one spoken - including languages Whisper cannot caption at all.

## Decision

On request, translate every speaker's captions into one chosen language with
**NLLB-200 distilled 1.3B** (Meta AI, pretrained; CC-BY-NC 4.0, which this
non-commercial project satisfies), run on the GPU worker through CTranslate2 —
the engine faster-whisper already uses, so no new runtime dependency.

- The model is converted once from `facebook/nllb-200-distilled-1.3B`
  (`ct2-transformers-converter --quantization int8_float16`, with
  `transformers` added only for that command) to
  `~/models/nllb-200-distilled-1.3B-ct2` (1.4 GB), and loaded from disk.
- Each caption segment is translated as one sentence and keeps its timing;
  word timings are dropped, because translation reorders words.
- One `translations` row per (project, language); asking again returns the
  same translation, a failed one is retried in place, at most 12 per project.
  Files: `projects/<id>/translations/<lang>/spk_<n>.json`, signed on read.
- The page shows the translation in the transcript and the player's caption,
  with the original under each line; search and every export use it.

Targets: English, 17 scheduled languages of India — four of which (Odia,
Maithili, Kashmiri, Manipuri) cannot be captioned — and 22 world languages.
Every target was checked by translating three English sentences: Santali and
Konkani came back as SentencePiece unknown marks and untranslated English with
the released model, and are not offered; the worker also refuses any output
in which more than a fifth of the segments contain unknown marks.

## Consequences

- Speed on the workstation (A5000): 100 caption segments in 0.7 s once the
  model is loaded (4.7 s, once per worker process).
- Translation quality is NLLB's own and has **not** been measured on this
  system; the interface labels it machine translation. Kashmiri's test output
  mistranslated "meeting", a reminder that low-resource targets are weaker.
- Share pages show the original only; translations are an owner's feature.
- A one-off model download grew the WSL disk until the host drive filled
  (2026-10-09); the download cache was removed after conversion. Large model
  downloads on this machine need free space checked on the drive holding the
  WSL disk, not only inside it.
