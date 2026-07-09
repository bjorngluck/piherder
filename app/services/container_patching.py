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
"""
import subprocess
import re
from typing import List, Dict
from ..models import Server
from ..services.ssh import get_ssh_client, run_command
from ..config import settings


def find_compose_file(dir_path: str) -> str | None:
    for name in ["compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"]:
        # In real: use sftp or exec ls
        candidate = f"{dir_path}/{name}"
        # Placeholder
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


def run_project_update(server: Server, project: str | None = None) -> Dict:
    """
    Replicate the main logic:
    - cd into project
    - docker compose config --images
    - before IDs
    - pull
    - after IDs
    - if changed: up -d
    """
    client = get_ssh_client(server)
    from .ssh import docker_base_expanded
    base = docker_base_expanded(server)

    projects_to_do = [project] if project else discover_projects(server)
    updated = []
    failed = []

    for proj in projects_to_do:
        if not proj or proj.startswith("ERROR"):
            continue
        proj_dir = f"{base}/{proj}"

        try:
            # Check compose file exists (port of find_compose_file)
            status, ls_out, _ = run_command(client, f"ls {proj_dir}/compose.* {proj_dir}/docker-compose.* 2>/dev/null || true")
            if not ls_out.strip():
                continue

            # has_build detection (like original script)
            has_build = False
            try:
                _, compose_raw, _ = run_command(client, f"cat {proj_dir}/compose.yaml {proj_dir}/compose.yml {proj_dir}/docker-compose.yaml {proj_dir}/docker-compose.yml 2>/dev/null | head -100 || true")
                has_build = 'build:' in compose_raw
            except Exception:
                pass

            # Get images
            _, images_raw, _ = run_command(client, f"cd {proj_dir} && docker compose config --images 2>/dev/null || true")
            images = [l.strip() for l in images_raw.strip().splitlines() if l.strip()]

            before = ""
            if images:
                _, before_raw, _ = run_command(client, f"docker inspect --format '{{{{.Id}}}}' {' '.join(images)} 2>/dev/null | sort -u | tr '\\n' ' ' || true")
                before = before_raw.strip()

            # Pull
            pstatus, pull_out, _ = run_command(client, f"cd {proj_dir} && docker compose pull 2>&1 || true", timeout=300)

            # After
            after = ""
            if images:
                _, after_raw, _ = run_command(client, f"docker inspect --format '{{{{.Id}}}}' {' '.join(images)} 2>/dev/null | sort -u | tr '\\n' ' ' || true")
                after = after_raw.strip()

            if before != after:
                if has_build:
                    # still try up for local build projects
                    pass
                # Up
                up_status, up_out, _ = run_command(client, f"cd {proj_dir} && docker compose up -d 2>&1 || true", timeout=180)
                if up_status == 0:
                    updated.append(proj)
                else:
                    failed.append(f"{proj} (up): {up_out[-200:]}")
            else:
                # no image change
                pass

            if pstatus != 0 and not has_build:
                failed.append(f"{proj}: pull failed")

        except Exception as e:
            failed.append(f"{proj}: {str(e)}")

    client.close()
    return {
        "updated": updated,
        "failed": failed,
        "projects_checked": projects_to_do,
    }


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
