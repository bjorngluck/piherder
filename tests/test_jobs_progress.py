"""Unit tests for job details merge, public dict, and labels (no DB)."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from app.services.jobs import (
    JOB_TYPE_LABELS,
    _initial_job_details,
    _merge_job_details,
    job_public_dict,
    job_type_label,
)


def test_job_type_labels():
    assert job_type_label("os_patch") == "OS patch"
    assert job_type_label("backup") == "Backup"
    assert job_type_label(None) == "Job"
    assert job_type_label("unknown_thing") == "Unknown Thing"
    assert "container_patch" in JOB_TYPE_LABELS


def test_initial_job_details_shape():
    raw = _initial_job_details("OS patch queued…", scheduled=True, os_steps=["update"])
    data = json.loads(raw)
    assert data["current"] == "queued"
    assert data["done"] is False
    assert data["log_lines"] == ["OS patch queued…"]
    assert data["scheduled"] is True
    assert data["os_steps"] == ["update"]


def test_merge_job_details_log_line_cap():
    job = SimpleNamespace(details=None)
    _merge_job_details(job, current="running", log_line="first")
    data = json.loads(job.details)
    assert data["current"] == "running"
    assert data["log_lines"] == ["first"]

    for i in range(50):
        _merge_job_details(job, log_line=f"line-{i}")
    data = json.loads(job.details)
    assert len(data["log_lines"]) == 40
    assert data["log_lines"][0] == "line-10"
    assert data["log_lines"][-1] == "line-49"


def test_merge_job_details_replaces_log_lines_list():
    job = SimpleNamespace(details=json.dumps({"log_lines": ["a", "b"]}))
    _merge_job_details(job, log_lines=["x", "y", "z"], current="patching")
    data = json.loads(job.details)
    assert data["log_lines"] == ["x", "y", "z"]
    assert data["current"] == "patching"


def test_job_public_dict_summary_and_tails():
    lines = [f"L{i}" for i in range(20)]
    job = SimpleNamespace(
        id=42,
        server_id=7,
        job_type="os_patch",
        status="running",
        created_at=datetime(2026, 7, 9, 12, 0, 0),
        started_at=datetime(2026, 7, 9, 12, 0, 1),
        finished_at=None,
        details=json.dumps(
            {
                "current": "patching",
                "summary": "upgrading…",
                "log_lines": lines,
                "scheduled": True,
                "os_steps": ["update", "upgrade"],
                "result_snippet": "big " * 100,
            }
        ),
    )
    short = job_public_dict(job, detail=False)
    assert short["id"] == 42
    assert short["job_type_label"] == "OS patch"
    assert short["current"] == "patching"
    assert short["scheduled"] is True
    assert short["done"] is False
    assert len(short["log_tail"]) == 8
    assert short["log_tail"][-1] == "L19"
    assert "details_json" not in short

    full = job_public_dict(job, detail=True)
    assert len(full["log_tail"]) == 20
    assert "details_json" in full
    assert full["os_steps"] == ["update", "upgrade"]


def test_job_public_dict_done_on_terminal():
    job = SimpleNamespace(
        id=1,
        server_id=1,
        job_type="backup",
        status="success",
        created_at=None,
        started_at=None,
        finished_at=datetime(2026, 7, 9, 12, 5, 0),
        details=json.dumps({"summary": "ok", "log_lines": ["done"]}),
    )
    d = job_public_dict(job)
    assert d["done"] is True
    assert d["status"] == "success"
