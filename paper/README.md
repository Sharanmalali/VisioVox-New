# VisioVox paper (IEEE conference format)

`main.tex` + `references.bib` → `main.pdf`, IEEEtran conference class.

## Build

```bash
python paper/scripts/make_figures.py   # data figures + data/summary.json   (repo .venv: matplotlib)
python paper/scripts/make_tables.py    # tables/*.tex, generated from data/
python paper/scripts/make_screens.py   # UI figures (needs data/screens/, not committed)
cd paper && latexmk -pdf main.tex
```

Three diagrams (Figs. 2–4) are TikZ inside `main.tex`. Figures 1, 6, 7, 8 and Tables II–IV are
generated from `data/`, so a number in the paper cannot drift from its source.

## Where every number comes from

| Paper item | Source |
|---|---|
| Permutation study (Sec. IV, Fig. 1) | `docs/27-phase1-baseline-report.md` §2–3 (scripts `measure_permutation_full.py`, `run_baseline.py`) |
| SepFormer harness check, ASD matrix, AMI mapping | `docs/27` §6, §8 |
| Model size, training configs (Table I) | `~/runs/{c1,c3,c2-v2}/*.pt` (`config`, `model_config`, `extra`) |
| Training curves (Fig. 6) | `data/train-*.json` ← `~/runs/*/log.json` |
| Product condition (Table II, Fig. 7) | `data/eval-product.log`, `data/eval-diagnostic.log` ← `scripts/eval_overlap.py` |
| AV-MossFormer2 test (Table III) | `data/avmossformer2-voxceleb2-pairs.json` ← `~/runs/moss-overlap-test/` |
| Production latency (Table IV, Fig. 8) | `data/production-jobs.csv` ← production `jobs`/`job_stages` tables |
| Integration defects (Table V) | `docs/track-w/DECISIONS.md`, commits `c3af647`, `c672339`, `54819d5` |
| UI screenshots (Fig. 5) | Playwright end-to-end runs against the production site; faces blurred |

## Before submission

- Authors and affiliations are anonymised (`\author{...}`); fill them in for a camera-ready.
- Claims the evidence does not support are listed in Sec. X; keep them there.
