# Facilitator script

Read the parts in quotation marks aloud and word for word, so that every
participant hears the same thing. Do not help during a task. If asked, say
"Do whatever you would normally do."

## Before anyone arrives

- [ ] Signed in to VisioVox as the facilitator account, on the study laptop.
- [ ] Recordings A and B processed, with speakers renamed, and chosen on
      `/study`. Tasks and keys entered (they are kept in this browser).
- [ ] A **third, training** recording is open in another tab.
- [ ] Headphones plugged in, with the volume at a comfortable middle level.
- [ ] Two copies of the information sheet and consent form, and a pen.
- [ ] The participant code ready: the next unused P-number, in order. Never
      skip a number: the code decides the counterbalancing.

## 1. Welcome (3 min)

> "Thanks for coming. We've built a website that separates the voices in a
> recorded conversation, and we want to see whether it actually helps. We're
> testing the website, not you, so if something is confusing, that's useful
> for us to know. Please read this sheet, and ask me anything."

Give them the information sheet and consent form. Once the form is signed, tick
the consent box on `/study` and check that the participant code is right.

## 2. Training (4 min, on the training recording)

> "This is VisioVox. Each person in the video gets their own voice."

Show each of these once, briefly, on the **training** recording only:

1. Pick a person in the list → only their voice plays.
2. Click a face in the video → the same thing.
3. The transcript: click a line to jump there, and search it with the box.
4. The mixer: turn people up and down.

> "And this is the normal player you'll also use: the same video and the
> same controls, but only the original sound."

Show the baseline: open the training project's video in a plain player, or
describe it as "just play, pause and seek".

> "You'll do three short questions with one of them, then three with the
> other, on two different recordings. Each question has a Start button.
> Read the question first if you like; the clock starts when you press Start.
> Press Done when you've typed your answer. Try to be quick and accurate, but
> don't worry if you can't find something. There's a limit of three minutes
> a question, and I'll tell you if you reach it."

## 3. The two parts (12 to 16 min)

Press **Start session**. Let the participant drive the laptop. Sit to the
side, slightly behind, and stay quiet.

- **At 3:00 on a task,** say: "Please give your best answer and press Done."
- **Between parts,** say: "Now the same kind of questions on a different
  recording, with the other player."
- **If something breaks** (the video won't load, the page errors), note the
  participant code and the task. Then let them carry on if possible, or end
  the session. A participant who doesn't finish both parts is excluded, which
  is decided in advance, so don't re-run them under the same code.

## 4. Questionnaire (3 min)

> "Last part. These ten statements are about VisioVox, the website with the
> separated voices, not the normal player. Go with your first reaction. Then
> tell us which you'd rather use, and anything else you'd like to say."

Press **Finish and save**. Check that the page says "Saved".

## 5. Debrief (1 min)

> "Thank you, that's everything. Your answers are stored under the code
> P\_\_, not your name. If you'd like them removed, tell us that code before
> [date]. Please don't tell other people what the questions were until we've
> finished, since they might take part too."

File the consent form away from the laptop.

## After the last participant

1. On `/study`, press **Export results (CSV)**.
2. Score the T3 rows by hand: put 1 or 0 in the `correct` column. Hide or
   ignore the `condition` column while scoring.
3. Run `uv run python scripts/analyse_user_study.py visiovox-user-study.csv`.
4. Report what it prints. Include the non-significant results and the threats
   to validity (README §8).
