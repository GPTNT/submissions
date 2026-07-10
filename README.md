# GPTNT Leaderboard Submissions

This repo is the central location for submitting new results to the [GPTNT Leaderboard](https://gptnt.github.io/leaderboard).


## Submitting

Each PR adds one submission bundle under `submissions/`, a flat folder named `submissions/YYYYMMDD_<display-slug>_<capfp8>_<suite>_<ver>/`. One model measured across several suites is several bundles, so several PRs — submit as many complete bundles as you like. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full layout and the `gptnt submission new` → `submit` flow.


## Inspiration

This method of tracking submissions was highly-inspired by [ProgramBench](https://programbench.com), which has their process outlined in [ProgramBench/submissions](https://github.com/programbench/submissions).
