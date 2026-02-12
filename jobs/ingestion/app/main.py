"""Ingestion job entrypoint.

Runs the ingestion/ETL pipeline that populates the platform database with the
latest player/game/stat information used for feature engineering and modeling.
"""

from app.etl.nflverse_ingest import run

def main():
    """Entrypoint for the ingestion job container.

    Runs the nflverse ingestion pipeline defined in `app.etl.nflverse_ingest`.

    Returns:
        None
    """
    run()

if __name__ == "__main__":
    main()
