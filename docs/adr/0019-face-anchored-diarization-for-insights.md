# ADR-0019: Face-anchored diarization for conversation insights

- **Status:** accepted
- **Date:** 2026-10-09
- **Evidence:** docs/29 §9

## Context

The project page now reports who spoke, for how long, and when people talked
over each other. These numbers need *when* each person spoke. The separated
tracks' caption words give *who* reliably (each track is tied to a face) but
*when* poorly: on AMI-AV they found overlap with F1 0.38, because the
cross-track dedup (ADR-0017) deletes words exactly where people overlap.
An audio-only diarizer has the opposite profile — F1 0.77 for overlap — but
its labels are anonymous, and a product has no reference to name them.

## Decision

Run pyannote 3.1 on the mixture in the caption stage (S7) and pair its
labels one-to-one with the face tracks, maximising each label's overlap with
that face's caption words (`ml/pipeline/diarize.anchor`). A label that cannot
be paired is dropped, not guessed. The per-person intervals ride in each
speaker's caption JSON as `activity` (the manifest schema is frozen; the
caption file is not). The browser computes insights from `activity` when every
speaker has it and from caption words otherwise; word counts always come from
the captions. Diarization failing never fails a job.

## Consequences

- Overlap F1 0.38 → 0.77 and interruption F1 0.07 → 0.21 on 30 meetings, at
  the same talk-share error (12.9 vs 13.1 pp). On the held-out ten, talk share
  was worse (16.1 vs 13.1 pp); reported, not hidden.
- One more model in the worker (pyannote, loaded once per worker process);
  its cost per job is part of the S7 stage time shown under Processing details.
- Insights inherit the face-visibility limit: speech while a face is off
  screen can anchor a label to the wrong person.
- Projects processed before this keep word-based insights until reprocessed.
