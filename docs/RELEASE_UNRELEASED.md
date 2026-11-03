# Unreleased fixes

These changes are pending release. No version or release date is assigned here.

- The host Backups page reads the latest backup audit entry again, displaying
  **last ok** or **last failed** instead of always **ready** when idle. Failed
  audit lookups now produce an error log with a traceback. See issue #77 and
  the [backup guide](../wiki/day-to-day/backups.md).
