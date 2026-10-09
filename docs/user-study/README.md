# User study: does VisioVox make a recorded conversation easier to use?

The rest of this repository measures the models (SI-SDR, WER, F1 on AMI). This
study measures **people**. It asks whether someone with VisioVox can do real
jobs on a recorded multi-speaker conversation faster and more accurately than
with an ordinary video player, and whether they find it usable.

It is a small, within-subjects, counterbalanced lab study run by the team.
**No result in the paper or anywhere else may be reported until real
participants have taken part.** The analysis script refuses to run on an empty
export, and there is no simulated data anywhere in this folder.

| File | What it is |
| --- | --- |
| [README.md](README.md) | This protocol |
| [tasks-template.md](tasks-template.md) | How to write the six tasks and their answer key |
| [consent-form.md](consent-form.md) | Information sheet and consent form (print two per participant) |
| [facilitator-script.md](facilitator-script.md) | What the facilitator says and does, word for word |
| `/study` in the app | The facilitator's screen: runs the session, times every task, stores it |
| `scripts/analyse_user_study.py` | Scores the export and runs the statistics |

## 1. Research questions and hypotheses

- **RQ1 (speed).** Do people answer questions about a multi-speaker recording
  faster with VisioVox than with a normal player?
  H1: median per-participant task time is lower with VisioVox.
- **RQ2 (accuracy).** Are their answers more often correct?
  H2: per-participant accuracy is higher with VisioVox. This matters most for
  task T1 (overlapped speech), where a normal player gives a mixture.
- **RQ3 (usability).** Is VisioVox usable by first-time users?
  Reported as the System Usability Scale (SUS) score with a 95 % confidence
  interval, read against the usual benchmark of 68 (Bangor et al., 2009).
- **RQ4 (preference).** Which would they rather use? Counts and an exact
  binomial test of VisioVox against the baseline, with "no preference" set aside.

## 2. Design

- **Within subjects.** Every participant uses both conditions.
- **Conditions.**
  - *VisioVox*: the full player. That means per-speaker tracks, captions, the
    interactive transcript, insights, click-a-face and the mixer.
  - *Baseline*: the **same player** with every VisioVox feature removed. It shows
    the picture with the original sound and has no speakers, captions,
    transcript, insights or faces. Using the same player keeps the controls
    (play, seek, speed) identical, so the comparison is about the features and
    not about a different UI.
- **Two recordings, A and B.** Each condition is used on a different recording,
  so the second block can't be answered from memory of the first.
- **Counterbalancing.** The `/study` page assigns a 4-way Latin square from the
  participant number:

  | Participant | Part 1 | Part 2 |
  | --- | --- | --- |
  | P01, P05, P09, … | VisioVox on A | baseline on B |
  | P02, P06, P10, … | baseline on A | VisioVox on B |
  | P03, P07, P11, … | VisioVox on B | baseline on A |
  | P04, P08, P12, … | baseline on B | VisioVox on A |

  Recruit a **multiple of four** so that every cell is filled equally.
- **Tasks.** Each recording has three tasks, the same three kinds on both
  recordings (see [tasks-template.md](tasks-template.md)):
  - **T1, overlap.** Write what one named person said while two people were
    talking at once.
  - **T2, find.** Give the time at which a topic is mentioned.
  - **T3, follow-up.** Say what someone agreed to do and who will do it.

## 3. Participants

- **Number.** 12 to 16 (a multiple of four). With 12 pairs, a Wilcoxon
  signed-rank test has about 80 % power to detect a large paired effect
  (d ≈ 0.9) at α = 0.05. That is the effect size this study can claim and no
  smaller. Say so in the paper.
- **Who.**
  - Students or staff who did not build VisioVox and have not seen it.
  - Fluent in English, which is the language of the recordings.
  - Normal or corrected hearing and vision, self-reported.
- **Pilot.** Run 2 pilot sessions with codes `pilot1` and `pilot2` first. Fix
  task wording, then delete those rows (or leave them: the analysis skips any
  code that is not `P` followed by digits).
- **Anonymity.**
  - Participants are recorded only as a code (`P01`, `P02`, …). The app
    refuses anything that looks like a name.
  - Keep the paper consent forms, which carry names, separately from the codes.
  - Nobody is filmed or audio-recorded. The app stores only timings, typed
    answers, questionnaire ticks and comments.
- **Consent.** The project has no institutional ethics review (see the team's
  decision record). Informed, written consent from every participant is still
  required, using [consent-form.md](consent-form.md). Participants may stop at
  any time, and their row is deleted on request.

## 4. Materials

1. **Two recordings, A and B.** Each should have:
   - 2 to 4 people, all with faces visible (click-a-face needs faces).
   - 3 to 6 minutes of length, and roughly matched to each other in length and
     number of speakers.
   - At least one clear stretch of overlapped speech.

   Good sources:
   - **AMI meetings (CC BY 4.0).** Their manual transcripts give a ground-truth
     answer key for T1, so you don't have to transcribe overlaps by ear. Use a
     5-minute excerpt with the close-up camera views composed side by side.
   - **A conversation the team records themselves.** Everyone filmed must sign
     a recording release first.

   Upload both to the facilitator's account and wait for them to finish
   processing.
2. **The task sheet and answer key** for each recording, entered on the
   `/study` setup screen (saved in that browser only).
3. **A laptop with headphones**, the same for everyone, signed in as the
   facilitator.
4. **Printed consent forms.**

## 5. Procedure (about 25 minutes per participant)

| Step | Minutes |
| --- | --- |
| Welcome, information sheet, consent | 3 |
| Training: a 2-minute tour of VisioVox on a **third** recording, not A or B, and of the baseline player | 4 |
| Part 1: three tasks | 6–8 |
| Part 2: three tasks | 6–8 |
| SUS questionnaire, preference, comments | 3 |
| Thanks and debrief | 1 |

The exact words are in [facilitator-script.md](facilitator-script.md).

Timing runs from the moment the participant presses **Start task**, which is
also when the player appears and the question is already on screen, until they
press **Done**. Answers are typed into the box. Each task has a soft cap of
3 minutes: at 3 minutes the facilitator asks the participant to give their best
answer and press Done. Record such tasks as incorrect unless the answer is right.

## 6. Measures and scoring

| Measure | How |
| --- | --- |
| Task time (s) | Recorded automatically by `/study` |
| T1 correct | Token F1 between the answer and the key ≥ 0.5, computed automatically. Mean F1 is also reported. |
| T2 correct | Within ±5 s of the key time, computed automatically |
| T3 correct | Scored **by hand** as 1 or 0 in the `correct` column of the export, blind to condition where possible (sort by participant, hide the condition column) |
| SUS | Standard scoring, 0 to 100 |
| Preference | VisioVox, baseline or no preference |

A hand score in the `correct` column always overrides the automatic one, so a
scorer can correct an automatic decision.

## 7. Analysis (pre-registered here, before any data)

Run:

```
uv run python scripts/analyse_user_study.py visiovox-user-study.csv
```

The script computes the following, and nothing else is tested:

1. **Time.** Each participant's mean time per condition across their three
   tasks. Report:
   - medians per condition;
   - a two-sided Wilcoxon signed-rank test;
   - the matched-pairs rank-biserial correlation as the effect size.
2. **Accuracy.** Each participant's proportion correct per condition. Report:
   - means per condition;
   - a two-sided Wilcoxon signed-rank test;
   - per-task accuracy as a descriptive table.
3. **SUS.** Report the mean and a 95 % bootstrap confidence interval (10 000
   resamples, seed 0).
4. **Preference.** Report the counts and a two-sided exact binomial test with
   p = 0.5, using only participants who expressed a preference.

Two primary tests (time and accuracy) are run at α = 0.05 each, and the script
also prints Holm-adjusted p-values. Everything else is descriptive.

**Exclusions, decided now.**
- A participant who does not finish both parts is excluded entirely.
- Pilot codes are excluded.
- No other exclusions are made, and no outliers are removed.

## 8. Threats to validity, to state in the paper

- **Small N and a convenience sample.** Participants are classmates, so the
  study supports only large effects and generalises with care.
- **Recording difficulty.** A and B differ. Counterbalancing spreads this
  difference evenly but does not remove it.
- **Novelty and demand effects.** Participants know the team built VisioVox.
  The facilitator script avoids praising it, and the baseline is the same
  player, so it is not a strawman.
- **Task selection.** The tasks are the jobs VisioVox was built for. The study
  shows it helps with those jobs and makes no claim about general video
  viewing.
