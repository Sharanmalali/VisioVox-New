# Tasks and answer key

Write these **before the first pilot** and keep them unchanged after it. Each
recording (A and B) gets the same three kinds of task, with ids `T1`, `T2` and
`T3`. The analysis script decides how to score a task from its id, so keep the
ids.

Enter the questions and keys on the `/study` setup screen. Keep a copy here,
filled in, in a private place: the keys must never be shown to participants.

## Rules for good tasks

- **One answer.** Each task has one checkable answer.
- **Use names, not speaker numbers.** Name people the way they appear in the
  app (rename the speakers in the project first: "Anna", "Ben", …). On the
  baseline the participant sees only faces, so also say where the person sits:
  "Anna (left)".
- **Make A and B equally hard.**
  - T1 overlaps of similar length (3 to 6 s).
  - T2 topics mentioned exactly once, at a similar depth into the recording.
  - T3 commitments stated once and clearly.
- **Don't name the topic in T2 with the exact words used in the recording** if
  that makes a transcript search trivially easy and listening impossible. Use a
  close paraphrase, the same way for both recordings.

## T1: overlap (scored automatically)

> Between 1:42 and 1:48, Anna (left) and Ben (right) talk at the same time.
> What exactly did **Anna** say? Write her words as closely as you can.

- **Key.** Anna's words in that window, from the AMI manual transcript or a
  careful transcription made from her close-talk microphone.
- **Scoring.** Token F1 between the answer and the key, lower-cased with
  punctuation removed. A score of 0.5 or more counts as correct, and the mean F1
  is reported too.

## T2: find (scored automatically)

> Find the moment where someone first mentions **the price of the remote
> control**. Write the time as mm:ss.

- **Key.** The time, for example `2:37`.
- **Scoring.** Correct if within ±5 seconds of the key.

## T3: follow-up (scored by hand)

> What did someone agree to do before the next meeting, and who is going to do
> it?

- **Key.** For example, "Ben will email the cost spreadsheet to everyone".
- **Scoring.** By hand, 1 or 0. Both the action and the person must be right.

## Filled-in key (keep private)

| Recording | Project | Task | Question as shown | Key |
| --- | --- | --- | --- | --- |
| A | | T1 | | |
| A | | T2 | | |
| A | | T3 | | |
| B | | T1 | | |
| B | | T2 | | |
| B | | T3 | | |
