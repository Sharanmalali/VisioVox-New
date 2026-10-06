# ADR-0017 — Per-track captions keep each word once

- **Status:** Accepted
- **Date:** 2026-10-01
- **Related:** [`29-ami-av-evaluation.md`](../29-ami-av-evaluation.md), `ml/pipeline/word_dedup.py`, `ml/pipeline/s7_transcribe.py`, `services/worker-gpu/src/worker_gpu/real_pipeline.py` (S7)

## Context

S7 transcribed each person's extracted track and showed those words under that
person's face. On real meetings (AMI-AV, 10 held-out meetings) that put words
under the right face with 110 % error, almost all insertions: every track keeps
the room a few dB down, and Whisper writes that cross-talk down. Even
close-talk headsets transcribed this way score > 100 %.

Two replacements were measured:

| | AMI held-out saWER | Heavy-overlap WER (3 videos) |
|---|---|---|
| per-track (before) | 110.2 % | 4.5 % |
| transcribe the mixture once, attribute words by track | 76.6 % | **67.0 %** — loses a speaker |
| per-track, cross-track duplicates removed | 78.1 % | 5.4 % |

The two replacements are statistically tied on AMI (dedup − transcribe-once:
+1.5 points [−1.6, +4.6], paired bootstrap over meetings); only one survives
the product's hardest case.

## Decision

S7 transcribes each track (Whisper large-v3, Silero VAD on, no conditioning on
previous text — the measured configuration), then removes cross-track
duplicates: a word that appears in several tracks with the same normalised text
and midpoints within 0.5 s is kept only in the track that retains most of the
mixture's energy over it (smoothing 50 ms, fitted on the AMI fitting meetings).
Words that appear once are always kept, which is what preserves genuinely
overlapped speech.

Invariant 6 is unchanged: captions come from the faithful tracks.

## Consequences

- Captions on meetings stop repeating other people's words under every face.
- A word said by someone off screen still lands under a visible face (the
  track that kept most of it). Fixing that needs an active-speaker decision per
  face track; it is the next step named in docs/29.
- Not adopted, with evidence in docs/29: transcribe-once attribution,
  cross-stream audio gating ("arbitration"), SEAVE gap filling, naming
  diarization clusters by face.
