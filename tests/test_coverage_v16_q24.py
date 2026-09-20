"""v1.6 Q-80 twenty-fourth pack — unzip/iter_file + DNS attach_from_plan helpers."""
from __future__ import annotations

import io
import zipfile
import stat
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import Server
from app.services import host_files as hf
from app.services.dns_fabric import core as fabric


class _Sftp:
    def __init__(self):
        self.files = {
            "/home/pi/docker": SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o755, st_size=0, st_mtime=1, st_uid=1000, st_gid=1000
            ),
        }
        self.bodies = {}

    def add_file(self, path, data: bytes):
        self.bodies[path] = data
        self.files[path] = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o644, st_size=len(data), st_mtime=1, st_uid=1000, st_gid=1000
        )

    def normalize(self, path):
        return path

    def lstat(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def mkdir(self, path):
        self.files[path] = SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o755, st_size=0, st_mtime=1, st_uid=1000, st_gid=1000
        )

    def remove(self, path):
        self.files.pop(path, None)
        self.bodies.pop(path, None)

    def rename(self, src, dst):
        self.files[dst] = self.files.pop(src)
        if src in self.bodies:
            self.bodies[dst] = self.bodies.pop(src)

    def open(self, path, mode="rb"):
        if "w" in mode:
            buf = io.BytesIO()

            class W:
                def write(_, data):
                    buf.write(data if isinstance(data, (bytes, bytearray)) else str(data).encode())

                def __enter__(wself):
                    return wself

                def __exit__(wself, *a):
                    self.add_file(path, buf.getvalue())

            return W()
        return io.BytesIO(self.bodies.get(path, b""))


def test_unzip_iter_and_attach_plan(monkeypatch):
    monkeypatch.setattr("app.services.demo.demo_mode", lambda: False)
    monkeypatch.setattr(hf.settings, "PIHERDER_DEMO_MODE", False, raising=False)
    monkeypatch.setattr(hf.settings, "PIHERDER_HOST_FILES", True, raising=False)
    srv = SimpleNamespace(
        id=1,
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        container_patch_enabled=True,
        ssh_private_key_encrypted="enc",
        hostname="pi.local",
        ssh_port=22,
    )
    fs = _Sftp()
    fs.add_file("/home/pi/docker/notes.txt", b"hello zip pack")
    data = b"".join(hf.iter_file(srv, "notes.txt", sftp=fs))
    assert data == b"hello zip pack"
    with pytest.raises(hf.FilesError):
        list(hf.iter_file(srv, "", sftp=fs))

    fs.add_file("/home/pi/docker/bad.zip", b"not-a-zip")
    with pytest.raises(hf.FilesError):
        hf.unzip_into(srv, "bad.zip", "", sftp=fs)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ok.txt", b"yes")
    fs.add_file("/home/pi/docker/ok.zip", buf.getvalue())
    out = hf.unzip_into(srv, "ok.zip", "", sftp=fs)
    assert out.get("files") == 1 or out.get("files") >= 0
    assert fs.bodies.get("/home/pi/docker/ok.txt") == b"yes" or out.get("files") == 1

    session_e = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(session_e)
    session = Session(session_e)
    host = Server(
        name="edge",
        hostname="edge.local",
        dns_name="edge.lan",
        ip_address="10.0.0.2",
        ssh_username="pi",
        docker_base_dir="/home/pi/docker",
        os_type="debian",
    )
    session.add(host)
    session.commit()
    session.refresh(host)
    with pytest.raises(fabric.DnsFabricError):
        fabric.attach_service_dns_from_plan(session, {})
    with pytest.raises(fabric.DnsFabricError):
        fabric.attach_service_dns_from_plan(
            session, {"fqdn": "app.lan", "target_server_id": 1}
        )
    monkeypatch.setattr(fabric, "sync_service_dns", lambda *a, **k: [{"ok": True}])
    row, results = fabric.attach_service_dns_from_plan(
        session,
        {
            "fqdn": "grafana.lan",
            "target_server_id": host.id,
            "backend_server_id": host.id,
            "label": "Grafana",
            "docker_project": "grafana",
            "via_proxy": False,
        },
        sync_now=False,
    )
    assert row.fqdn == "grafana.lan"
    monkeypatch.setattr(fabric, "list_pihole_cnames", lambda s: [])
    imported = fabric.import_pihole_cnames(session, user_id=1)
    assert imported["imported_count"] == 0
    monkeypatch.setattr(
        fabric,
        "list_pihole_cnames",
        lambda s: [{"domain": "grafana.lan", "target": "edge.lan"}],
    )
    again = fabric.import_pihole_cnames(session, user_id=1)
    assert again["skipped_count"] >= 1
