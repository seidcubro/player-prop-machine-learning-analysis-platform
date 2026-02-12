"""Model artifact loading utilities (future extension point).

The current project loads joblib artifacts directly where needed. A dedicated
loader module becomes valuable once you need:
- LRU caching of loaded models,
- versioned artifacts and routing logic,
- remote artifact storage (e.g., S3) with local caching,
- warm-up / health probes that verify artifacts are usable.

When implemented, this module should expose functions such as:
- `load_pipeline(path: str) -> sklearn.pipeline.Pipeline`
- `get_active_model(market_code: str, lookback: int) -> ModelHandle`
"""
