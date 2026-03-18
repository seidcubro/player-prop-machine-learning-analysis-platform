# Database SQL Organization

This folder holds database-related SQL organized by purpose.

## Folder guide

- migrations/
  - Schema changes
  - Registry changes
  - New columns / structural updates
  - Market table changes

- iews/
  - SQL views used for feature engineering
  - Reusable upstream data-shaping logic

- ackfills/
  - Data repair scripts
  - One-time injections
  - Rebuild/repopulation jobs

- inspection/
  - Debug and validation SQL
  - Row checks
  - Table inspection
  - Verification scripts

- patches/
  - Temporary/manual fixes that do not cleanly fit elsewhere

## Notes

Keep root clean. New SQL should not be dropped at repo root.
