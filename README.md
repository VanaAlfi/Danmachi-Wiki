# Orario Ledger

Unofficial DanMachi wiki using the project's English novel corpus.

## Build

Requires Python 3.12 or later; no third-party packages.

```sh
python tools/build.py
```

The generated site is in `dist/`. For a local preview, run `python tools/build.py --serve` and open http://127.0.0.1:8770/.

## GitHub Pages

In repository Settings → Pages, choose GitHub Actions as the source. The deployment workflow builds and checks the site before uploading only `dist/`. Pushes to `main` publish updates; the workflow can also be run manually.

Private repositories require an eligible GitHub plan for Pages. A private source repository does not by itself make the published website private.

## Scope

This repository includes wiki articles, source metadata, static assets and the site generator. It deliberately excludes novels, raw reference texts, private research, local paths, artwork folders and unrelated projects. No claim of ownership of DanMachi characters or source works is made.
