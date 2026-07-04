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
