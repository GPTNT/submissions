# GPTNT Leaderboard Submissions

This repo is the central location for submitting new results to the [GPTNT Leaderboard](https://gptnt.github.io/leaderboard).

## Submitting

Each PR adds one submission bundle under `submissions/`. Its folder name is
`submissions/YYYYMMDD_<display-slug>_<capfp8>_<suite>_<ver>/`. One model
measured across several suites creates several bundles and therefore several
PRs. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full layout and the
`gptnt submission new` to `submit` flow.

## Leaderboard publication

Merging a bundle runs the leaderboard publication workflow. It checks out this repository afresh,
builds the active website artifact from `multi-self-async` and `multi-self-sync` suite revisions
2 or later, and opens or updates one pull request in `GPTNT/gptnt.github.io` only when the JSON
changes. It never reads a maintainer's local submissions directory.

Before enabling the workflow, configure these repository settings:

- `GPTNT_LEADERBOARD_RELEASE_REF` variable: an immutable released GPTNT tag or commit that
  contains `gptnt leaderboard build`.
- `WEBSITE_PR_TOKEN` secret: a least-privilege token with contents and pull-request write access
  to `GPTNT/gptnt.github.io`.
- Configure GitHub notifications for this repository and `GPTNT/gptnt.github.io` to receive pull
  request and Actions updates. The publication workflow does not use an external email provider.

## Inspiration

This method of tracking submissions was highly-inspired by [ProgramBench](https://programbench.com), which has their process outlined in [ProgramBench/submissions](https://github.com/programbench/submissions).
