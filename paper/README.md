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

Three diagrams (Figs. 2–4) are TikZ inside `main.tex`. Figures 1, 6, 7, 8 and Tables II–VI are
generated from `data/`, so a number in the paper cannot drift from its source. Numbers quoted in
the text of Sec. VIII are in `data/ami-numbers.json`. `data/` is not committed (repo-wide ignore);
every file in it is regenerated from the sources below.

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
| Integration defects (Table VII) | `docs/track-w/DECISIONS.md`, commits `c3af647`, `c672339`, `54819d5` |
| UI screenshots (Fig. 5) | Playwright end-to-end runs against the production site; faces blurred |

## Before submission

- Authors and affiliations are filled in; anonymise `\author{...}` if the venue reviews double-blind.
- Claims the evidence does not support are listed in Sec. XI; keep them there.
- AV-MossFormer2 is a pretrained model (ClearerVoice-Studio) and is credited as such throughout.
