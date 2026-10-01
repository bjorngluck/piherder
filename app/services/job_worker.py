"""Which process claimed a Job.

Celery's task request carries the worker nodename (``celery@host``,
``nmap@host``). That is recorded when the worker picks the job up, including
while the row is still pending (host wait, backup lock). A redelivery that
finds the job already running does not replace that name — the worker that
started it is the one that was performing it.

Demo simulation still uses the label ``web``. Pytest runs retention, herder
backup, and host facts in-process and stamps ``web`` there too. Production
stamps the Celery nodename.
"""
from __future__ import annotations

WEB_WORKER_NAME = "web"
WORKER_NAME_MAX = 200


def normalize_worker_name(raw) -> str | None:
    text = str(raw or "").strip()
    if not text or text.lower() in {"none", "null"}:
        return None
    return text[:WORKER_NAME_MAX]


def hostname_from_celery_request(request) -> str | None:
    """Celery nodename from a bound task request. Empty when not in a worker."""
    if request is None:
        return None
    host = getattr(request, "hostname", None)
    if not host and isinstance(request, dict):
        host = request.get("hostname")
    return normalize_worker_name(host)


def stamp_job_worker(job, name, *, overwrite: bool = True) -> bool:
    """Set ``job.worker_hostname``. Returns True when the value changed."""
    clean = normalize_worker_name(name)
    if not clean or job is None:
        return False
    current = (getattr(job, "worker_hostname", None) or "").strip()
    if current == clean:
        return False
    if current and not overwrite:
        return False
    job.worker_hostname = clean
    return True


def remember_celery_worker(job, request) -> bool:
    """Record the worker holding this job.

    Pending claims (including lock wait and SSH wait) update the name so a
    later worker that picks the same row replaces the previous one. A job
    already ``running`` keeps a name that was set. If that name is still
    blank, the current worker fills it once and later redeliveries do not
    replace it.
    """
    if job is None:
        return False
    name = hostname_from_celery_request(request)
    if (getattr(job, "status", None) or "") == "running":
        return stamp_job_worker(job, name, overwrite=False)
    return stamp_job_worker(job, name, overwrite=True)


def job_worker_label(job) -> str | None:
    """UI string: nodename, ``not claimed`` while pending, or None."""
    name = (getattr(job, "worker_hostname", None) or "").strip()
    if name:
        return name
    if (getattr(job, "status", None) or "") == "pending":
        return "not claimed"
    return None
