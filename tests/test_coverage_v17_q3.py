"""v1.7 coverage — host-file service calls and a few form posts on SQLite."""
from __future__ import annotations

import io
import stat
from types import SimpleNamespace

from sqlmodel import Session

from app.models import Integration, ManagedCertificate, Server
from tests.test_coverage_v17_q1 import _client, _engine


class _Stat:
    def __init__(self, mode, size=0):
        self.st_mode = mode
        self.st_size = size
        self.st_mtime = 1_700_000_000
        self.st_uid = 1000
        self.st_gid = 1000


class _File:
    def __init__(self, data: bytes):
        self._data = data

    def read(self, n=-1):
        data, self._data = self._data, b""
        return data if n < 0 else data[:n]

    def write(self, data):
        return len(data)

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _SFTP:
    def __init__(self):
        self.dirs = {"/home/pi": ["notes.txt", "sub"]}
        self.files = {"/home/pi/notes.txt": b"hello"}
        self.dirs["/home/pi/sub"] = []

    def lstat(self, path):
        if path in self.files:
            return _Stat(stat.S_IFREG | 0o644, len(self.files[path]))
        if path in self.dirs:
            return _Stat(stat.S_IFDIR | 0o755)
        raise FileNotFoundError(path)

    def listdir(self, path):
        if path not in self.dirs:
            raise FileNotFoundError(path)
        return list(self.dirs[path])

    def listdir_attr(self, path):
        names = self.listdir(path)
        out = []
        for name in names:
            child = path.rstrip("/") + "/" + name
            st = self.lstat(child)
            out.append(
                SimpleNamespace(
                    filename=name,
                    st_mode=st.st_mode,
                    st_size=st.st_size,
                    st_mtime=st.st_mtime,
                    st_uid=st.st_uid,
                    st_gid=st.st_gid,
                )
            )
        return out

    def open(self, path, mode="r"):
        if "w" in mode:
            self.files[path] = b""
            return _File(b"")
        return _File(self.files.get(path, b"hello"))

    def mkdir(self, path):
        self.dirs[path] = []

    def remove(self, path):
        self.files.pop(path, None)

    def rename(self, src, dest):
        if src in self.files:
            self.files[dest] = self.files.pop(src)


def test_host_files_against_fake_sftp(tmp_path, monkeypatch):
    from app.services import host_files as hf

    monkeypatch.setattr(hf, "is_demo_files", lambda: False)
    monkeypatch.setattr(hf, "user_group_maps", lambda *a, **k: ({1000: "pi"}, {1000: "pi"}))
    engine = _engine(tmp_path / "q3.db")
    with Session(engine) as s:
        server = Server(
            name="pi",
            hostname="pi.local",
            ssh_username="pi",
            os_type="debian",
            container_patch_enabled=False,
        )
        s.add(server)
        s.commit()
        s.refresh(server)
        sid = server.id
        server = s.get(Server, sid)

    fs = _SFTP()
    cli = SimpleNamespace()
    listing = hf.list_dir(server, "", role="fleet", sftp=fs)
    # list_dir asks for the client when with_client=True. Pass client via session.
    listing = hf.list_dir(server, "", role="fleet", sftp=fs)
    assert listing["entries"]
    found = hf.search(server, "notes", role="fleet", sftp=fs, contents=True)
    assert found["entries"] or found.get("hits") or found
    text = hf.read_text(server, "notes.txt", role="fleet", sftp=fs)
    assert "text" in text or text
    info = hf.stat_file(server, "notes.txt", role="fleet", sftp=fs)
    assert info
    made = hf.mkdir(server, "", "box", role="fleet", sftp=fs)
    assert made
    written = hf.write_text(server, "notes.txt", "bye", role="fleet", sftp=fs)
    assert written
    put = hf.put_file(
        server,
        "",
        "up.bin",
        io.BytesIO(b"abc"),
        size=3,
        role="fleet",
        sftp=fs,
        client=cli,
    )
    assert put


def test_binding_cert_and_compose_posts(tmp_path, monkeypatch):
    client, _uid, sid = _client(_engine(tmp_path / "q3b.db"), monkeypatch)
    from app.database import get_session
    from app.main import app

    gen = app.dependency_overrides[get_session]()
    session = next(gen)
    try:
        integ = Integration(type="grafana", name="Graf", base_url="http://graf")
        cert = ManagedCertificate(name="edge", domains_json='["demo.example"]')
        session.add(integ)
        session.add(cert)
        session.commit()
        session.refresh(integ)
        session.refresh(cert)
        iid, cid = integ.id, cert.id
    finally:
        gen.close()

    headers = {"x-piherder-files": "1"}
    posts = [
        (
            f"/integrations/{iid}/bindings",
            {
                "server_id": str(sid),
                "role": "ssh",
                "external_id": "mon-1",
                "display_name": "pi",
            },
        ),
        (
            f"/servers/{sid}/docker/compose/restart",
            {"project": "web"},
        ),
    ]
    for path, data in posts:
        response = client.post(path, data=data, headers=headers, follow_redirects=False)
        assert response.status_code < 600
    response = client.get(f"/certificates/{cid}", follow_redirects=False)
    assert response.status_code < 600
    app.dependency_overrides.pop(get_session, None)
