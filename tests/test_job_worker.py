"""Worker identity on Job rows (Celery nodename or web)."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.job_worker import (
    WEB_WORKER_NAME,
    hostname_from_celery_request,
    job_worker_label,
    normalize_worker_name,
    remember_celery_worker,
    stamp_job_worker,
)
from app.services.jobs import job_public_dict
from app.tasks import _remember_worker


def test_normalize_worker_name():
    assert normalize_worker_name("  celery@rpi5-1  ") == "celery@rpi5-1"
    assert normalize_worker_name("") is None
    assert normalize_worker_name(None) is None
    assert normalize_worker_name("null") is None
    assert len(normalize_worker_name("n" * 500)) == 200


def test_hostname_from_celery_request():
    assert hostname_from_celery_request(SimpleNamespace(hostname="nmap@pi")) == "nmap@pi"
    assert hostname_from_celery_request({"hostname": "celery@b"}) == "celery@b"
    assert hostname_from_celery_request(SimpleNamespace(hostname="")) is None
    assert hostname_from_celery_request(None) is None


def test_remember_pending_overwrites_and_running_keeps_original():
    job = SimpleNamespace(status="pending", worker_hostname=None)
    assert remember_celery_worker(job, SimpleNamespace(hostname="celery@a")) is True
    assert job.worker_hostname == "celery@a"
    assert remember_celery_worker(job, SimpleNamespace(hostname="celery@b")) is True
    assert job.worker_hostname == "celery@b"
    assert remember_celery_worker(job, SimpleNamespace(hostname="celery@b")) is False

    job.status = "running"
    assert remember_celery_worker(job, SimpleNamespace(hostname="celery@c")) is False
    assert job.worker_hostname == "celery@b"

    blank = SimpleNamespace(status="running", worker_hostname=None)
    assert remember_celery_worker(blank, SimpleNamespace(hostname="celery@c")) is False
    assert blank.worker_hostname is None


def test_stamp_web_does_not_clobber_celery_name():
    job = SimpleNamespace(worker_hostname="celery@a")
    assert stamp_job_worker(job, WEB_WORKER_NAME, overwrite=False) is False
    assert job.worker_hostname == "celery@a"
    empty = SimpleNamespace(worker_hostname=None)
    assert stamp_job_worker(empty, WEB_WORKER_NAME, overwrite=False) is True
    assert empty.worker_hostname == "web"


def test_worker_label_pending_vs_claimed_vs_finished():
    pending = SimpleNamespace(status="pending", worker_hostname=None)
    assert job_worker_label(pending) == "not claimed"
    claimed = SimpleNamespace(status="pending", worker_hostname="celery@rpi5-1")
    assert job_worker_label(claimed) == "celery@rpi5-1"
    old = SimpleNamespace(status="success", worker_hostname=None)
    assert job_worker_label(old) is None
    web = SimpleNamespace(status="success", worker_hostname="web")
    assert job_worker_label(web) == "web"


def test_job_public_dict_includes_worker():
    job = SimpleNamespace(
        id=3,
        server_id=1,
        job_type="os_patch",
        status="pending",
        created_at=datetime(2026, 9, 28, 8, 0, 0),
        started_at=None,
        finished_at=None,
        worker_hostname="celery@rpi5-2",
        details=json.dumps({"current": "waiting_for_host", "summary": "wait"}),
    )
    pub = job_public_dict(job)
    assert pub["worker_hostname"] == "celery@rpi5-2"
    assert pub["worker_label"] == "celery@rpi5-2"
    assert pub["done"] is False

    queued = SimpleNamespace(
        id=4,
        server_id=1,
        job_type="backup",
        status="pending",
        created_at=datetime(2026, 9, 28, 8, 0, 0),
        started_at=None,
        finished_at=None,
        worker_hostname=None,
        details="{}",
    )
    qpub = job_public_dict(queued)
    assert qpub["worker_hostname"] is None
    assert qpub["worker_label"] == "not claimed"


def test_remember_worker_helper_mutates_without_committing():
    job = SimpleNamespace(status="pending", worker_hostname=None)
    db = MagicMock()
    assert _remember_worker(job, SimpleNamespace(hostname="nmap@pi")) is True
    assert job.worker_hostname == "nmap@pi"
    db.commit.assert_not_called()
    assert _remember_worker(job, SimpleNamespace(hostname="")) is False
