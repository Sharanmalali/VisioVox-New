# ADR-0024: Per-speaker dubbing that keeps the room

- **Status:** accepted
- **Date:** 2026-10-10

## Context

Translated captions (ADR-0020) let someone *read* a conversation in their
own language. Many people would rather *hear* it: viewers who read slowly,
or anyone sharing a recording with family who speak only Kannada or Hindi.
Ordinary dubbing throws away the whole soundtrack and re-records it. That
loses the room: the laughter, the people who weren't dubbed, the ambience.
It also takes one voice actor per speaker.

VisioVox already has every person separated, and every line timed and
attributed. Those are exactly the inputs a dub needs.

## Decision

**S12, on request,** runs as `visiovox.dub_project` on the GPU worker, one
per project and language (at most 6 per project):

1. **Translate.** NLLB (ADR-0020) translates the captions into the target
   language, reusing a finished translation if one exists.
2. **Voice.** Each speaker gets a voice of their own from Indic Parler-TTS
   (AI4Bharat, pretrained, used as is), with its named speakers per language
   (15 languages: as, bn, en, gu, hi, kn, ml, mni, mr, ne, or, pa, sa, ta,
   te). The voice is chosen by speaker order. When a language has more
   speakers than named voices, the pitch is varied. The model runs in its own
   environment (`~/venvs/tts`, `services/worker-gpu/tts/run_tts.py`) because
   parler-tts pins a transformers version the worker does not share. It
   generates 8 lines per batch with a fixed seed and runs offline from the
   pre-downloaded weights.
3. **Place.** Each line starts where the original line started.
   - **Slot.** A line's slot runs to 120 ms before the same speaker's next
     line. People may talk over each other, as they did in the original, but
     never over themselves.
   - **Speed-up.** A line longer than its slot is sped up with ffmpeg
     `atempo`, which keeps pitch, by at most ×1.35. Beyond that it overruns.
     Overruns are counted and shown, never hidden.
4. **Keep the room.** The room is `mixture − Σ g_k·s_k` over the dubbed
   speakers. The `s_k` are the packaged separated tracks, which S9
   loudness-normalises, so each track's scale inside the mixture, `g_k`, is
   recovered by least squares rather than assumed. Speakers with no
   translated lines are not subtracted, so they stay in the original
   language.
5. **Assemble.** Each new voice is matched to that speaker's original speech
   level (75th percentile of active-frame RMS). The voices and the room are
   summed at 24 kHz and peak-normalised to 0.95. The picture is copied
   unchanged and the result is muxed to `dubs/<lang>/dub.mp4`.

The panel under the player plays the dub. It also shows which voice each
person got and the fit statistics: the share of lines that fit as spoken,
were sped up, or ran over.

## Honesty

- **No voice cloning.** The synthetic voices are named, generic Indic
  Parler-TTS speakers, and the speakers' own voices are not imitated. The
  panel says the dub is machine-translated and synthetic. Cloning a real
  person's voice from a recording they did not consent to is out of scope by
  design.
- **The room is a subtraction.** Wherever separation leaked, a little of the
  original voice remains under the dub. The panel says so.
- **Pretrained models only.** NLLB and Indic Parler-TTS are both pretrained,
  and both are credited as such.

## Measured

- **Smoke test** (RTX workstation, fp16):
  - The model loads in about 35 s.
  - Unbatched, it runs at about 2.2× real time. A batch of 3 Hindi lines
    produced 12.0 s of speech in 17.3 s.
- **Whisper large-v3 round trip** of those 3 lines: the transcripts match the
  input text up to punctuation and one spelling variant (हफ्ते / हफ़ते).

### Fitting the lines in time (revised after the first end-to-end run)

The first live dub was test_video2: two stand-up comedians, about
3.5 words/s, 15 lines. Only 13 % of lines fitted as spoken, and 73 % **ran
over**. An overrun line played over the same speaker's next line, so one
voice spoke twice at once. Measured causes and fixes (same 15 lines):

| Change | Lines on time | Note |
| --- | --- | --- |
| First version (moderate pace, untrimmed, overlap self) | 27 % unpushed | median overrun 1.16 s |
| Trim each line's silent edges (TTS pads 0.3–0.9 s per side) | | median overrun 0.63 s |
| Ask for a *fast* pace | | median overrun 0.25 s; Whisper CER 0.07–0.10, against 0.13 at moderate |
| **Never overlap yourself**: an overrun pushes the speaker's next line later; pauses absorb the lag | 60 % | 40 % start late (median 3.4 s, max 4.7 s) on this monologue |

- **Trimming is safe.** On the same audio, Whisper CER was 0.133 untrimmed and
  0.118 trimmed.
- **Why this clip lags.** It is a worst case: a continuous monologue, and Hindi
  runs about 1.4× longer than the English even at a fast pace. Lag is made up
  only where the speaker pauses.
- **What the panel shows.** It states the share of late lines and the largest
  lag.
- **Not done.** Condensing the translation to fit was left out on purpose. It
  would change what people said.

## Consequences

- One more 3.6 GB model on disk (`~/.cache/huggingface`), gated on Hugging
  Face. Access was accepted under the project's HF account.
- A dub of a 5-minute meeting takes a few minutes, mostly synthesis. It is
  asynchronous, and the panel polls.
- The worker needs no new Python dependencies. The TTS environment is
  separate, like the AV-TSE one.
