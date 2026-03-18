# Training Artifacts

This folder stores trained model artifacts and metadata.

## Files

- *.joblib -> serialized sklearn model artifact
- *.json -> model metadata / feature list / metrics
- evals/*.json -> evaluation summaries

## Suggested naming convention

{model_name}_{market_code}_lb{lookback}.joblib

Examples:
- f_v6_rec_yds_lb5.joblib
- idge_v1_pass_yds_lb5.joblib

## Git policy

Large binary artifacts should generally not be committed long-term unless needed for demo or reproducibility.
Prefer keeping:
- metadata JSON
- eval JSON
- only a small number of representative artifacts

and ignoring repeated experimental binaries.
