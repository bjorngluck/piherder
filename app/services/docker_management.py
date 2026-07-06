"""
Docker management service for PiHerder.

All operations run over SSH on the remote server.

Features:
- List all containers (name, status, ports, image)
- Container actions: start, stop, restart
- Get exposed ports
- Read / write docker-compose files (via SFTP)
- Redeploy (compose up -d)
- Fetch logs (with tail)
- Build streaming, prune, compose validation, update checks

Versioning (drafts, history, deploy from version, new projects) was extracted to
docker_versions.py for maintainability. All names are re-exported below so
existing imports of docker_management continue to work unchanged.
"""
import json
import shlex
from typing import List, Dict, Optional
import yaml
from yaml import YAMLError
from ..models import Server
from ..services.ssh import get_ssh_client, run_command
import paramiko
import time
from datetime import datetime
from sqlmodel import Session, select
from ..database import engine  # for direct if needed, but prefer passed session

# Docker versioning extracted to docker_versions.py.
# Re-export for backward compatibility (routers use `from ..services import docker_management as docker_svc`).
# Call sites like docker_svc.get_versions, docker_svc.save_draft_version, docker_svc.deploy_version etc. keep working.
from . import docker_versions

get_project_live_files = docker_versions.get_project_live_files
write_project_files = docker_versions.write_project_files
get_versions = docker_versions.get_versions
save_draft_version = docker_versions.save_draft_version
deploy_version = docker_versions.deploy_version
prune_old_versions = docker_versions.prune_old_versions
create_new_docker_project = docker_versions.create_new_docker_project

def get_container_status(server: Server, name: str) -> Dict:
    """Get detailed status for one container."""
    client = get_ssh_client(server)
    cmd = f'docker inspect --format "{{{{json .}}}}" {name}'
    status, out, err = run_command(client, cmd, timeout=15)
    client.close()

    try:
        data = json.loads(out)
        ports = []
        if "NetworkSettings" in data and "Ports" in data["NetworkSettings"]:
            for container_port, host in (data["NetworkSettings"]["Ports"] or {}).items():
                if host:
                    for h in host:
                        ports.append(f"{h.get('HostIp', '0.0.0.0')}:{h.get('HostPort')}->{container_port}")
        return {
            "name": data.get("Name", "").lstrip("/"),
            "state": data.get("State", {}).get("Status", ""),
            "running": data.get("State", {}).get("Running", False),
            "ports": ports or ["none"],
            "image": data.get("Config", {}).get("Image", ""),
            "created": data.get("Created", ""),
        }
    except Exception:
        return {"name": name, "state": "unknown", "running": False, "ports": []}


def container_action(server: Server, name: str, action: str) -> Dict:
    """Perform action on a container: start, stop, restart."""
    valid = {"start", "stop", "restart"}
    if action not in valid:
        return {"success": False, "error": "Invalid action"}

    client = get_ssh_client(server)
    cmd = f"docker {action} {name}"
    status, out, err = run_command(client, cmd, timeout=60)
    client.close()

    success = status == 0
    return {
        "success": success,
        "action": action,
        "name": name,
        "output": (out + err).strip()[:300]
    }


def read_compose_file(server: Server, project_path: str) -> str:
    """Read the content of a docker-compose file via SFTP. Short-lived SSH session."""
    client = get_ssh_client(server)
    sftp = client.open_sftp()
    try:
        compose_candidates = [
            f"{project_path}/docker-compose.yml",
            f"{project_path}/docker-compose.yaml",
            f"{project_path}/compose.yml",
            f"{project_path}/compose.yaml",
        ]

        content = ""
        for path in compose_candidates:
            try:
                with sftp.open(path, "rb") as f:
                    raw = f.read()
                    content = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
                    break
            except IOError:
                continue
        return content or "# No compose file found"
    finally:
        try:
            sftp.close()
        except:
            pass
        client.close()


def get_compose_build_services(server: Server, project_path: str) -> Dict[str, dict]:
    """Parse compose to find services with build: section. Returns {service_name: build_config}"""
    content = read_compose_file(server, project_path)
    try:
        data = yaml.safe_load(content) or {}
        services = data.get("services") or {}
        buildable = {}
        for name, cfg in services.items():
            if isinstance(cfg, dict) and "build" in cfg:
                buildable[name] = cfg["build"]
        return buildable
    except Exception:
        return {}


def read_dockerfile(server: Server, dockerfile_full_path: str) -> str:
    """Read a Dockerfile via SFTP (short-lived session)."""
    client = get_ssh_client(server)
    sftp = client.open_sftp()
    try:
        with sftp.open(dockerfile_full_path, "rb") as f:
            raw = f.read()
            return raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    except IOError:
        return "# Dockerfile not found or unreadable at this path"
    finally:
        try:
            sftp.close()
        except:
            pass
        client.close()


def write_dockerfile(server: Server, dockerfile_full_path: str, content: str) -> bool:
    """Write Dockerfile via SFTP. Uses tmp + rename for safety."""
    client = get_ssh_client(server)
    sftp = client.open_sftp()
    success = False
    tmp = dockerfile_full_path + ".tmp"
    try:
        data = content.encode("utf-8") if isinstance(content, str) else content
        with sftp.open(tmp, "wb") as f:
            f.write(data)
        sftp.rename(tmp, dockerfile_full_path)
        success = True
    except Exception:
        try:
            sftp.remove(tmp)
        except:
            pass
    finally:
        try:
            sftp.close()
        except:
            pass
        client.close()
    return success


def write_compose_file(server: Server, project_path: str, content: str) -> bool:
    """Write (overwrite) a docker-compose file via SFTP."""
    client = get_ssh_client(server)
    sftp = client.open_sftp()

    # Try to determine the file name that exists or default to docker-compose.yml
    target = f"{project_path}/docker-compose.yml"
    try:
        # Check which one exists
        for candidate in [f"{project_path}/docker-compose.yml", f"{project_path}/compose.yml"]:
            try:
                sftp.stat(candidate)
                target = candidate
                break
            except IOError:
                pass
    except Exception:
        pass

    success = False
    tmp_target = target + ".tmp"
    try:
        data = content.encode("utf-8") if isinstance(content, str) else content
        with sftp.open(tmp_target, "wb") as f:
            f.write(data)
        # atomic-ish replace
        sftp.rename(tmp_target, target)
        success = True
    except Exception:
        try:
            sftp.remove(tmp_target)
        except:
            pass
        success = False
    finally:
        sftp.close()
        client.close()
    return success


def redeploy_project(server: Server, project_path: str, pull: bool = True) -> Dict:
    """Redeploy a compose project."""
    client = get_ssh_client(server)
    cmd = f"cd {project_path} && docker compose pull" if pull else f"cd {project_path} && docker compose up -d"
    # Always run up -d after optional pull
    if pull:
        status1, out1, err1 = run_command(client, f"cd {project_path} && docker compose pull", timeout=300)
    else:
        status1, out1, err1 = 0, "", ""

    status2, out2, err2 = run_command(client, f"cd {project_path} && docker compose up -d", timeout=120)
    client.close()

    return {
        "success": status2 == 0,
        "output": (out1 + err1 + "\n" + out2 + err2).strip()[-800:],
    }


def compose_action(server: Server, project_path: str, action: str, service: str = None) -> Dict:
    """stop, start, restart, down (undeploy) for a whole compose project or specific service."""
    valid = ("stop", "start", "restart", "down")
    if action not in valid:
        return {"success": False, "error": "bad action"}

    client = get_ssh_client(server)
    cmd = f"cd {project_path} && docker compose {action}"
    if service:
        cmd += f" {service}"
    status, out, err = run_command(client, cmd, timeout=120)
    client.close()

    return {
        "success": status == 0,
        "action": action,
        "service": service,
    }


def build_compose_services(server: Server, project_path: str, services: List[str] = None, no_cache: bool = False) -> Dict:
    """Build compose services. services=None means all buildable. Supports --no-cache."""
    client = get_ssh_client(server)
    try:
        cmd = f"cd {project_path} && docker compose build"
        if no_cache:
            cmd += " --no-cache"
        if services:
            # only the selected
            cmd += " " + " ".join(shlex.quote(s) for s in services)
        # run with longer timeout for builds
        status, out, err = run_command(client, cmd, timeout=600)
        return {
            "success": status == 0,
            "output": (out + err).strip()[-3000:],
            "services": services or "all",
            "no_cache": no_cache,
        }
    finally:
        client.close()


def get_logs(server: Server, container_or_service: str, lines: int = 100, follow: bool = False, project_path: str = None) -> str:
    """Get recent logs. For streaming use follow=False and poll from frontend.
    If project_path provided, uses docker compose logs for the service."""
    client = get_ssh_client(server)
    if project_path:
        cmd = f"cd {project_path} && docker compose logs --tail {lines} {container_or_service} 2>&1"
        if follow:
            cmd = f"cd {project_path} && docker compose logs -f --tail {lines} {container_or_service} 2>&1 | head -{lines}"
    else:
        cmd = f"docker logs --tail {lines} {container_or_service} 2>&1"
        if follow:
            cmd = f"docker logs -f --tail {lines} {container_or_service} 2>&1 | head -{lines}"

    status, out, err = run_command(client, cmd, timeout=30)
    client.close()
    return (out + err).strip()


def check_compose_updates(server: Server, project_path: str) -> Dict:
    """Check if there are new images for a compose project.
    Uses docker compose pull and reports changes.
    """
    client = get_ssh_client(server)
    try:
        # Get current images
        _, images_raw, _ = run_command(client, f"cd {project_path} && docker compose config --images 2>/dev/null || true", timeout=20)
        images = [l.strip() for l in images_raw.strip().splitlines() if l.strip()]

        before = ""
        if images:
            _, before_raw, _ = run_command(
                client,
                f"docker inspect --format '{{{{.Id}}}}' {' '.join(images)} 2>/dev/null | sort -u | tr '\\n' ' ' || true",
                timeout=30
            )
            before = before_raw.strip()

        # Try pull (dry-ish by capturing output)
        status, pull_out, pull_err = run_command(client, f"cd {project_path} && docker compose pull 2>&1", timeout=180)

        after = ""
        if images:
            _, after_raw, _ = run_command(
                client,
                f"docker inspect --format '{{{{.Id}}}}' {' '.join(images)} 2>/dev/null | sort -u | tr '\\n' ' ' || true",
                timeout=30
            )
            after = after_raw.strip()

        has_updates = before != after

        return {
            "has_updates": has_updates,
            "pull_output": (pull_out + pull_err).strip()[-600:],
            "success": status == 0 or "Pulled" in pull_out or has_updates
        }
    finally:
        client.close()


def stream_logs(server: Server, container: str, lines: int = 50, project_path: str = None):
    """Generator for streaming logs using SSE.
    Yields lines from `docker logs -f` (or compose logs -f) for live tail.
    """
    client = get_ssh_client(server)
    try:
        if project_path:
            cmd = f"cd {project_path} && docker compose logs -f --tail {lines} {container} 2>&1"
        else:
            cmd = f"docker logs -f --tail {lines} {container} 2>&1"
        stdin, stdout, stderr = client.exec_command(
            cmd,
            timeout=None
        )
        # Stream all output lines as SSE events
        for line in stdout:
            if line:
                yield f"data: {line.rstrip()}\n\n"
    except Exception as e:
        yield f"data: [ERROR] {str(e)}\n\n"
    finally:
        try:
            client.close()
        except:
            pass


def validate_compose_content(content: str) -> Dict:
    """Validate YAML content for docker-compose.
    Returns { 'valid': bool, 'errors': [{'line': int, 'column': int, 'message': str}, ...] }
    PyYAML stops at the first syntax error. To surface multiple issues (on different lines),
    we successively remove the line the parser choked on and re-try the remaining content.
    This reveals additional problems that were masked by earlier ones.
    We also bubble errors to the earliest failing prefix when possible.
    """
    if not content or not content.strip():
        return {"valid": False, "errors": [{"line": 1, "column": 1, "message": "File is empty"}]}

    errors: list[dict] = []
    seen: set[tuple] = set()

    def _add_error(line: int, column: int, message: str):
        key = (line, message[:100])
        if key not in seen:
            seen.add(key)
            errors.append({"line": line, "column": column, "message": message})

    original_lines = content.splitlines(keepends=True)
    active_indices = list(range(len(original_lines)))

    # Primary full parse + bubble to earliest
    try:
        yaml.safe_load(content)
        return {"valid": True, "errors": []}
    except YAMLError as exc:
        mark = getattr(exc, 'problem_mark', None)
        if mark:
            msg = str(getattr(exc, 'problem', exc))
            ctx = getattr(exc, 'context', None)
            if ctx:
                msg = f"{ctx} {msg}".strip()
            # bubble: find earliest prefix that fails
            bubbled = mark.line + 1
            for pl in range(1, bubbled + 1):
                pref = ''.join(original_lines[:pl])
                try:
                    yaml.safe_load(pref)
                except YAMLError as pexc:
                    pmark = getattr(pexc, 'problem_mark', None)
                    if pmark:
                        bubbled = pmark.line + 1
                        pmsg = str(getattr(pexc, 'problem', pexc))
                        pctx = getattr(pexc, 'context', None)
                        if pctx:
                            pmsg = f"{pctx} {pmsg}".strip()
                        if "expected ':'" in pmsg.lower() or "mapping values" in pmsg.lower():
                            pmsg += " (check indentation/key on lines above)"
                        _add_error(bubbled, pmark.column + 1, pmsg)
                    break
            else:
                if "expected ':'" in msg.lower() or "mapping values" in msg.lower():
                    msg += " (check indentation/key on lines above)"
                _add_error(bubbled, mark.column + 1, msg)
        else:
            msg = str(exc)
            line = 1
            import re
            m = re.search(r'line\s+(\d+)', msg, re.I)
            if m:
                line = int(m.group(1))
            _add_error(line, 1, msg)

    # Successive removal to discover additional masked errors
    for _attempt in range(8):
        if not active_indices:
            break
        test = ''.join(original_lines[i] for i in active_indices)
        try:
            yaml.safe_load(test)
            break
        except YAMLError as exc:
            mark = getattr(exc, 'problem_mark', None)
            if not mark or mark.line >= len(active_indices):
                break
            # the bad line in the current active set
            bad_pos = mark.line
            orig_idx = active_indices[bad_pos]
            ln = orig_idx + 1
            msg = str(getattr(exc, 'problem', exc))
            ctx = getattr(exc, 'context', None)
            if ctx:
                msg = f"{ctx} {msg}".strip()
            if "expected ':'" in msg.lower() or "mapping values" in msg.lower():
                msg += " (check indentation/key on lines above)"
            _add_error(ln, (mark.column + 1) if mark else 1, msg)
            # remove this line from active set to expose next problem
            del active_indices[bad_pos]

    # Final fallback
    if not errors:
        try:
            yaml.safe_load(content)
        except Exception as e:
            _add_error(1, 1, str(e))

    # Try to surface errors in the "tail" after the last reported bad line (helps when early error masks later syntax)
    if errors:
        last_reported = max(e["line"] for e in errors)
        if last_reported < len(original_lines):
            suffix = ''.join(original_lines[last_reported:])
            # wrap the tail under a dummy key so indentation-based structures can be tested
            wrapped = "root_for_validation:\n" + "\n".join("  " + l for l in suffix.splitlines())
            try:
                yaml.safe_load(wrapped)
            except YAMLError as exc:
                mark = getattr(exc, 'problem_mark', None)
                if mark:
                    approx_line = last_reported + mark.line
                    msg = str(getattr(exc, 'problem', exc))
                    ctx = getattr(exc, 'context', None)
                    if ctx:
                        msg = f"{ctx} {msg}".strip()
                    if "expected ':'" in msg.lower() or "mapping values" in msg.lower():
                        msg += " (check indentation/key on lines above)"
                    _add_error(approx_line, (mark.column + 1) if mark else 1, msg)

    errors.sort(key=lambda e: (e["line"], e.get("column", 0)))
    return {"valid": False, "errors": errors}


# Simple in-memory cache for expensive host listings (short TTL to keep data fresh)
_CACHE = {}

def _cached(fn, key, ttl=30, *args, **kwargs):
    now = time.time()
    if key in _CACHE:
        ts, val = _CACHE[key]
        if now - ts < ttl:
            return val
    val = fn(*args, **kwargs)
    _CACHE[key] = (now, val)
    return val


def list_containers(server: Server) -> List[Dict]:
    """List all containers... (cached short time)"""
    key = f"containers_{server.id}"
    return _cached(_list_containers_uncached, key, 15, server)

def _list_containers_uncached(server: Server) -> List[Dict]:
    """List all containers on the host with enhanced info: running status, version from image tag, ports."""
    client = get_ssh_client(server)
    try:
        cmd = 'docker ps -a --format "{{json .}}"'
        status, out, err = run_command(client, cmd, timeout=30)
    finally:
        client.close()

    containers = []
    for line in out.strip().splitlines():
        if not line.strip():
            continue
        try:
            c = json.loads(line)
            image = c.get("Image", "")
            version = ""
            if ":" in image:
                version = image.split(":", 1)[1]
            ports_raw = c.get("Ports", "") or ""
            ports = [p.strip() for p in ports_raw.split(",") if p.strip()] if ports_raw else []
            state = c.get("State", "").lower()
            running = "running" in state or "up" in c.get("Status", "").lower()
            containers.append({
                "id": c.get("ID", "")[:12],
                "name": c.get("Names", ""),
                "image": image,
                "version": version,
                "status": c.get("Status", ""),
                "state": state,
                "running": running,
                "ports": ports,
                "ports_display": ports_raw or "—",
                "created": c.get("CreatedAt", ""),
                "command": c.get("Command", ""),
            })
        except Exception:
            pass
    return containers


def list_compose_projects(server: Server, base_dir: Optional[str] = None) -> List[Dict]:
    """List... (cached)"""
    key = f"compose_{server.id}"
    return _cached(_list_compose_uncached, key, 30, server, base_dir)

def _list_compose_uncached(server: Server, base_dir: Optional[str] = None) -> List[Dict]:
    """List docker compose projects under the base dir, with service versions if possible."""
    if not base_dir:
        base_dir = server.docker_base_dir.replace("~", f"/home/{server.ssh_username}")
    client = get_ssh_client(server)
    cmd = f'find {base_dir} -maxdepth 2 -name "docker-compose.yml" -o -name "docker-compose.yaml" -o -name "compose.yml" -o -name "compose.yaml" 2>/dev/null | head -30'
    status, out, err = run_command(client, cmd, timeout=20)

    projects = []
    for path in out.strip().splitlines():
        if not path:
            continue
        proj_dir = path.rsplit("/", 1)[0]
        proj_name = proj_dir.split("/")[-1]
        # Try to get versions from compose ps
        versions = []
        try:
            ps_cmd = f'cd {proj_dir} && docker compose ps --format "{{{{json .}}}}" 2>/dev/null | head -20'
            _, ps_out, _ = run_command(client, ps_cmd, timeout=15)
            for line in ps_out.strip().splitlines():
                if line:
                    p = json.loads(line)
                    svc = p.get("Service", "")
                    img = p.get("Image", "")
                    ver = ""
                    if ":" in img:
                        ver = img.split(":", 1)[1]
                    if svc:
                        versions.append(f"{svc}:{ver}" if ver else svc)
        except:
            pass
        # detect build services + dockerfile path from compose (using cat over same session)
        build_services = []
        has_build = False
        dockerfile_path = None
        try:
            cat_cmd = f"cat {path} 2>/dev/null | head -100"
            _, cat_out, _ = run_command(client, cat_cmd, timeout=10)
            comp = yaml.safe_load(cat_out) or {}
            svcs = comp.get("services") or {}
            for nm, cfg in svcs.items():
                if isinstance(cfg, dict) and cfg.get("build"):
                    build_services.append(nm)
            has_build = len(build_services) > 0
            services = list(svcs.keys()) if isinstance(svcs, dict) else []

            if has_build and build_services:
                first = build_services[0]
                bcfg = svcs.get(first, {}) or {}
                if isinstance(bcfg, dict):
                    b = bcfg.get("build") or {}
                    if isinstance(b, dict):
                        df = b.get("dockerfile", "Dockerfile")
                        ctx = b.get("context", ".")
                    else:
                        df = "Dockerfile"
                        ctx = str(b) if b else "."
                else:
                    df = "Dockerfile"
                    ctx = "."
                import os
                dockerfile_path = os.path.normpath(f"{proj_dir}/{ctx}/{df}").replace("\\", "/")
        except:
            pass

        projects.append({
            "name": proj_name,
            "path": proj_dir,
            "compose_file": path,
            "versions": versions or ["(not running)"],
            "services": services,
            "build_services": build_services,
            "has_build": has_build,
            "dockerfile_path": dockerfile_path
        })
    client.close()
    return projects


# === Cleanup: unused / dangling for issue 8 ===
def list_unused_images_and_containers(server: Server) -> dict:
    """List dangling images and exited containers (roughly unused). Short-lived SSH."""
    client = get_ssh_client(server)
    try:
        _, dimg, _ = run_command(client, 'docker images --filter "dangling=true" --format "{{.ID}} {{.Repository}}:{{.Tag}} {{.Size}}"', timeout=20)
        dangling = [l for l in dimg.strip().splitlines() if l][:20]
        _, ex, _ = run_command(client, 'docker ps -a --filter "status=exited" --format "{{.ID}} {{.Names}} {{.Image}}"', timeout=20)
        exited = [l for l in ex.strip().splitlines() if l][:20]
        return {"dangling_images": dangling, "exited_containers": exited}
    finally:
        client.close()

def prune_unused(server: Server, prune_type: str = 'both') -> dict:
    """Prune based on type: 'images' (dangling), 'containers' (exited), or 'both'."""
    client = get_ssh_client(server)
    try:
        outs = []
        if prune_type in ('images', 'both'):
            s, o, e = run_command(client, 'docker image prune -f --filter "dangling=true"', timeout=60)
            outs.append("Images: " + (o + e).strip())
        if prune_type in ('containers', 'both'):
            s, o, e = run_command(client, 'docker container prune -f', timeout=60)
            outs.append("Containers: " + (o + e).strip())
        return {"success": True, "output": "\n".join(outs) or "Nothing to prune.", "type": prune_type}
    finally:
        client.close()


def stream_compose_build(server: Server, project_path: str, services: list = None, no_cache: bool = False):
    """Stream the output of docker compose build using SSE. Fresh SSH session for the build."""
    client = get_ssh_client(server)
    try:
        cmd = f"cd {project_path} && docker compose build"
        if no_cache:
            cmd += " --no-cache"
        if services:
            cmd += " " + " ".join(shlex.quote(s) for s in services)
        stdin, stdout, stderr = client.exec_command(cmd, timeout=None)
        # combine stdout and stderr for build output
        for line in stdout:
            if line:
                yield f"data: {line.rstrip()}\n\n"
        for line in stderr:
            if line:
                yield f"data: [ERR] {line.rstrip()}\n\n"
    except Exception as e:
        yield f"data: [ERROR] {str(e)}\n\n"
    finally:
        try:
            client.close()
        except:
            pass
