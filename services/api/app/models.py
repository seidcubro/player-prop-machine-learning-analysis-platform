"""API data models (optional).

The API service primarily uses raw SQL queries (`sqlalchemy.text`) and returns
response dictionaries rather than SQLAlchemy ORM models.

If you decide to migrate toward ORM models, define your SQLAlchemy declarative
models here (e.g., Player, PropMarket, Projection) and update routes to use them.

Keeping this module present (and documented) signals a clear extension point
without pretending the ORM layer is already implemented.
"""
