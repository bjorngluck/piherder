# PiHerder v1.12.0 — notes, not a walk

**Status:** Notes written 2026-10-08. No branch. No build. No boxes to tick.  
**Plan:** [PLAN_v1.12.0.md](PLAN_v1.12.0.md)

## Notes

- **Audit API and MCP reads.** Off unless an operator flag is on. A read from a token or from hosted `/mcp` can write an audit row. Writes stay audited either way.
- **Host facts clutter.** The 15-minute `host_facts` job and its audit row stay out of the default Jobs list and the default Audit list. A failed snapshot stays visible.

## Moved from v1.11

- **NPM CRUD.** Creating and editing proxy hosts stays a note. Move can still retarget a backend.
- **Remote restore.** Restore from Drive, SMB, or OneDrive stays a note. Restore stays a reverse rsync from `/backups`.
