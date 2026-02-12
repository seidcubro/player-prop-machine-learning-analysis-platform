"""ETL job entrypoint.

This container/job directory is included to match the intended platform
architecture (separate ingestion / feature engineering / training stages).

At the moment, only the ingestion job and the training script under
`services/training/train.py` are wired end-to-end. This entrypoint is kept as a
stable hook for future work so orchestration (Docker Compose/Kubernetes) can
mount a job image without changing paths.

When implemented, this job should:
- pull or compute feature rows (or call the API job endpoints),
- write results back to Postgres and/or an artifact store,
- exit non-zero on failure (so schedulers can retry/alert).

Returns:
    The process exit code (0 = success).
"""

def main() -> int:
    """Run the job."""
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
