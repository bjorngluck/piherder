"""Admin create, edit, and delete of an NPM proxy host. Move still only retargets."""
from __future__ import annotations

import pytest

from app.security.auth import _admin_only_path
from app.services.integrations import npm


class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload
        self.content = b"{}"
        self.text = ""

    def json(self):
        return self._payload


class _Client:
    def __init__(self, handler):
        self.handler = handler

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, headers=None):
        return self.handler("GET", url)

    def post(self, url, headers=None, json=None):
        return self.handler("POST", url, json)

    def put(self, url, headers=None, json=None):
        return self.handler("PUT", url, json)

    def delete(self, url, headers=None):
        return self.handler("DELETE", url, None)


def test_parse_rejects_a_domain_with_a_scheme():
    with pytest.raises(ValueError):
        npm.parse_proxy_host_fields(
            domain_names="https://app.example.com",
            forward_host="192.0.2.10",
            forward_port=80,
            forward_scheme="http",
        )


def test_create_does_not_request_a_certificate(monkeypatch):
    seen = {}

    def handler(method, url, body=None):
        seen["method"] = method
        seen["url"] = url
        seen["body"] = body
        return _Resp({"id": 12})

    monkeypatch.setattr(npm.httpx, "Client", lambda *a, **k: _Client(handler))
    fields = npm.parse_proxy_host_fields(
        domain_names="app.example.com, www.example.com",
        forward_host="192.0.2.10",
        forward_port="8080",
        forward_scheme="http",
        enabled=True,
        certificate_id="",
        ssl_forced=True,
    )
    result = npm.create_proxy_host("http://npm.example", "tok", fields)
    assert result["id"] == "12"
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/api/nginx/proxy-hosts")
    assert seen["body"]["domain_names"] == ["app.example.com", "www.example.com"]
    assert seen["body"]["certificate_id"] == 0
    assert seen["body"]["ssl_forced"] is False
    assert seen["body"]["meta"]["letsencrypt_agree"] is False


def test_update_keeps_advanced_config(monkeypatch):
    seen = {}
    stored = {
        "id": 9,
        "domain_names": ["old.example.com"],
        "forward_host": "192.0.2.4",
        "forward_port": 80,
        "forward_scheme": "http",
        "enabled": True,
        "certificate_id": 3,
        "ssl_forced": True,
        "advanced_config": "proxy_set_header X-Test 1;",
        "locations": [{"path": "/x"}],
    }

    def handler(method, url, body=None):
        if method == "GET":
            return _Resp(stored)
        seen["body"] = body
        return _Resp({"ok": True})

    monkeypatch.setattr(npm.httpx, "Client", lambda *a, **k: _Client(handler))
    fields = npm.parse_proxy_host_fields(
        domain_names="app.example.com",
        forward_host="192.0.2.10",
        forward_port=443,
        forward_scheme="https",
        enabled=False,
        certificate_id=3,
        ssl_forced=True,
    )
    result = npm.update_proxy_host("http://npm.example", "tok", 9, fields)
    assert result["forward_host"] == "192.0.2.10"
    assert seen["body"]["advanced_config"].startswith("proxy_set_header")
    assert seen["body"]["locations"] == [{"path": "/x"}]
    assert seen["body"]["enabled"] is False
    assert seen["body"]["ssl_forced"] is True


def test_delete_calls_delete(monkeypatch):
    seen = {}

    def handler(method, url, body=None):
        seen["method"] = method
        seen["url"] = url
        return _Resp({})

    monkeypatch.setattr(npm.httpx, "Client", lambda *a, **k: _Client(handler))
    assert npm.delete_proxy_host("http://npm.example", "tok", "9") == "9"
    assert seen["method"] == "DELETE"
    assert seen["url"].endswith("/api/nginx/proxy-hosts/9")


def test_proxy_host_writes_are_admin_only():
    assert _admin_only_path("/integrations/4/npm/proxy-hosts")
    assert _admin_only_path("/integrations/4/npm/proxy-hosts/9")
    assert _admin_only_path("/integrations/4/npm/proxy-hosts/9/delete")
    assert not _admin_only_path("/integrations/4/npm/bind")
