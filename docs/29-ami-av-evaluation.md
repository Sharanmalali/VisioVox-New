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
word lands under some visible face (§8 shows the audio cue cannot).

## 6. Follow-up: face identity for track merging (2026-10-08, deployed)

`scripts/bench_ami_identity.py` reruns the production S3 grouping on every
meeting's cached extractor output. Seats are ground truth (a track's seat is
where its face is). Speaking tracks only, so counts differ from §2.

| | voice only (before) | face, then voice ([ADR-0018](adr/0018-face-identity-track-merging.md)) |
|---|---|---|
| Fitting meetings, 79 seats | 103 people · 24 splits · 2 wrong merges | 79 · 0 · 0 |
| **Held-out meetings, 38 seats** | 49 people · 11 splits · 1 wrong merge | **38 · 0 · 0** |

Threshold 0.3 by the pre-declared rule; every threshold from 0.3 to 0.6 gave no
wrong merge on the fitting meetings. Live-site E2E on TS3009a: 4 speakers for 4
people (voice alone gave 5).

## 7. Follow-up: caption language (2026-10-08, user choice deployed)

Whisper detects the language per track. On AMI-AV (English, spoken largely by
Dutch speakers) 7 of 117 tracks were judged non-English (nl, et, is, cy), and
those captions come out in that language — seen live: an English remark
captioned in Dutch. Detecting once on the whole recording is not a fix: 0
wrong tracks on the fitting meetings but 8 on the held-out ones (two meetings
judged Dutch throughout) against 3 per track (`scripts/bench_ami_language.py`).
The dependable remedy is a user-chosen language at upload, now deployed
(commit d86b0f9): a "Spoken language" picker, default "Detect automatically",
offering exactly Whisper large-v3's 100 languages (`visiovox_api/languages.py`,
pinned to faster-whisper's list by `tests/test_languages.py`), Indian languages
first in their own scripts. India: Assamese, Bengali, Gujarati, Hindi, Kannada,
Malayalam, Marathi, Nepali, Punjabi, Sanskrit, Sindhi, Tamil, Telugu, Urdu are
covered; Odia, Maithili, Konkani, Kashmiri, Manipuri, Bodo, Dogri and Santali
are not (the recognizer lacks them; an Indic model such as AI4Bharat's would be
needed). Accuracy for the Indian languages has not been measured here. Live E2E
on TS3009a with English chosen: all four speakers captioned in English, where
automatic detection had captioned one in Dutch.

## 8. Follow-up: off-screen speech by retention (2026-10-08, rejected)

Can the retention of the track that kept a caption word tell that nobody on
screen said it? `scripts/bench_ami_offscreen.py` labels each caption word
on/off-screen from AMI words and face visibility. AUC of retention for
off- vs on-screen words: 0.64 on the fitting meetings, **0.38 held-out** —
no better than chance. Moving low-retention words (< −5 dB, fitted) to an
"off-screen" caption stream lowers held-out saWER 78.2 → 73.7 % only by
discarding uncertain words, and raises cpWER 68.7 → 78.0 %. Not deployed;
off-screen attribution needs a visual active-speaker decision.

## 9. Follow-up: conversation insights (2026-10-09, face-anchored diarization deployed)

The project page reports, per person, talk time and share, turns, pace and
who interrupted whom, plus a timeline of when people talked over each other
(`ml/pipeline/insights.py`, mirrored line for line in
`apps/web/src/lib/playback/insights.ts`; both pinned to the hand-computed
`packages/contracts/examples/insights.json`). Definitions: words ≤ 500 ms
apart form a spurt; an overlap onset is B starting ≥ 200 ms into A's spurt —
a *backchannel* if B's spurt is < 1 s and ends inside A's, an *interruption*
if A stops within 2 s and B outlasts A, otherwise *talk-over*.

`scripts/bench_ami_insights.py` scores these against the same insights from
AMI's hand-aligned words, all 30 meetings (`insights.json`,
`insights_summary.json`). Interruption: same interrupter and interrupted
within ±1 s. Overlap: frames inside the displayed overlap regions.

| System | Talk-share MAE | Overlap P / R / F1 | Interruption P / R / F1 (241 ref.) |
|---|---|---|---|
| Caption words of the separated tracks | 13.1 pp [10.2, 16.4] | 0.28 / 0.59 / 0.38 | 0.05 / 0.11 / 0.07 |
| **Face-anchored diarization** (deployed) | **12.9 pp** [9.0, 17.2] | **0.78 / 0.76 / 0.77** | **0.34 / 0.15 / 0.21** |
| pyannote 3.1, labels named by the reference (oracle, not achievable) | 6.4 pp [4.6, 8.5] | 0.78 / 0.76 / 0.77 | 0.52 / 0.24 / 0.33 |

Caption words undercount overlap by construction: the cross-track dedup that
makes captions readable (ADR-0017) removes words exactly where two people
speak at once. **Face-anchored diarization** (`ml/pipeline/diarize.py`,
ADR-0019) takes *when* from pyannote 3.1 on the mixture and *who* from the
faces: pyannote's anonymous labels are paired one-to-one with face tracks by
how much each overlaps that face's caption words (Hungarian assignment). No
reference is used. It doubles overlap F1 and triples interruption F1 at the
same talk-share error (difference −0.2 pp [−2.7, +2.5]).

The design was chosen on the 20 fitting meetings only
(`scripts/select_insights_design.py`, `insights_design.json`): one-to-one
pairing beat "each label to its best face" (talk share 11.3 vs 15.4 pp) and
Jaccard-normalised pairing on every metric. On the 10 held-out meetings the
gains in overlap (F1 0.80 vs 0.42) and interruptions (0.11 vs 0.08) held,
but talk share was **worse** than caption words (16.1 vs 13.1 pp, n = 10):
when a label is paired with the wrong face, all of that label's talk time
moves with it. The remaining gap to the oracle (+6.5 pp [+3.2, +10.6]) is
that naming error, and it traces to visibility (§2): TS3003a's most
talkative participant is tracked for 11 % of the window, so their words — and
the label they anchor — land on other faces.

Interruption detection is weak for every system, the oracle included (F1
0.33): AMI's interruptions are short and the 2 s yield rule is coarse. The
panel therefore presents interruptions as *moments to review* and says so.
