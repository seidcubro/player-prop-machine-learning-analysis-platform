"""SQS/queue helper utilities (optional shared library).

Long-running work (feature building, training, large ingests) is typically
executed asynchronously via a queue (e.g., Amazon SQS) rather than inside HTTP
request handlers.

If you adopt that design, implement shared helpers here for:
- publishing job messages,
- consuming/acknowledging messages,
- standardized message schemas and tracing fields.
"""
