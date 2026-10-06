# 29 — AMI-AV: the deployed system on real meetings

- **Date:** 2026-10-01
- **Scripts:** `scripts/bench_ami_*.py`, `scripts/fit_arbitration.py`, `scripts/analyse_ami.py`, `scripts/check_overlap_captions.py`
- **Results:** `~/runs/ami-av-bench/{results,gapfill,asr,attrib,seat,dedup,arbitration_signal,summary}.json`
- **Decision it led to:** [ADR-0017](adr/0017-per-track-captions-with-cross-track-dedup.md)

Until this report the deployed extractor (AV-MossFormer2 via `worker_gpu.av_tse`)
had been measured on six tracks from three synthetic VoxCeleb2 videos. This is
the first measurement on real recordings, and the first of the captions.

## 1. The benchmark

AMI records each participant with a close-up camera and a close-talking
headset. For each of 30 meetings (TS3003a–TS3012a held out; 10 ES + 10 IS
meetings used only for fitting) the 120 s window from the AMI set builder is
tiled into a 2×2 video of the four close-ups (AMI's channel-to-camera mapping)
and paired with the sum of the headsets. Each system runs exactly as deployed;
people are assigned to seats by face position, never by score.

Three corrections, each measured before it was made:

| Problem | Measurement | Correction |
|---|---|---|
| Energy VAD on headsets | 47 s of "speech" for a participant with no words in the window (headset at −55 dBFS, room within 5 dB) | Activity from AMI's hand-aligned words (±50 ms), `ml/eval/ami_words.py` |
| Headset gains | up to 30 dB apart in one meeting | each headset scaled to −26 dBFS active speech before summing |
| Unusable references | some headsets carry others within a few dB | not used as signal reference if bleed > −10 dB or < 2 s speech (fixed before scoring); 81 of 114 speakers remain |

Also: AV sync verified (extraction quality peaks at 0 offset); PESQ dropped
(P.862 is specified for 8–12 s samples, and the `pesq` C library aborts on some
of these clips).

## 2. The extractor on real meetings (all 30 meetings)

| Finding | Value |
|---|---|
| Speech spoken while the speaker's face is untracked | **14.4 %** (564 s of 3906 s); 20/114 speakers < 90 % covered, 6 never |
| People found for 120 seats | 152; 21 seats split into several people; 3 people spanning two seats |
| Overlap SI-SDRi, speakers visible ≥ 90 % | **+3.48 dB** [2.61, 4.32] (n = 65) |
| Overlap SI-SDRi, all reliable speakers | +1.87 dB [−0.40, 3.54] |
| Output level while the visible person is silent, vs mixture | −3.8 dB [−4.6, −2.9] |
| Whole-clip STOI | 0.54 vs mixture 0.65 |

The model helps on real overlapped speech when it can see the face, loses a
seventh of all speech to visibility, over-splits identities, and barely mutes
the room when its person is silent.

## 3. Captions (held-out meetings, n = 10)

cpWER (CHiME-6, MeetEval) lets streams pair with participants freely;
saWER requires stream *k* to match the person in seat *k* — what the product
needs, since captions sit under faces. Whisper large-v3 for every system.

| System | saWER | cpWER |
|---|---|---|
| Whisper on the mixture, no attribution | — | 31.1 % (plain WER) |
| pyannote 3.1 + Whisper (audio-only) | — | 61.2 % |
| Per-track transcription (deployed until ADR-0017) | 110.2 % | 96.1 % (57.6 % insertions) |
| Whisper on each equal-level *headset* | — | 122.5 % (104.7 % insertions) |
| **Per-track + cross-track dedup** (ADR-0017) | **78.1 %** (−32.1 [−39.0, −24.6]) | 68.7 % (vs pyannote +7.5 [−8.8, +23.4], n.s.) |
| Transcribe mixture once, attribute words by track | 76.6 % | 66.1 % |
| pyannote clusters mapped to faces | 90.5 % | 75.2 % |
| Per-track + cross-stream arbitration (gating) | 92.8 % | 80.4 % |
| Oracle attribution of the mixture transcript | 45.1 % | 45.1 % |

The headset row is the diagnosis: even near-clean close-talk audio yields
> 100 % cpWER, because Whisper transcribes cross-talk 13–25 dB below the talker.
No separator leaks little enough for plain per-track transcription.

**Heavy overlap** (three VoxCeleb2 two-face videos, ~90 % overlap; reference =
Whisper on each clean source, so a consistency check): per-track 4.5 % WER,
per-track + dedup 5.4 %, transcribe-once 67.0 % — it loses one whole speaker
per video. On AMI the two are tied (dedup − transcribe-once: +1.5 points
[−1.6, +4.6] held-out, 0.0 [−2.9, +2.8] over all 30); transcribe-once fails
here, so dedup is what ships.

## 4. Things that did not work (kept, because they are results)

- **Cross-stream arbitration** (`ml/pipeline/arbitration.py`, fitted on the 20
  fitting meetings): activity F1 0.383 → 0.453; cuts per-track cpWER 96 → 80 %;
  but on the audio tracks it does not reduce leakage (+0.28 dB [−0.16, 1.11])
  and costs STOI (−0.12 [−0.14, −0.09]). Not deployed.
- **SEAVE gap filling** (C3, self-enrolled from the person's own visible
  stretches; enrolment cosine to the true voice 0.53): in the hidden stretches
  it is *worse* than passing the mixture (−5.0 vs +3.3 dB SI-SDR, n = 11);
  whole-clip STOI +0.033 [0.013, 0.059] paired, SI-SDRi +1.18 [−0.11, 2.95];
  no caption gain (hybrid + dedup 78.3 % vs 78.1 %). Not deployed.
- **Naming pyannote clusters by face** is worse than per-track dedup on the
  held-out meetings (saWER +13.8 points [7.3, 21.5] vs transcribe-once).

## 5. Open

Attribution is the bottleneck, not separation: an oracle that knows who spoke
each word reaches 45 % saWER on the same transcripts. A learned active-speaker
detector on the face tracks is the obvious next step, and would also decide
which words of an off-screen person belong to nobody on screen — today every
word lands under some visible face.
