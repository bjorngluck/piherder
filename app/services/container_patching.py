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
        base = server.docker_base_dir.replace("~", f"/home/{server.ssh_username}")
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
    base = server.docker_base_dir.replace("~", f"/home/{server.ssh_username}")

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
    """Pull images for a project and compare IDs. Does not run `up -d`."""
    status, ls_out, _ = run_command(
        client, f"ls {proj_dir}/compose.* {proj_dir}/docker-compose.* 2>/dev/null || true"
    )
    if not (ls_out or "").strip():
        return {"has_compose": False, "has_updates": False}

    _, images_raw, _ = run_command(
        client, f"cd {proj_dir} && docker compose config --images 2>/dev/null || true", timeout=30
    )
    images = [l.strip() for l in (images_raw or "").strip().splitlines() if l.strip()]

    before = ""
    if images:
        _, before_raw, _ = run_command(
            client,
            f"docker inspect --format '{{{{.Id}}}}' {' '.join(images)} 2>/dev/null | sort -u | tr '\\n' ' ' || true",
            timeout=30,
        )
        before = (before_raw or "").strip()

    pstatus, pull_out, _ = run_command(
        client, f"cd {proj_dir} && docker compose pull 2>&1 || true", timeout=300
    )

    after = ""
    if images:
        _, after_raw, _ = run_command(
            client,
            f"docker inspect --format '{{{{.Id}}}}' {' '.join(images)} 2>/dev/null | sort -u | tr '\\n' ' ' || true",
            timeout=30,
        )
        after = (after_raw or "").strip()

    has_updates = bool(before and after and before != after) or (not before and after)
    # If pull changed nothing but before==after and both non-empty → no updates
    if before == after:
        has_updates = False

    return {
        "has_compose": True,
        "has_updates": has_updates,
        "pull_rc": pstatus,
        "images": images,
    }


def check_all_projects_updates(server: Server) -> dict:
    """Fleet check-only: pull + compare image IDs for each compose project. Never runs up -d."""
    client = get_ssh_client(server)
    base = server.docker_base_dir.replace("~", f"/home/{server.ssh_username}")
    projects = discover_projects(server)
    with_updates: list[str] = []
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
        "updates_count": len(with_updates),
        "failed": failed,
        "projects_checked": checked,
    }
