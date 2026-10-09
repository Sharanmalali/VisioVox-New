# VisioVox paper (IEEE conference format)

`main.tex` + `references.bib` → `main.pdf`, IEEEtran conference class.

## Build

```bash
python paper/scripts/make_figures.py   # data figures + data/summary.json   (repo .venv: matplotlib)
python paper/scripts/make_tables.py    # tables/*.tex, generated from data/
python paper/scripts/make_ami_tables.py  # tables/ami_*.tex + data/ami-numbers.json, from data/ami/
python paper/scripts/make_screens.py   # UI figures (needs data/screens/, not committed)
cd paper && latexmk -pdf main.tex
```

Three diagrams (Figs. 2–4) are TikZ inside `main.tex`. Figures 1, 6, 7, 8 and Tables II–VIII are
generated from `data/`, so a number in the paper cannot drift from its source. Numbers quoted in
the text of Secs. VIII–IX are in `data/ami-numbers.json`. `data/` is not committed (repo-wide
ignore); every file in it is regenerated from the sources below.

## Where every number comes from

| Paper item | Source |
|---|---|
| Permutation study (Sec. IV, Fig. 1) | `docs/27-phase1-baseline-report.md` §2–3 (scripts `measure_permutation_full.py`, `run_baseline.py`) |
| SepFormer harness check, ASD matrix, AMI mapping | `docs/27` §6, §8 |
| Model size, training configs (Table I) | `~/runs/{c1,c3,c2-v2}/*.pt` (`config`, `model_config`, `extra`) |
| Training curves (Fig. 6) | `data/train-*.json` ← `~/runs/*/log.json` |
| Product condition (Table II, Fig. 7) | `data/eval-product.log`, `data/eval-diagnostic.log`, `data/eval-c2v3.log` ← `scripts/eval_overlap.py` |
| AV-MossFormer2 test (Table III) | `data/avmossformer2-voxceleb2-pairs.json` ← `~/runs/moss-overlap-test/` |
| Production latency (Table IV, Fig. 8) | `data/production-jobs.csv` ← production `jobs`/`job_stages` tables |
| AMI-AV (Sec. VIII, Tables V–VI) | `data/ami/*.json` ← `~/runs/ami-av-bench/` (`scripts/bench_ami_*.py`, `scripts/analyse_ami.py`; `docs/29`) |
| Heavy-overlap caption check (Sec. VIII) | `data/ami/overlap-captions.json` ← `scripts/check_overlap_captions.py` |
| Conversation insights (Sec. IX, Table VII) | `data/ami/insights{,_summary,_design}.json` ← `scripts/bench_ami_insights.py`, `analyse_ami_insights.py`, `select_insights_design.py`; `docs/29` §9, ADR-0019 |
| Summaries (Sec. IX, Table VIII) | `data/ami/llm_summary.json` ← `scripts/bench_ami_summary.py`; `docs/29` §10, ADR-0021 |
| Translation screening, speed (Sec. IX) | ADR-0020 (40 targets screened 2026-10-09; 100 segments in 0.7 s) |
| Clip timing (Sec. IX) | live end-to-end run, 2026-10-09; ADR-0022 |
| Integration defects (Table IX) | `docs/track-w/DECISIONS.md`, commits `c3af647`, `c672339`, `54819d5`, `9050b27`, `a6b7459`, `166aa02`; export faults ADR-0022 |
| UI screenshots (Figs. 5, 9) | the PAPER step of the Playwright end-to-end run against the production site, 2x; faces blurred (`scripts/make_screens.py`) |

## Before submission

- Authors and affiliations are filled in; anonymise `\author{...}` if the venue reviews double-blind.
- Claims the evidence does not support are listed in Sec. XII; keep them there.
- AV-MossFormer2, NLLB-200 and Qwen2.5 are pretrained models and are credited as such throughout.
- 14 pages: check the venue's page limit. Sec. IX (conversation understanding) is self-contained if
  it has to move to a separate paper.
