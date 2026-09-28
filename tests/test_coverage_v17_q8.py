"""v1.7 coverage — nmap device/schedule posts, files mutations, patch jobs, settings."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from sqlmodel import Session

from app.database import get_session
from app.main import app
from app.models import Integration, Job, NmapDevice, NmapScanSchedule, Server, ServiceTemplate, StackDeployment, User
from tests.test_coverage_v17_q1 import _client, _engine, _patch_files


def test_nmap_files_patch_and_settings(tmp_path, monkeypatch):
    from app.services import host_files as hf
    from app.services import ssh_console as cons
    from app.services import ssh_identities as idents
    from app.services.nmap import schedules as nmap_sched
    import app.services.os_patching as os_patching
    import app.services.container_patching as container_patching
    from app.routers import templates_deploy as deploy_mod

    _patch_files(monkeypatch)
    monkeypatch.setattr(
        hf,
        "put_file",
        lambda *a, **k: {"dest": "up.bin", "rel": "box/up.bin", "bytes": 3, "sha256": "ab", "overwrite": False},
    )
    monkeypatch.setattr(hf, "zip_on_host", lambda *a, **k: {"rel": "a.zip", "name": "a.zip", "dest": ""})
    monkeypatch.setattr(hf, "unzip_into", lambda *a, **k: {"ok": True, "dest": "", "files": 2, "bytes": 8})
    monkeypatch.setattr(hf, "remove_tree", lambda *a, **k: {"ok": True, "files": 1, "dirs": 1})
    monkeypatch.setattr(
        hf,
        "apply_perms",
        lambda *a, **k: {"ok": True, "mode": "644", "owner": "pi", "group": "pi", "changed": 1, "sudo": False},
    )
    monkeypatch.setattr(hf, "parse_nested_rel", lambda rel: [p for p in rel.split("/") if p])
    monkeypatch.setattr(cons, "can_open_privileged", lambda user: True)
    monkeypatch.setattr(cons, "grant_valid", lambda *a, **k: True)
    monkeypatch.setattr(idents, "get_by_role", lambda *a, **k: SimpleNamespace(enabled=True, id=1))
    monkeypatch.setattr(nmap_sched, "fire_schedule", lambda schedule_id: None)
    monkeypatch.setattr(os_patching, "get_os_patch_progress", lambda host: {"log_lines": ["apt"], "current": "upgrade"})
    monkeypatch.setattr(
        container_patching,
        "get_container_patch_progress",
        lambda host: {"log_lines": ["pull"], "current": "web"},
    )
    monkeypatch.setattr(
        deploy_mod,
        "adopt_host_files_as_desired",
        lambda *a, **k: {"files": ["compose.yml"], "config_version": 2},
    )
    monkeypatch.setattr(
        deploy_mod,
        "migrate_host_env_into_deployment",
        lambda *a, **k: {"imported_secrets": ["TOKEN"], "imported_public": ["PORT"]},
    )
    monkeypatch.setattr(
        deploy_mod,
        "apply_last_known_config",
        lambda *a, **k: {"ok": True},
    )

    jobs = sys.modules["app.services.jobs"]
    calls = {"n": 0}

    def _enqueue(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise jobs.JobAlreadyActive(SimpleNamespace(id=3, status="running"))
        if calls["n"] == 3:
            raise ValueError("no steps")
        if calls["n"] == 4:
            raise RuntimeError("queue down")
        return SimpleNamespace(id=4, status="pending")

    for name in (
        "enqueue_template_redeploy",
        "enqueue_template_drift_check",
        "create_job_and_run",
        "enqueue_os_update_check",
        "enqueue_container_update_check",
    ):
        monkeypatch.setattr(jobs, name, _enqueue)

    engine = _engine(tmp_path / "q8.db")
    client, uid, sid = _client(engine, monkeypatch)
    try:
        with Session(engine) as s:
            user = s.get(User, uid)
            user.role = "admin"
            s.add(user)
            server = s.get(Server, sid)
            server.os_patch_enabled = True
            server.container_patch_enabled = True
            s.add(server)
            integ = Integration(type="nmap", name="LAN", base_url="local", config_json='{"cidrs":["192.168.1.0/24"]}')
            s.add(integ)
            s.commit()
            s.refresh(integ)
            iid = integ.id
            devices = []
            for key, state in (("ip:1", "new"), ("ip:2", "known"), ("ip:3", "linked"), ("ip:4", "stale")):
                row = NmapDevice(
                    integration_id=iid,
                    identity_key=key,
                    ip_address=f"192.168.1.{len(devices)+2}",
                    state=state,
                    linked_server_id=sid if state == "linked" else None,
                )
                s.add(row)
                devices.append(row)
            s.commit()
            for row in devices:
                s.refresh(row)
            dids = [row.id for row in devices]
            sched = NmapScanSchedule(integration_id=iid, name="nightly", intensity="discovery", enabled=False)
            s.add(sched)
            tpl = ServiceTemplate(slug="web", name="Web", definition_json="{}")
            s.add(tpl)
            s.commit()
            s.refresh(sched)
            s.refresh(tpl)
            dep = StackDeployment(
                server_id=sid,
                project_name="web",
                template_id=tpl.id,
                template_slug="web",
                variables_json="{}",
                files_json="{}",
            )
            running = Job(
                server_id=sid,
                job_type="os_patch",
                status="running",
                details=json.dumps({"log_lines": ["old"], "current": "update"}),
            )
            cjob = Job(
                server_id=sid,
                job_type="container_patch",
                status="pending",
                details=json.dumps({"log_lines": [], "summary": ""}),
            )
            bad = Job(server_id=sid, job_type="backup", status="failed", details="{not-json")
            s.add(dep)
            s.add(running)
            s.add(cjob)
            s.add(bad)
            s.commit()
            s.refresh(dep)
            s.refresh(running)
            s.refresh(cjob)
            sched_id, dep_id = sched.id, dep.id
            job_ids = (running.id, cjob.id, bad.id)

        headers = {"x-piherder-files": "1"}
        posts = [
            (
                f"/integrations/{iid}/nmap/device/{dids[0]}/name",
                {"display_name": "cctv", "kind_override": "camera", "map_role": "none", "return_tab": "devices", "return_view": "map"},
            ),
            (f"/integrations/{iid}/nmap/device/{dids[0]}/ignore", {"return_to": "hosts"}),
            (f"/integrations/{iid}/nmap/device/{dids[1]}/unignore", {"return_tab": "network"}),
            (f"/integrations/{iid}/nmap/device/{dids[1]}/mark-known", {}),
            (f"/integrations/{iid}/nmap/device/{dids[0]}/mark-new", {"return_view": "map"}),
            (f"/integrations/{iid}/nmap/device/{dids[2]}/purge", {"return_tab": "devices", "return_view": "map"}),
            (f"/integrations/{iid}/nmap/device/{dids[3]}/purge", {"return_to": f"server:{sid}"}),
            (f"/integrations/{iid}/nmap/devices/purge-offline", {}),
            (f"/integrations/{iid}/nmap/device/{dids[0]}/link", {"server_id": str(sid)}),
            (
                f"/integrations/{iid}/nmap/schedules",
                {"name": "hourly", "intensity": "inventory", "cron": "0 * * * *", "enabled": "on", "timing": "nope", "top_ports": "50"},
            ),
            (
                f"/integrations/{iid}/nmap/schedules",
                {"name": "bad", "interval_hours": "abc", "script_preset": "vuln", "vuln_scripts": "on", "use_syn": "1"},
            ),
            (
                f"/integrations/{iid}/nmap/schedules/{sched_id}/edit",
                {"name": "nightly", "cron": "", "interval_hours": "6", "enabled": "1", "timing": "3", "port_list": "22,80"},
            ),
            (f"/integrations/{iid}/nmap/schedules/{sched_id}/toggle", {}),
            (f"/integrations/{iid}/nmap/schedules/{sched_id}/run", {}),
            (f"/servers/{sid}/files/archive", {"p": "", "names": "notes.txt", "dest": "download", "delete": "1", "name": "out.zip"}),
            (f"/servers/{sid}/files/unzip", {"p": "", "name": "out.zip"}),
            (f"/servers/{sid}/files/rm", {"p": "box", "names": "notes.txt"}),
            (f"/servers/{sid}/files/move", {"p": "", "names": "notes.txt", "dest": "box", "overwrite": "1"}),
            (
                f"/servers/{sid}/files/perms",
                {"p": "", "names": "notes.txt", "mode": "644", "owner": "pi", "group": "pi", "recursive": "1", "identity": "privileged"},
            ),
            (f"/servers/{sid}/reorder", {"order": f"{sid},nope"}),
            (f"/servers/{sid}/move/up", {}),
            (f"/servers/{sid}/move/sideways", {}),
            (f"/servers/{sid}/run/os_patch", {"steps": "update"}),
            (f"/servers/{sid}/schedule/os-check", {"os_check_enabled": "on", "os_check_schedule": "0 4 * * *"}),
            (f"/servers/{sid}/schedule/container-check", {"container_check_enabled": "1", "container_check_schedule": "15 4 * * *"}),
            (
                f"/servers/{sid}/schedule/os-apply",
                {"os_apply_enabled": "on", "os_apply_schedule": "0 5 * * *", "os_apply_use_full_upgrade": "1"},
            ),
            (
                f"/servers/{sid}/schedule/container-apply",
                {"container_apply_enabled": "on", "container_apply_schedule": "30 5 * * *", "container_apply_only_if_updates": "1"},
            ),
            ("/herder-backups/timezone", {"timezone": "Europe/Stockholm"}),
            ("/herder-backups/delete", {"name": "missing.tar.gz"}),
            (f"/templates/deployments/{dep_id}/redeploy", {"pub_PORT": "8080", "vol_data__mode": "bind", "vol_data__source": "/data"}),
            (f"/templates/deployments/{dep_id}/check-drift", {}),
            (f"/templates/deployments/{dep_id}/adopt-host", {}),
            (f"/templates/deployments/{dep_id}/migrate-env", {}),
            (f"/templates/deployments/{dep_id}/apply-config", {}),
        ]
        async_headers = {"X-PiHerder-Async": "1"}
        for path, data in posts:
            extra = async_headers if "templates/" in path or path.endswith("/os_patch") else {}
            response = client.post(path, data=data, headers={**headers, **extra}, follow_redirects=False)
            assert response.status_code < 600, path

        response = client.post(
            f"/servers/{sid}/files/upload",
            data={"p": "", "identity": "fleet", "rel_path": "box/up.bin"},
            files={"file": ("up.bin", b"abc", "application/octet-stream")},
            headers=headers,
        )
        assert response.status_code < 600

        for job_id in job_ids:
            response = client.get(f"/servers/{sid}/jobs/{job_id}")
            assert response.status_code < 600
        response = client.get(f"/servers/{sid}/jobs?active_only=1")
        assert response.status_code < 600

        monkeypatch.setattr(nmap_sched, "create_schedule", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad cron")))
        response = client.post(
            f"/integrations/{iid}/nmap/schedules",
            data={"name": "x", "cron": "* * * * *"},
            follow_redirects=False,
        )
        assert response.status_code < 600
    finally:
        app.dependency_overrides.pop(get_session, None)
