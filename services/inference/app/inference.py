"""Inference business logic (future extension point).

The repository currently performs model inference inside the main API service
(`services/api/app/routes/players.py::projection_ml`) by loading a scikit-learn
pipeline artifact from disk and calling `predict`.

In a production deployment, you typically separate inference into its own
service so you can:
- scale inference independently from API traffic,
- run multiple model versions in parallel,
- isolate heavy dependencies (scikit-learn, numpy) from lightweight API pods,
- add caching, batching, or GPU workers later.

When you are ready to split inference out, this module is where you would place:
- artifact loading / caching,
- feature validation and transformation,
- prediction + post-processing,
- error handling and observability hooks.
"""
