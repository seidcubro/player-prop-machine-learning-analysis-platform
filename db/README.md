# Database SQL Layout

## db/migrations
Durable schema changes that should represent long-term structure.

## db/backfills
One-off repair, patch, or data-alignment scripts.

## db/inspection
Read-only debugging and inspection queries.

## db/bootstrap
Setup, reseed, rebuild, sync, or compatibility scripts used during local recovery
and environment alignment.

## db/ddl
Low-level create/drop table lifecycle scripts kept for manual recovery and debugging.

Rules:
- Keep ad hoc SQL out of the repo root.
- Prefer descriptive names over vague names.
- If a script becomes permanently required, move that logic into a migration or app code.
