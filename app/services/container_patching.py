"""
Container patching service.

Replicates robust logic from docker-cluster-update.sh:
- Support compose.yml / compose.yaml / docker-compose.*
- EXCLUDED_PROJECTS
- docker compose config --images
- before/after image ID comparison via docker inspect
- Only up -d when IDs changed
- has_build detection
- failure collection (pull vs up)
- Live progress dict for JobHold modal (same process as web BackgroundTasks)
"""
from __future__ import annotations

import time
from typing import List, Dict, Callable
from ..models import Server
from ..services.ssh import get_ssh_client, run_command
from ..config import settings

_container_patch_progress: dict = {}
_MAX_LOG_LINES = 200
_PROGRESS_UI_LINES = 40


def get_container_patch_progress(hostname: str) -> dict:
    p = _container_patch_progress.get(hostname)
    if not p:
        return {
            "current": None,
            "log_lines": [],
            "done": False,
            "finished_ok": None,
            "total_lines": 0,
            "tail": True,
        }
    all_lines = list(p.get("log_lines") or [])
    total = len(all_lines)
    tail = all_lines[-_PROGRESS_UI_LINES:]
    if total > _PROGRESS_UI_LINES:
        tail = [f"… ({total - _PROGRESS_UI_LINES} earlier lines omitted)"] + tail
    return {
        "current": p.get("current"),
        "log_lines": tail,
        "done": bool(p.get("done")),
        "finished_ok": p.get("finished_ok"),
        "total_lines": total,
        "tail": True,
    }


def init_container_patch_progress(hostname: str, initial_msg: str = "starting"):
    _container_patch_progress[hostname] = {
        "current": "starting",
        "log_lines": [f"[containers] {initial_msg}"],
        "done": False,
        "finished_ok": None,
        "last_activity": time.time(),
    }


def append_container_log(hostname: str, text: str):
    p = _container_patch_progress.get(hostname)
    if p is None:
        init_container_patch_progress(hostname, "…")
        p = _container_patch_progress[hostname]
    ln = (text or "").strip()
    if not ln:
        return
    lines = p["log_lines"]
    lines.append(ln)
    p["last_activity"] = time.time()
    if len(lines) > _MAX_LOG_LINES:
        p["log_lines"] = lines[-_MAX_LOG_LINES:]


def mark_container_patch_done(hostname: str, finished_ok: bool | None = None):
    p = _container_patch_progress.get(hostname)
    if not p:
        return
    p["current"] = None
    p["done"] = True
    p["finished_ok"] = finished_ok
    p["last_activity"] = time.time()


def clear_container_patch_progress(hostname: str):
    _container_patch_progress.pop(hostname, None)


def container_patch_succeeded(res: dict | None) -> bool:
    if not res or res.get("error"):
        return False
    failed = res.get("failed") or []
    return len(failed) == 0


def summarize_container_patch(res: dict | None) -> str:
    if not res:
        return "Container patch"
    if res.get("error"):
        return f"Failed: {str(res['error'])[:120]}"
    updated = res.get("updated") or []
    failed = res.get("failed") or []
    checked = res.get("projects_checked") or []
    parts = [f"{len(checked)} project(s)"]
    if updated:
        parts.append(f"{len(updated)} updated")
    else:
        parts.append("none updated")
    if failed:
        parts.append(f"{len(failed)} failed")
    return " · ".join(parts)


def find_compose_file(dir_path: str) -> str | None:
    for name in ["compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"]:
        candidate = f"{dir_path}/{name}"
        _ = candidate
    return None


def discover_projects(server: Server) -> List[str]:
    """SSH to host and return immediate subdirs under docker_base_dir that have compose files."""
    try:
        client = get_ssh_client(server)
        from .ssh import docker_base_expanded
        base = docker_base_expanded(server)
        cmd = f"ls -1 {base} 2>/dev/null || true"
        status, out, err = run_command(client, cmd)
        client.close()

        projects = []
        for line in out.strip().splitlines():
            proj = line.strip()
            if proj:
                projects.append(proj)
        excluded = server.get_excluded_projects()
        return [p for p in projects if p not in excluded]
    except Exception as e:
        return [f"ERROR: {e}"]


def run_project_update(
    server: Server,
    project: str | None = None,
    on_progress: Callable[[str, str], None] | None = None,
) -> Dict:
    """
    Replicate the main logic:
    - cd into project
    - docker compose config --images
    - before IDs
    - pull
    - after IDs
    - if changed: up -d

    on_progress(current_step, log_line) is optional (job runner uses it to flush Job.details).
    """
    hostname = server.hostname or server.name or "host"
    if hostname not in _container_patch_progress:
        init_container_patch_progress(hostname, "starting container patch…")

    def _log(current: str | None, msg: str):
        if current is not None and hostname in _container_patch_progress:
            _container_patch_progress[hostname]["current"] = current
        append_container_log(hostname, msg)
        if on_progress:
            try:
                on_progress(current or (_container_patch_progress.get(hostname) or {}).get("current") or "", msg)
            except Exception:
                pass

    client = get_ssh_client(server)
    from .ssh import docker_base_expanded
    base = docker_base_expanded(server)

    projects_to_do = [project] if project else discover_projects(server)
    if projects_to_do and str(projects_to_do[0]).startswith("ERROR"):
        err = projects_to_do[0]
        _log("error", err)
        try:
            client.close()
        except Exception:
            pass
        return {
            "updated": [],
            "failed": [err],
            "projects_checked": [],
            "error": err,
            "summary": summarize_container_patch({"failed": [err], "projects_checked": []}),
        }

    updated = []
    failed = []
    skipped = []
    total = len([p for p in projects_to_do if p and not str(p).startswith("ERROR")])
    _log("discover", f"[containers] {total} project(s) to check under {base}")

    idx = 0
    for proj in projects_to_do:
        if not proj or str(proj).startswith("ERROR"):
            continue
        idx += 1
        proj_dir = f"{base}/{proj}"
        step = f"{idx}/{total} {proj}"
        _log(step, f"[{proj}] checking compose…")

        try:
            status, ls_out, _ = run_command(
                client, f"ls {proj_dir}/compose.* {proj_dir}/docker-compose.* 2>/dev/null || true"
            )
            if not ls_out.strip():
                skipped.append(proj)
                _log(step, f"[{proj}] no compose file — skip")
                continue

            has_build = False
            try:
                _, compose_raw, _ = run_command(
                    client,
                    f"cat {proj_dir}/compose.yaml {proj_dir}/compose.yml "
                    f"{proj_dir}/docker-compose.yaml {proj_dir}/docker-compose.yml "
                    f"2>/dev/null | head -100 || true",
                )
                has_build = "build:" in compose_raw
            except Exception:
                pass

            _, images_raw, _ = run_command(
                client, f"cd {proj_dir} && docker compose config --images 2>/dev/null || true"
            )
            images = [l.strip() for l in images_raw.strip().splitlines() if l.strip()]

            before = ""
            if images:
                _, before_raw, _ = run_command(
                    client,
                    f"docker inspect --format '{{{{.Id}}}}' {' '.join(images)} "
                    f"2>/dev/null | sort -u | tr '\\n' ' ' || true",
                )
                before = before_raw.strip()

            _log(step, f"[{proj}] pulling ({len(images)} image(s))" + (" · has build" if has_build else ""))
            pstatus, pull_out, _ = run_command(
                client, f"cd {proj_dir} && docker compose pull 2>&1 || true", timeout=300
            )
            if pull_out:
                for line in pull_out.strip().splitlines()[-5:]:
                    if line.strip():
                        _log(step, f"[{proj}] {line.strip()[:180]}")

            after = ""
            if images:
                _, after_raw, _ = run_command(
                    client,
                    f"docker inspect --format '{{{{.Id}}}}' {' '.join(images)} "
                    f"2>/dev/null | sort -u | tr '\\n' ' ' || true",
                )
                after = after_raw.strip()

            if before != after:
                _log(step, f"[{proj}] image change detected — docker compose up -d")
                up_status, up_out, _ = run_command(
                    client, f"cd {proj_dir} && docker compose up -d 2>&1 || true", timeout=180
                )
                if up_status == 0:
                    updated.append(proj)
                    _log(step, f"[{proj}] ✓ updated")
                else:
                    failed.append(f"{proj} (up): {(up_out or '')[-200:]}")
                    _log(step, f"[{proj}] ✗ up failed")
            else:
                _log(step, f"[{proj}] no image change")

            if pstatus != 0 and not has_build:
                failed.append(f"{proj}: pull failed")
                _log(step, f"[{proj}] ✗ pull failed (rc={pstatus})")

        except Exception as e:
            failed.append(f"{proj}: {str(e)}")
            _log(step, f"[{proj}] ERROR: {e}")

    try:
        client.close()
    except Exception:
        pass

    res = {
        "updated": updated,
        "failed": failed,
        "skipped": skipped,
        "projects_checked": [p for p in projects_to_do if p and not str(p).startswith("ERROR")],
        "summary": "",
    }
    res["summary"] = summarize_container_patch(res)
    _log(
        "finishing",
        f"[containers] done — {res['summary']}"
        + (f" · updated: {', '.join(updated)}" if updated else ""),
    )
    return res


def check_project_images(client, proj_dir: str) -> dict:
    """
    Pull registry images and compare IDs. Does not run `up -d`.

    Local-build services (compose ``build:``) are excluded from update detection —
    those are updated via Build, not pull-based "Update available".
    """
    import shlex
    from .docker_management import _image_id_remote, classify_compose_images

    status, ls_out, _ = run_command(
        client, f"ls {proj_dir}/compose.* {proj_dir}/docker-compose.* 2>/dev/null || true"
    )
    if not (ls_out or "").strip():
        return {"has_compose": False, "has_updates": False, "updated_images": [], "images": []}

    qdir = shlex.quote(proj_dir)
    classified = classify_compose_images(client, proj_dir)
    images = list(classified.get("pullable_images") or [])
    if not images:
        return {
            "has_compose": True,
            "has_updates": False,
            "pull_rc": 0,
            "images": [],
            "updated_images": [],
            "skipped_build_only": True,
            "build_services": classified.get("build_services") or [],
        }

    before_ids = {img: _image_id_remote(client, img) for img in images}
    pstatus, pull_out, _ = run_command(
        client, f"cd {qdir} && docker compose pull 2>&1 || true", timeout=300
    )
    after_ids = {img: _image_id_remote(client, img) for img in images}
    updated_images = [img for img in images if before_ids.get(img) != after_ids.get(img)]
    has_updates = bool(updated_images)

    return {
        "has_compose": True,
        "has_updates": has_updates,
        "pull_rc": pstatus,
        "images": images,
        "updated_images": updated_images,
        "build_services": classified.get("build_services") or [],
    }


def check_all_projects_updates(server: Server) -> dict:
    """Fleet check-only: pull + compare image IDs for each compose project. Never runs up -d."""
    client = get_ssh_client(server)
    from .ssh import docker_base_expanded
    base = docker_base_expanded(server)
    projects = discover_projects(server)
    with_updates: list[str] = []
    project_details: dict[str, dict] = {}
    failed: list[str] = []
    checked: list[str] = []

    try:
        for proj in projects:
            if not proj or str(proj).startswith("ERROR"):
                if proj and str(proj).startswith("ERROR"):
                    failed.append(str(proj))
                continue
            proj_dir = f"{base}/{proj}"
            checked.append(proj)
            try:
                res = check_project_images(client, proj_dir)
                if not res.get("has_compose"):
                    continue
                if res.get("has_updates"):
                    with_updates.append(proj)
                    project_details[proj] = {
                        "images": list(res.get("updated_images") or []),
                    }
            except Exception as e:
                failed.append(f"{proj}: {e}")
    finally:
        try:
            client.close()
        except Exception:
            pass

    return {
        "server": server.hostname,
        "projects_with_updates": with_updates,
        "project_details": project_details,
        "updates_count": len(with_updates),
        "failed": failed,
        "projects_checked": checked,
    }
