# Contributing a submission

Results reach the leaderboard as pull requests to this repo. Each PR adds one
submission bundle under `submissions/`. The tooling that builds and submits a
bundle lives in the [gptnt](https://github.com/GPTNT/gptnt) package.

## Bundle layout

A bundle is one flat folder holding its manifest and payload:

```text
submissions/YYYYMMDD_<display-slug>_<capfp8>_<suite>_<ver>/
    submission.yaml         # manifest: submitter, players, suite identity, provenance
    experiments.parquet     # interactive payload   (OR)
    metrics.json            # statics payload
```

The folder name is the run date, the model's leaderboard display name, the first
eight characters of its capability fingerprint, and the measured `<suite>_<ver>`.
One model measured across several suites is several bundles, so several PRs. The
leaderboard aggregates across a model's bundles, so submit as many complete
bundles as you like.

A submission is pinned to a suite `(name, revision)`. That pin is validated
against the canonical `suites.lock` shipped with gptnt, which is the record of
which suite revisions the leaderboard accepts.

Everything in `submission.yaml` is machine-generated except the `submitter`
block, which you fill in by hand with your name and contact.

## Contributor flow

1. **Build the bundle.**

   ```bash
   gptnt submission new
   ```

   This measures your run and writes one bundle folder per measured suite, each
   with its own `submission.yaml` alongside its payload.

2. **Fill in the `submitter` block** in each generated `submission.yaml`. This
   is the only part you edit by hand.

3. **Open the PR.**

   ```bash
   gptnt submission submit
   ```

   This opens one pull request per bundle against this repo.

Once the PR is open, CI runs `gptnt submission validate` on every changed
bundle. Validation checks the `submitter` block is filled, the suite pin is
still accepted, and the payload matches its manifest. The PR merges once
validation passes and a maintainer reviews it.
