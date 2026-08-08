#!/usr/bin/env python3
"""CLI: seed or re-seed the demo fleet.

Examples (from repo root, with app on PYTHONPATH / inside web container)::

    python scripts/demo_seed/seed.py
    python scripts/demo_seed/seed.py --force
    PIHERDER_DEMO_PASSWORD=secret python scripts/demo_seed/seed.py --force

Inside compose::

    docker compose exec web python scripts/demo_seed/seed.py --force
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root without install
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed PiHerder demo fleet")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Wipe fleet tables and re-seed (destructive on this DB)",
    )
    parser.add_argument("--email", default=None, help="Demo admin email override")
    parser.add_argument("--password", default=None, help="Demo admin password override")
    parser.add_argument("--json", action="store_true", help="Print summary as JSON")
    args = parser.parse_args()

    from sqlmodel import Session

    from app.database import engine
    from app.services.demo_seed import seed_demo_fleet

    with Session(engine) as session:
        summary = seed_demo_fleet(
            session,
            force=args.force,
            password=args.password,
            email=args.email,
        )
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        if summary.get("skipped"):
            print(f"Seed skipped ({summary.get('reason')}) — {summary.get('servers')} servers, user {summary.get('user_email')}")
            print("Use --force to wipe and re-seed.")
        else:
            print(
                f"Seeded v{summary.get('seed_version')}: "
                f"{summary.get('servers')} servers, "
                f"user {summary.get('user_email')}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
