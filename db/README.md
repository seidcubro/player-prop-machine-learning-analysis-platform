# Database SQL Layout

## db/migrations
Schema-changing SQL that should represent durable structural changes.

## db/backfills
One-off repair, sync, patch, and backfill scripts used to align data with current app behavior.

## db/inspection
Read-only inspection and debugging queries.

Notes:
- Do not add new ad hoc SQL files to the repo root.
- Prefer clear names over vague names like fix_final.sql.
- If a backfill becomes a permanent requirement, convert it into a real migration or app logic.
