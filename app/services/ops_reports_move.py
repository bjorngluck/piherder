"""Reports card: finished service_migrate jobs (count / fail / last dest)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlmodel import Session, select

from ..models import Job, Server


def _rpt():
    from . import ops_reports as rpt

    return rpt


def _move_dest_facts(
    details: dict[str, Any], servers: dict[int, Server]
) -> dict[str, str]:
    """Project + dest host from a service_migrate Job.details blob."""
    rpt = _rpt()
    project = str(details.get("project") or details.get("dest_project") or "").strip()
    dest_project = str(details.get("dest_project") or "").strip()
    dest_name = str(details.get("dest_name") or "").strip()
    dest_id = details.get("dest_server_id")
    if not dest_name and dest_id is not None:
        dest_name = rpt._host_label(servers, dest_id)
    label = dest_name or "—"
    if project:
        label = f"{project} → {dest_name}" if dest_name else project
    return {
        "project": project,
        "dest_project": dest_project,
        "dest_name": dest_name,
        "label": label,
    }


def collect_move_history(
    session: Session,
    *,
    days: int = 30,
    now: Optional[datetime] = None,
    jobs: Optional[list[Job]] = None,
    servers: Optional[dict[int, Server]] = None,
) -> dict[str, Any]:
    """Finished ``service_migrate`` jobs: count / fail / last dest."""
    rpt = _rpt()
    now = now or datetime.utcnow()
    days = max(1, int(days))
    since = now - timedelta(days=days)
    if jobs is None:
        jobs = rpt._load_jobs(session, rpt._MOVE_TYPES, since)
    else:
        jobs = [
            j
            for j in jobs
            if j.job_type == "service_migrate"
            and j.finished_at
            and j.finished_at >= since
            and j.status in ("success", "failed")
        ]
    if servers is None:
        servers = {int(s.id): s for s in session.exec(select(Server)).all() if s.id}

    day_keys = rpt._day_list(now, days)
    by_day = {d: {"day": d, "ok": 0, "fail": 0, "last_dest": ""} for d in day_keys}
    host_rows: dict[int, dict[str, Any]] = {}
    ok_n = fail_n = 0
    last_dest = ""
    last_project = ""
    last_ok_dest = ""
    last_ok_project = ""
    last_finished: Optional[datetime] = None
    last_ok_at: Optional[datetime] = None

    def _host(sid: Optional[int]) -> dict[str, Any]:
        key = int(sid) if sid is not None else 0
        return host_rows.setdefault(
            key,
            {
                "server_id": sid,
                "name": rpt._host_label(servers, sid),
                "href": f"/servers/{sid}" if sid else "/servers",
                "ok": 0,
                "fail": 0,
                "last_dest": "",
            },
        )

    for j in jobs:
        day = rpt._app_day(j.finished_at)
        if day not in by_day:
            continue
        facts = _move_dest_facts(rpt._parse_details(j), servers)
        row = by_day[day]
        hr = _host(j.server_id)
        ok = j.status == "success"
        if ok:
            row["ok"] += 1
            hr["ok"] += 1
            ok_n += 1
            if last_ok_at is None or (j.finished_at and j.finished_at >= last_ok_at):
                last_ok_at = j.finished_at
                last_ok_dest = facts["dest_name"] or facts["label"]
                last_ok_project = facts["project"]
        else:
            row["fail"] += 1
            hr["fail"] += 1
            fail_n += 1
        if facts["label"]:
            row["last_dest"] = facts["label"]
            hr["last_dest"] = facts["label"]
        if last_finished is None or (j.finished_at and j.finished_at >= last_finished):
            last_finished = j.finished_at
            last_dest = facts["dest_name"] or facts["label"]
            last_project = facts["project"]

    day_rows = [by_day[d] for d in day_keys]
    hosts = sorted(host_rows.values(), key=lambda h: (h["name"] or "").lower())
    total_runs = ok_n + fail_n
    kpi_dest = last_ok_dest or last_dest or "—"
    kpi_project = last_ok_project if last_ok_dest else last_project
    return {
        "days": days,
        "ok": ok_n,
        "fail": fail_n,
        "runs": total_runs,
        "ok_pct": int(round(100 * ok_n / total_runs)) if total_runs else 0,
        "last_dest": kpi_dest,
        "last_project": kpi_project,
        "day_rows": day_rows,
        "hosts": hosts,
        "empty": total_runs == 0,
        "jobs_href": "/jobs?job_type=service_migrate",
        "note": (
            "Move jobs are finished service_migrate rows (source host). "
            "Last dest is the destination host of the last successful Move in the window, "
            "or the last finished Move if none succeeded."
        ),
    }
