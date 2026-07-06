# Scripts Layout

## scripts/dev
Developer convenience scripts for local work.

## scripts/pipeline
End-to-end pipeline runners and smoke-test flows (`run_market_pipeline.ps1`).

Note: one-off patch scripts that regex-rewrote source files during past debugging
sessions (`scripts/patches/`, root `patch_*.py`) were removed in the 2026-07-05 repo
cleanup once confirmed already-applied and unreferenced. If you need to make a
similar targeted fix in the future, prefer a normal editor change or a small,
reviewable diff over a standalone patch script.
