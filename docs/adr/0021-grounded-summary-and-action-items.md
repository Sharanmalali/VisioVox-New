# ADR-0021: Summary and action items, every item tied to where it was said

- **Status:** accepted
- **Date:** 2026-10-09
- **Evidence:** docs/29 §10 (`scripts/bench_ami_summary.py`): 30/30 meetings, action items F1 0.34 (lexical floor), owners 15/26, decisions 0/90, 0 of 314 items uncited

## Context

A separated, speaker-attributed transcript is the input a meeting summary
needs most and usually lacks: "who will do what" requires knowing who said
it. Language models write fluent summaries and also invent things; on the
first trial here the model credited a speaker with "losing weight", which
nobody said.

## Decision

On request, the GPU worker runs **Qwen2.5-7B-Instruct** (Apache-2.0, 4.7 GB,
Q4_K_M) through **Ollama** on the workstation - no external service, no
recurring cost, nothing leaves the machine.

- Ollama is unpacked into the user's tree (`~/.local/ollama`, v0.40.2) and
  started by the worker on first use (`tasks._ensure_llm`), so it lives and
  dies with the worker; no new system service. Models in
  `~/.cache/ollama/models`, writable under the worker's `ProtectHome=read-only`.
- The prompt shows lines as `[L12 03:41 S2 Priya] text`. The answer is
  constrained to a JSON schema (overview, key points, decisions, action items
  with owner and due date), every entry carrying the line ids it rests on.
- `s11_summarize.grounded` keeps only entries citing real lines, maps owners to
  real speakers or none, and counts what it dropped. The overview is cited
  sentences too, so nothing on the page is uncited.
- The page shows play buttons for each item's cited lines, labels the summary
  as written by the model, and says how many items were dropped.
- Long meetings are summarised in 220-line parts; each part's cited items are
  kept as they are.
- `keep_alive: 2m` hands the GPU back to separation soon after.

## Consequences

- Grounding is a citation check, not a truth check: an item can cite the
  right lines and still misread them. The interface says "listen before
  relying on it".
- Quality is measured on AMI's own action items and decisions (docs/29 §10),
  given AMI's transcript - i.e. with perfect attribution. With VisioVox's
  separation the transcript is noisier; that combination is not measured.
- Output is English only.
- First request after a worker restart loads the model (about a minute);
  later ones take seconds.
