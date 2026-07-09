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
import traceback
import sys
from typing import List, Dict, Optional
import yaml
from yaml import YAMLError
from ..models import Server
from ..services.ssh import get_ssh_client, run_command, docker_base_expanded
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


def normalize_container_ref(name: str) -> str:
    """Normalize docker Names/ID from UI (leading /, multi-name, whitespace)."""
    cname = (name or "").strip()
    if not cname or cname == "error":
        return ""
    # docker ps may return "name1,name2" or "/name"
    if "," in cname:
        cname = cname.split(",")[0].strip()
    cname = cname.lstrip("/")
    return cname


def container_action(server: Server, name: str, action: str) -> Dict:
    """Perform action on a container: start, stop, restart.
    ``name`` may be container name or short/long ID (ID preferred).
    """
    valid = {"start", "stop", "restart"}
    if action not in valid:
        return {"success": False, "error": "Invalid action"}
    cname = normalize_container_ref(name)
    if not cname:
        return {"success": False, "error": "Invalid container name"}

    client = get_ssh_client(server)
    try:
        cmd = f"docker {action} {shlex.quote(cname)}"
        status, out, err = run_command(client, cmd, timeout=90)
        success = status == 0
        output = ((out or "") + (err or "")).strip()
        # Retry without leading path quirks: resolve name → ID
        if not success:
            # Resolve by name → ID (name filter is substring; prefer exact via inspect)
            _, id_out, _ = run_command(
                client,
                f"docker inspect --format '{{{{.Id}}}}' {shlex.quote(cname)} 2>/dev/null | head -c 64 || true",
                timeout=15,
            )
            cid = (id_out or "").strip()
            if cid and cid != cname and not cid.lower().startswith("error"):
                # strip sha256: prefix length — docker accepts full id
                status2, out2, err2 = run_command(
                    client, f"docker {action} {shlex.quote(cid)}", timeout=90
                )
                if status2 == 0:
                    success = True
                    cname = cid[:12]
                    output = ((out2 or "") + (err2 or "")).strip()
                else:
                    output = ((out2 or "") + (err2 or "") or output).strip()
        return {
            "success": success,
            "action": action,
            "name": cname,
            "output": output[:500],
            "error": None if success else (output[:300] or f"docker {action} failed"),
        }
    finally:
        try:
            client.close()
        except Exception:
            pass


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


def write_dockerfile(server: Server, dockerfile_full_path: str, content: str) -> tuple[bool, str]:
    """Write Dockerfile via SFTP. Uses tmp + rename for safety.
    Returns (success, error_message_or_empty).
    """
    client = get_ssh_client(server)
    sftp = client.open_sftp()
    success = False
    err = ""
    tmp = dockerfile_full_path + ".tmp"
    try:
        # ensure containing directory exists on remote (common when ctx subdir)
        try:
            d = dockerfile_full_path.rsplit("/", 1)[0] if "/" in dockerfile_full_path else ""
            if d:
                run_command(client, f"mkdir -p {shlex.quote(d)}", timeout=15)
        except Exception:
            pass
        # remove stale tmp if present
        try:
            sftp.remove(tmp)
        except Exception:
            pass
        data = content.encode("utf-8") if isinstance(content, str) else content
        with sftp.open(tmp, "wb") as f:
            f.write(data)
        # Pre-remove target to ensure rename succeeds on all SFTP servers (some do not overwrite via rename).
        try:
            sftp.remove(dockerfile_full_path)
        except Exception:
            pass
        sftp.rename(tmp, dockerfile_full_path)
        success = True
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        try:
            print("[write_dockerfile] failed for", dockerfile_full_path, ":", err, file=sys.stderr)
            traceback.print_exc()
        except Exception:
            pass
        try:
            sftp.remove(tmp)
        except Exception:
            pass
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass
    return success, err


def write_compose_file(server: Server, project_path: str, content: str) -> tuple[bool, str]:
    """Write (overwrite) a docker-compose file via SFTP.
    Returns (success, error_message_or_empty).
    """
    client = get_ssh_client(server)
    sftp = client.open_sftp()

    # Try to determine the file name that exists or default to docker-compose.yml
    target = f"{project_path}/docker-compose.yml"
    tmp_target = None
    err = ""
    try:
        # Check which one exists (support all common names)
        for candidate in [
            f"{project_path}/docker-compose.yml",
            f"{project_path}/docker-compose.yaml",
            f"{project_path}/compose.yml",
            f"{project_path}/compose.yaml",
        ]:
            try:
                sftp.stat(candidate)
                target = candidate
                break
            except Exception:
                pass
    except Exception:
        pass

    try:
        # ensure dir exists
        try:
            run_command(client, f"mkdir -p {shlex.quote(project_path)}", timeout=15)
        except Exception:
            pass

        tmp_target = target + ".tmp"
        try:
            sftp.remove(tmp_target)
        except Exception:
            pass

        data = content.encode("utf-8") if isinstance(content, str) else content
        with sftp.open(tmp_target, "wb") as f:
            f.write(data)
        # Pre-remove target to ensure rename succeeds on all SFTP servers (some do not overwrite via rename).
        try:
            sftp.remove(target)
        except Exception:
            pass
        # atomic-ish replace
        sftp.rename(tmp_target, target)
        return True, ""
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        try:
            print("[write_compose_file] failed for", target, ":", err, file=sys.stderr)
            traceback.print_exc()
        except Exception:
            pass
        try:
            if tmp_target:
                sftp.remove(tmp_target)
        except Exception:
            pass
        return False, err
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass


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


def _image_id_remote(client, image: str) -> str:
    img = (image or "").strip()
    if not img:
        return ""
    _, out, _ = run_command(
        client,
        f"docker image inspect --format '{{{{.Id}}}}' {shlex.quote(img)} 2>/dev/null || true",
        timeout=20,
    )
    return (out or "").strip()


def classify_compose_images(client, project_path: str) -> dict:
    """
    Split compose images into registry-pullable vs local-build.

    "Update available" only makes sense for services without a ``build:`` section
    (pure pull images). Local/Dockerfile builds are updated via **Build**, not pull.
    """
    qdir = shlex.quote(project_path)
    _, cfg_raw, _ = run_command(
        client,
        f"cd {qdir} && docker compose config --format json 2>/dev/null || true",
        timeout=30,
    )
    pullable: list[str] = []
    build_local: list[str] = []
    build_services: list[str] = []
    pull_services: list[str] = []
    try:
        cfg = json.loads(cfg_raw or "{}") if (cfg_raw or "").strip() else {}
    except Exception:
        cfg = {}
    services = cfg.get("services") or {}
    if isinstance(services, dict):
        for svc_name, svc in services.items():
            if not isinstance(svc, dict):
                continue
            has_build = bool(svc.get("build"))
            image = (svc.get("image") or "").strip()
            if has_build:
                build_services.append(str(svc_name))
                if image:
                    build_local.append(image)
            elif image:
                pull_services.append(str(svc_name))
                pullable.append(image)

    # Fallback if config json unavailable: treat all compose images as pullable
    if not pullable and not build_local and not build_services:
        _, images_raw, _ = run_command(
            client,
            f"cd {qdir} && docker compose config --images 2>/dev/null || true",
            timeout=20,
        )
        pullable = [l.strip() for l in (images_raw or "").strip().splitlines() if l.strip()]

    # Dedupe preserving order
    def _uniq(seq: list[str]) -> list[str]:
        seen = set()
        out = []
        for x in seq:
            if x and x not in seen:
                seen.add(x)
                out.append(x)
        return out

    return {
        "pullable_images": _uniq(pullable),
        "build_images": _uniq(build_local),
        "build_services": build_services,
        "pull_services": pull_services,
    }


def check_compose_updates(server: Server, project_path: str) -> Dict:
    """
    Check for newer *registry* images (docker compose pull + ID compare).

    Services with ``build:`` are ignored for update badges — rebuild is manual.
    """
    client = get_ssh_client(server)
    try:
        classified = classify_compose_images(client, project_path)
        images = list(classified.get("pullable_images") or [])
        if not images:
            return {
                "has_updates": False,
                "updated_images": [],
                "images": [],
                "skipped_build_only": True,
                "build_services": classified.get("build_services") or [],
                "pull_output": "No registry-pullable services (all build: local, or empty project).",
                "success": True,
            }

        before_ids = {img: _image_id_remote(client, img) for img in images}
        status, pull_out, pull_err = run_command(
            client,
            f"cd {shlex.quote(project_path)} && docker compose pull 2>&1",
            timeout=180,
        )
        after_ids = {img: _image_id_remote(client, img) for img in images}
        updated_images = [
            img for img in images
            if before_ids.get(img) != after_ids.get(img)
        ]
        has_updates = bool(updated_images)

        return {
            "has_updates": has_updates,
            "updated_images": updated_images,
            "images": images,
            "build_services": classified.get("build_services") or [],
            "pull_output": ((pull_out or "") + (pull_err or "")).strip()[-600:],
            "success": status == 0 or "Pulled" in (pull_out or "") or has_updates,
        }
    finally:
        client.close()


def parse_container_updates_summary(server: Server) -> dict:
    """Parse Server.container_updates_summary into project names + image refs."""
    projects: set[str] = set()
    images: set[str] = set()
    project_images: dict[str, list[str]] = {}
    raw = getattr(server, "container_updates_summary", None) or ""
    if not raw:
        return {"projects": projects, "images": images, "project_images": project_images}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {"projects": projects, "images": images, "project_images": project_images}
    if not isinstance(data, dict):
        return {"projects": projects, "images": images, "project_images": project_images}
    for p in data.get("projects") or []:
        if p:
            projects.add(str(p).strip())
    details = data.get("project_details") or {}
    if isinstance(details, dict):
        for pname, det in details.items():
            if not pname:
                continue
            projects.add(str(pname).strip())
            imgs = []
            if isinstance(det, dict):
                imgs = list(det.get("images") or [])
            elif isinstance(det, list):
                imgs = list(det)
            project_images[str(pname).strip()] = [str(i) for i in imgs if i]
            for i in imgs:
                if i:
                    images.add(str(i).strip())
    return {"projects": projects, "images": images, "project_images": project_images}


def _image_ref_matches(container_image: str, updated_refs: set[str] | list[str]) -> bool:
    """True if container Image field matches a checked-updated compose image ref."""
    if not container_image or not updated_refs:
        return False
    ci = str(container_image).strip().split("@")[0]
    if not ci:
        return False
    for ref in updated_refs:
        r = str(ref).strip().split("@")[0]
        if not r:
            continue
        if ci == r or ci.startswith(r + ":") or r.startswith(ci + ":"):
            return True
        # same repo different tag still highlight if exact ref in updated set is close
        if ci.split(":")[0] == r.split(":")[0] and (":" in r and ci.endswith(":" + r.split(":")[-1])):
            return True
    return False


def annotate_update_flags(
    projects: List[Dict],
    orphan_containers: List[Dict],
    server: Server,
) -> tuple[List[Dict], List[Dict]]:
    """
    Mark projects/containers with pending image updates from last fleet check.
    Per-image when summary has project_details; otherwise whole stack is flagged.
    """
    info = parse_container_updates_summary(server)
    proj_names = info["projects"]
    all_images = info["images"]
    per_proj = info["project_images"]

    out_projects = []
    for proj in projects:
        row = dict(proj)
        name = (row.get("name") or "").strip()
        stack_update = name in proj_names
        row["has_pending_update"] = stack_update
        stack_imgs = set(per_proj.get(name) or [])
        if not stack_imgs and stack_update:
            stack_imgs = set(all_images)  # older summaries: flag all services in stack
        attached = []
        for c in row.get("containers") or []:
            cc = dict(c)
            if cc.get("placeholder"):
                cc["has_pending_update"] = False
            elif stack_update:
                if stack_imgs:
                    cc["has_pending_update"] = _image_ref_matches(cc.get("image") or "", stack_imgs)
                else:
                    cc["has_pending_update"] = True
            else:
                cc["has_pending_update"] = False
            attached.append(cc)
        row["containers"] = attached
        row["update_container_count"] = sum(1 for x in attached if x.get("has_pending_update"))
        out_projects.append(row)

    out_orphans = []
    for c in orphan_containers or []:
        cc = dict(c)
        cc["has_pending_update"] = bool(all_images) and _image_ref_matches(
            cc.get("image") or "", all_images
        )
        out_orphans.append(cc)
    return out_projects, out_orphans


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


def _parse_compose_labels(labels) -> dict:
    """Extract compose project/service/workdir from docker Labels (str or dict)."""
    out = {
        "compose_project": "",
        "compose_service": "",
        "compose_workdir": "",
    }
    if not labels:
        return out
    if isinstance(labels, dict):
        out["compose_project"] = labels.get("com.docker.compose.project") or labels.get("Project") or ""
        out["compose_service"] = labels.get("com.docker.compose.service") or labels.get("Service") or ""
        out["compose_workdir"] = labels.get("com.docker.compose.project.working_dir") or ""
        return out
    text = str(labels)
    for part in text.split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k == "com.docker.compose.project":
            out["compose_project"] = v
        elif k == "com.docker.compose.service":
            out["compose_service"] = v
        elif k == "com.docker.compose.project.working_dir":
            out["compose_workdir"] = v
    return out


def list_containers(server: Server) -> List[Dict]:
    """List all containers... (cached short time)"""
    key = f"containers_{server.id}"
    return _cached(_list_containers_uncached, key, 15, server)


def _list_containers_uncached(server: Server) -> List[Dict]:
    """List all containers with status, compose labels, ports, mounts, size."""
    client = get_ssh_client(server)
    try:
        # --size adds Size (writable + virtual); useful on expand details
        cmd = 'docker ps -a --size --format "{{json .}}"'
        status, out, err = run_command(client, cmd, timeout=45)
    finally:
        try:
            client.close()
        except Exception:
            pass

    if status != 0:
        err_msg = (err or out or "docker ps failed").strip()[:300]
        return [{
            "id": "",
            "name": "error",
            "image": "",
            "version": "",
            "status": err_msg or "command failed",
            "state": "error",
            "running": False,
            "ports": [],
            "ports_display": "—",
            "created": "",
            "command": "",
            "mounts": "",
            "mounts_list": [],
            "size": "",
            "local_volumes": "",
            "compose_project": "",
            "compose_service": "",
            "compose_workdir": "",
        }]

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
            state = (c.get("State") or "").lower()
            running = "running" in state or "up" in (c.get("Status") or "").lower()
            labels = _parse_compose_labels(c.get("Labels") or "")
            if c.get("Project") and not labels["compose_project"]:
                labels["compose_project"] = c.get("Project") or ""
            if c.get("Service") and not labels["compose_service"]:
                labels["compose_service"] = c.get("Service") or ""
            name = c.get("Names") or c.get("Name") or ""
            if isinstance(name, list):
                name = name[0] if name else ""
            name = normalize_container_ref(str(name))
            cmd_raw = c.get("Command") or ""
            if isinstance(cmd_raw, str):
                cmd_raw = cmd_raw.strip().strip('"')
            mounts_raw = c.get("Mounts") or ""
            if isinstance(mounts_raw, list):
                mounts_list = [str(m).strip() for m in mounts_raw if str(m).strip()]
            else:
                # docker ps joins mounts with commas (paths often truncated with …)
                mounts_list = [m.strip() for m in str(mounts_raw).split(",") if m.strip()]
            ports_list = ports if ports else (
                [p.strip() for p in ports_raw.split(",") if p.strip()] if ports_raw else []
            )
            full_id = (c.get("ID") or "")[:64]
            containers.append({
                "id": full_id[:12],
                "id_full": full_id,
                "name": name,
                "image": image,
                "version": version,
                "status": c.get("Status", ""),
                "state": state,
                "running": running,
                "ports": ports_list,
                "ports_display": ports_raw or "—",
                "created": c.get("CreatedAt", "") or "",
                "command": cmd_raw,
                "mounts": mounts_raw if isinstance(mounts_raw, str) else ", ".join(mounts_list),
                "mounts_list": mounts_list,
                "size": c.get("Size") or "",
                "local_volumes": c.get("LocalVolumes") or "",
                **labels,
            })
        except Exception:
            pass
    # docker ps truncates mount paths — fill full Source:Destination via inspect
    try:
        _enrich_container_mounts(server, containers)
    except Exception:
        pass
    return containers


def _human_bytes(n: int) -> str:
    """Compact size string for UI."""
    try:
        n = int(n)
    except Exception:
        return ""
    if n < 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1024.0 or u == units[-1]:
            if u == "B":
                return f"{int(f)} B"
            return f"{f:.1f} {u}"
        f /= 1024.0
    return f"{n} B"


def _parse_inspect_mount(m: dict) -> dict:
    """Structured mount from docker inspect Mounts entry."""
    src = m.get("Source") or m.get("Name") or ""
    dst = m.get("Destination") or m.get("Target") or ""
    mode = m.get("Mode") or ""
    mtype = (m.get("Type") or "").lower() or "bind"
    ro = m.get("RW") is False or m.get("ReadOnly") is True
    name = m.get("Name") or ""
    return {
        "source": src,
        "destination": dst,
        "type": mtype,
        "name": name,
        "ro": ro,
        "mode": mode,
        "size_bytes": None,
        "size_human": "",
    }


def _format_mount_line(m: dict) -> str:
    """Human line for a structured mount (optionally with size)."""
    src = m.get("source") or m.get("name") or ""
    dst = m.get("destination") or ""
    mtype = m.get("type") or ""
    ro = m.get("ro")
    if src and dst:
        line = f"{src} → {dst}"
    elif dst:
        line = dst
    elif src:
        line = src
    else:
        line = "—"
    extra = []
    if mtype:
        extra.append(mtype)
    if ro:
        extra.append("ro")
    size_h = m.get("size_human") or ""
    if size_h:
        extra.append(size_h)
    if extra:
        line = f"{line} ({', '.join(extra)})"
    return line


def _du_sizes_for_paths(client, paths: List[str]) -> dict:
    """Return {path: size_bytes} via one remote du -sb for existing paths.

    Named docker volumes live under /var/lib/docker/volumes — may need sudo.
    """
    paths = [p for p in paths if p and p.startswith("/")]
    # de-dupe, cap work
    uniq: list = []
    seen = set()
    for p in paths:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
        if len(uniq) >= 80:
            break
    if not uniq:
        return {}
    quoted = " ".join(shlex.quote(p) for p in uniq)
    # Prefer plain du; fall back to sudo -n for volume store paths
    cmd = (
        f"du -sb {quoted} 2>/dev/null; "
        f"sudo -n du -sb {quoted} 2>/dev/null"
    )
    status, out, _err = run_command(client, cmd, timeout=45)
    if not (out or "").strip():
        return {}
    sizes: dict = {}
    for line in out.strip().splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            b = int(parts[0])
        except ValueError:
            continue
        path = parts[1].strip()
        # keep largest if both plain + sudo reported
        if path not in sizes or b > sizes[path]:
            sizes[path] = b
    return sizes


def _enrich_container_mounts(server: Server, containers: List[Dict]) -> None:
    """Replace truncated docker-ps mounts with full paths + disk usage from host.

    1) ``docker inspect`` for Source/Destination (full paths)
    2) ``du -sb`` on unique Source paths for space used (bind mounts + volume data dirs)
    """
    if not containers or containers[0].get("name") == "error":
        return
    client = get_ssh_client(server)
    try:
        cmd = (
            "ids=$(docker ps -aq 2>/dev/null | head -n 200); "
            'if [ -n "$ids" ]; then docker inspect $ids 2>/dev/null; else echo []; fi'
        )
        status, out, _err = run_command(client, cmd, timeout=90)
        if status != 0 or not (out or "").strip():
            names = [c.get("name") for c in containers if c.get("name") and c.get("name") != "error"]
            if not names:
                return
            quoted = " ".join(shlex.quote(n) for n in names[:80])
            status, out, _err = run_command(
                client, f"docker inspect {quoted} 2>/dev/null || true", timeout=90
            )
            if status != 0 or not (out or "").strip():
                return
        try:
            data = json.loads(out)
        except Exception:
            return
        if not isinstance(data, list):
            return

        by_id: dict = {}
        by_short: dict = {}
        by_name: dict = {}
        all_sources: list = []
        for item in data:
            if not isinstance(item, dict):
                continue
            raw_mounts = item.get("Mounts") or []
            if not isinstance(raw_mounts, list):
                raw_mounts = []
            structured = [
                _parse_inspect_mount(m) for m in raw_mounts if isinstance(m, dict)
            ]
            structured = [m for m in structured if m.get("source") or m.get("destination")]
            for m in structured:
                if m.get("source"):
                    all_sources.append(m["source"])
            cid = (item.get("Id") or "").strip()
            if cid.startswith("sha256:"):
                cid = cid[7:]
            names = item.get("Name") or ""
            if isinstance(names, str):
                n = names.lstrip("/")
                if n:
                    by_name[n] = structured
                    by_name[normalize_container_ref(n)] = structured
            if cid:
                by_id[cid] = structured
                by_short[cid[:12]] = structured

        # Disk usage for host paths (bind mounts + volume _data dirs)
        size_map: dict = {}
        try:
            size_map = _du_sizes_for_paths(client, all_sources)
        except Exception:
            size_map = {}

        def apply_sizes(mounts: list) -> list:
            out_m = []
            for m in mounts:
                mm = dict(m)
                src = mm.get("source") or ""
                b = size_map.get(src)
                if b is not None:
                    mm["size_bytes"] = b
                    mm["size_human"] = _human_bytes(b)
                out_m.append(mm)
            return out_m

        for c in containers:
            full = (c.get("id_full") or c.get("id") or "").strip()
            if full.startswith("sha256:"):
                full = full[7:]
            name = normalize_container_ref(c.get("name") or "")
            mounts = (
                by_id.get(full)
                or by_short.get(full[:12] if full else "")
                or by_name.get(name)
                or by_name.get(c.get("name") or "")
            )
            if mounts:
                mounts = apply_sizes(mounts)
                c["mounts_detail"] = mounts
                lines = [_format_mount_line(m) for m in mounts]
                c["mounts_list"] = lines
                c["mounts"] = ", ".join(lines)
                total = sum(int(m.get("size_bytes") or 0) for m in mounts if m.get("size_bytes"))
                c["mounts_total_bytes"] = total or None
                c["mounts_total_human"] = _human_bytes(total) if total else ""
            elif c.get("mounts_list"):
                cleaned = [
                    m for m in c["mounts_list"]
                    if m and "…" not in m and "..." not in m
                ]
                if cleaned:
                    c["mounts_list"] = cleaned
    finally:
        try:
            client.close()
        except Exception:
            pass


def nest_containers_under_projects(projects: List[Dict], containers: List[Dict]):
    """
    Attach container rows under matching compose projects.
    Match: compose_workdir == path, else compose_project == project name.
    Returns (projects_with_containers, orphan_containers).
    """
    assigned: set = set()
    by_workdir: dict = {}
    by_project: dict = {}
    for i, c in enumerate(containers):
        if c.get("name") == "error":
            continue
        wd = (c.get("compose_workdir") or "").rstrip("/")
        if wd:
            by_workdir.setdefault(wd, []).append(i)
        pn = (c.get("compose_project") or "").strip()
        if pn:
            by_project.setdefault(pn, []).append(i)

    enriched = []
    for proj in projects:
        row = dict(proj)
        path = (proj.get("path") or "").rstrip("/")
        name = (proj.get("name") or "").strip()
        idxs = []
        seen_i = set()
        for i in by_workdir.get(path, []):
            if i not in seen_i:
                idxs.append(i)
                seen_i.add(i)
        if not idxs:
            for i in by_project.get(name, []):
                if i not in seen_i:
                    idxs.append(i)
                    seen_i.add(i)
        declared = list(proj.get("services") or [])
        present_services = set()
        attached = []
        for i in idxs:
            c = dict(containers[i])
            svc = c.get("compose_service") or ""
            if svc:
                present_services.add(svc)
            attached.append(c)
            assigned.add(i)
        for svc in declared:
            if svc not in present_services:
                attached.append({
                    "id": "",
                    "name": "",
                    "image": "",
                    "version": "",
                    "status": "not created",
                    "state": "missing",
                    "running": False,
                    "ports": [],
                    "ports_display": "—",
                    "created": "",
                    "command": "",
                    "mounts": "",
                    "mounts_list": [],
                    "size": "",
                    "local_volumes": "",
                    "compose_project": name,
                    "compose_service": svc,
                    "compose_workdir": path,
                    "placeholder": True,
                })
        attached.sort(key=lambda x: (0 if x.get("running") else 1, (x.get("compose_service") or x.get("name") or "").lower()))
        row["containers"] = attached
        row["running_count"] = sum(1 for x in attached if x.get("running"))
        row["container_count"] = len([x for x in attached if not x.get("placeholder")])
        enriched.append(row)

    orphans = [c for i, c in enumerate(containers) if i not in assigned]
    return enriched, orphans


def list_compose_projects(server: Server, base_dir: Optional[str] = None) -> List[Dict]:
    """List... (cached)"""
    key = f"compose_{server.id}"
    return _cached(_list_compose_uncached, key, 30, server, base_dir)

def _list_compose_uncached(server: Server, base_dir: Optional[str] = None) -> List[Dict]:
    """List docker compose projects under the base dir, with service versions if possible."""
    if not base_dir:
        base_dir = docker_base_expanded(server)
    client = get_ssh_client(server)
    try:
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
            services = []  # ensure always defined (prevents UnboundLocalError/NameError on partial failures)
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
                # Fallback: always expose a root Dockerfile candidate so the editor tab can appear
                # and users can create/edit even if no build: section references it.
                if not dockerfile_path:
                    dockerfile_path = f"{proj_dir}/Dockerfile"
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
        return projects
    finally:
        try:
            client.close()
        except:
            pass



# === Cleanup: unused / dangling for issue 8 ===
def list_unused_images_and_containers(server: Server) -> dict:
    """List dangling images and exited containers (roughly unused). Short-lived SSH."""
    client = get_ssh_client(server)
    try:
        s1, dimg, e1 = run_command(client, 'docker images --filter "dangling=true" --format "{{.ID}} {{.Repository}}:{{.Tag}} {{.Size}}"', timeout=20)
        dangling = [l for l in dimg.strip().splitlines() if l][:20]
        s2, ex, e2 = run_command(client, 'docker ps -a --filter "status=exited" --format "{{.ID}} {{.Names}} {{.Image}}"', timeout=20)
        exited = [l for l in ex.strip().splitlines() if l][:20]
        errs = []
        if s1 != 0 and e1:
            errs.append("images: " + e1.strip()[:200])
        if s2 != 0 and e2:
            errs.append("containers: " + e2.strip()[:200])
        return {
            "dangling_images": dangling,
            "exited_containers": exited,
            "success": (s1 == 0 and s2 == 0),
            "errors": errs
        }
    finally:
        try:
            client.close()
        except:
            pass

def prune_unused(server: Server, prune_type: str = 'both') -> dict:
    """Prune based on type: 'images' (dangling), 'containers' (exited), or 'both'."""
    valid_types = ('images', 'containers', 'both')
    if prune_type not in valid_types:
        return {"success": False, "output": "Invalid prune_type", "type": prune_type}

    client = get_ssh_client(server)
    try:
        outs = []
        success = True
        if prune_type in ('images', 'both'):
            s, o, e = run_command(client, 'docker image prune -f --filter "dangling=true"', timeout=60)
            outs.append("Images: " + (o + e).strip())
            if s != 0:
                success = False
        if prune_type in ('containers', 'both'):
            s, o, e = run_command(client, 'docker container prune -f', timeout=60)
            outs.append("Containers: " + (o + e).strip())
            if s != 0:
                success = False
        return {
            "success": success,
            "output": "\n".join(outs) or "Nothing to prune.",
            "type": prune_type
        }
    finally:
        try:
            client.close()
        except:
            pass


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
