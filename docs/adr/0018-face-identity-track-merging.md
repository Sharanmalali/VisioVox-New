# ADR-0018 — Face tracks are merged into people by face, then voice

- **Status:** Accepted
- **Date:** 2026-10-08
- **Related:** [`29-ami-av-evaluation.md`](../29-ami-av-evaluation.md) §2 and §6, `ml/pipeline/face_identity.py`, `services/worker-gpu/src/worker_gpu/av_tse.py` (`group_speakers`), `scripts/bench_ami_identity.py`, ADR-0008 (face data lifecycle)

## Context

S3 joins time-disjoint face tracks of one person (a shot cut, a turn of the
head, someone leaving and coming back) into one speaker. It decided by voice
alone: ECAPA on each track's extracted audio, cosine > 0.6. On AMI-AV that
split people: an extracted track carries the room a few dB down, which blurs
the voice print. In the product a split person is two "Speaker" tracks for one
face.

The extractor already crops every face track (224×224); a face crop contains
nobody else.

## Decision

Each speaking track gets an ArcFace vector (insightface `buffalo_sc`,
MobileFaceNet; mean of 8 frames sampled through its crops). A track joins a
person whose face matches above cosine **0.3**; where a face cannot be
embedded, the voice rule applies unchanged. Tracks that overlap in time are
never merged. Face vectors exist only in memory for the job (ADR-0008).

The threshold was chosen on the 20 AMI-AV fitting meetings by a rule fixed in
advance (minimise split persons + 2 × wrong merges; ties to the higher
threshold). Every value from 0.3 to 0.6 gave no wrong merge there.

| | voice only | face, then voice |
|---|---|---|
| Fitting meetings (79 seats with speech) | 103 people, 24 splits, 2 wrong merges | 79 people, 0, 0 |
| **Held-out meetings (38 seats)** | 49 people, 11 splits, 1 wrong merge | **38 people, 0, 0** |

## Consequences

- One face, one speaker track, on everything measured. Verified in the real
  pipeline on an AMI meeting (5 tracks → 4 people, face match 0.83) and on the
  two-face videos (unchanged).
- Adds ~1–2 s of CPU per job (8 crops per track).
- AMI faces are distinct adults at fixed seats. Identical twins or near-identical
  look-alikes who never appear together could be merged; the voice embedder no
  longer guards that case. Not observed; noted.
