"""Nginx Proxy Manager adapter — token auth, proxy hosts (RO), certificates.

Auth: POST /api/tokens {"identity": email, "secret": password}
      → Bearer JWT

Observed routes (unofficial public API):
  GET  /api/nginx/proxy-hosts
  GET  /api/nginx/certificates
  GET  /api/nginx/certificates/{id}/download  (zip)
  POST /api/nginx/certificates/{id}/renew
"""
from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
DOWNLOAD_TIMEOUT = 120.0


@dataclass
class NpmPollResult:
    ok: bool
    error: Optional[str] = None
    proxy_hosts: list[dict[str, Any]] = field(default_factory=list)
    certificates: list[dict[str, Any]] = field(default_factory=list)
    version: str = ""

    def to_status_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error": self.error,
            "proxy_host_count": len(self.proxy_hosts),
            "certificate_count": len(self.certificates),
            "proxy_hosts": self.proxy_hosts,
            "certificates": self.certificates,
            "version": self.version,
            "monitor_count": len(self.proxy_hosts),
        }


def normalize_base_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        raise ValueError("Base URL is required")
    for suffix in ("/api", "/login", "/nginx/proxy"):
        if u.endswith(suffix):
            u = u[: -len(suffix)]
            break
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Base URL must be http(s)://host[:port]")
    return f"{parsed.scheme}://{parsed.netloc}"


def open_npm_url(base_url: str, path: str = "") -> str:
    base = normalize_base_url(base_url) if base_url else ""
    p = (path or "").strip()
    if not p:
        return base
    if p.startswith("http://") or p.startswith("https://"):
        return p
    if not p.startswith("/"):
        p = "/" + p
    return f"{base}{p}"


def get_token(
    base_url: str,
    identity: str,
    password: str,
    *,
    tls_verify: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
    expiry: str | None = None,
) -> str:
    """Login to NPM and return a Bearer token.

    Body must be exactly ``{identity, secret}`` — many NPM versions reject
    extra fields (e.g. ``expiry``) with HTTP 400 "additional properties".
    Optional long-lived token: GET /api/tokens?expiry=… after login (not used by default).
    """
    origin = normalize_base_url(base_url)
    ident = (identity or "").strip()
    secret = password or ""
    if not ident or not secret:
        raise ValueError("NPM identity and password are required")
    url = f"{origin}/api/tokens"
    # Strict schema: only identity + secret (email + password).
    body: dict[str, Any] = {"identity": ident, "secret": secret}
    with httpx.Client(verify=tls_verify, timeout=timeout) as client:
        r = client.post(url, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"NPM login failed HTTP {r.status_code}: {r.text[:200]}")
        data = r.json() if r.content else {}
        token = _extract_token(data)
        # Optional: exchange for longer-lived token (query param, not body).
        if token and expiry:
            try:
                r2 = client.get(
                    f"{origin}/api/tokens",
                    params={"expiry": expiry},
                    headers=_auth_headers(token),
                )
                if r2.status_code < 400 and r2.content:
                    token2 = _extract_token(r2.json())
                    if token2:
                        token = token2
            except Exception:
                pass
    if not token:
        raise RuntimeError("NPM login response missing token")
    return token


def _extract_token(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    token = str(data.get("token") or data.get("access_token") or "").strip()
    if not token and isinstance(data.get("result"), dict):
        token = str(data["result"].get("token") or "").strip()
    return token


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


def _get_json(
    base_url: str,
    token: str,
    path: str,
    *,
    tls_verify: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    origin = normalize_base_url(base_url)
    url = f"{origin}{path if path.startswith('/') else '/' + path}"
    with httpx.Client(verify=tls_verify, timeout=timeout) as client:
        r = client.get(url, headers=_auth_headers(token))
        if r.status_code >= 400:
            raise RuntimeError(f"NPM GET {path} HTTP {r.status_code}: {r.text[:200]}")
        if not r.content:
            return []
        return r.json()


def list_proxy_hosts(
    base_url: str,
    token: str,
    *,
    tls_verify: bool = True,
) -> list[dict[str, Any]]:
    data = _get_json(base_url, token, "/api/nginx/proxy-hosts", tls_verify=tls_verify)
    rows = data if isinstance(data, list) else (data.get("data") if isinstance(data, dict) else [])
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        domains = row.get("domain_names") or row.get("domains") or []
        if isinstance(domains, str):
            domains = [domains]
        out.append(
            {
                "id": str(row.get("id") or ""),
                "domain_names": list(domains),
                "forward_host": str(row.get("forward_host") or ""),
                "forward_port": row.get("forward_port"),
                "forward_scheme": str(row.get("forward_scheme") or "http"),
                "enabled": bool(row.get("enabled", True)),
                "certificate_id": row.get("certificate_id"),
                "ssl_forced": bool(row.get("ssl_forced") or False),
                "meta": row.get("meta") if isinstance(row.get("meta"), dict) else {},
                "label": ", ".join(str(d) for d in domains) or f"host-{row.get('id')}",
            }
        )
    return out


def list_certificates(
    base_url: str,
    token: str,
    *,
    tls_verify: bool = True,
) -> list[dict[str, Any]]:
    data = _get_json(base_url, token, "/api/nginx/certificates", tls_verify=tls_verify)
    rows = data if isinstance(data, list) else (data.get("data") if isinstance(data, dict) else [])
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        domains = row.get("domain_names") or []
        if isinstance(domains, str):
            domains = [domains]
        out.append(
            {
                "id": str(row.get("id") or ""),
                "nice_name": str(row.get("nice_name") or row.get("name") or ""),
                "provider": str(row.get("provider") or ""),
                "domain_names": list(domains),
                "expires_on": row.get("expires_on") or row.get("meta", {}).get("expires_on")
                if isinstance(row.get("meta"), dict)
                else row.get("expires_on"),
                "meta": row.get("meta") if isinstance(row.get("meta"), dict) else {},
            }
        )
    return out


def download_certificate_zip(
    base_url: str,
    token: str,
    cert_id: str | int,
    *,
    tls_verify: bool = True,
) -> bytes:
    origin = normalize_base_url(base_url)
    cid = str(cert_id).strip()
    url = f"{origin}/api/nginx/certificates/{cid}/download"
    with httpx.Client(verify=tls_verify, timeout=DOWNLOAD_TIMEOUT) as client:
        r = client.get(url, headers=_auth_headers(token))
        if r.status_code >= 400:
            raise RuntimeError(
                f"NPM cert download failed HTTP {r.status_code}: {r.text[:200]}"
            )
        return r.content


def renew_certificate(
    base_url: str,
    token: str,
    cert_id: str | int,
    *,
    tls_verify: bool = True,
) -> dict[str, Any]:
    origin = normalize_base_url(base_url)
    cid = str(cert_id).strip()
    url = f"{origin}/api/nginx/certificates/{cid}/renew"
    with httpx.Client(verify=tls_verify, timeout=DOWNLOAD_TIMEOUT) as client:
        r = client.post(url, headers=_auth_headers(token))
        if r.status_code >= 400:
            raise RuntimeError(
                f"NPM cert renew failed HTTP {r.status_code}: {r.text[:300]}"
            )
        if not r.content:
            return {"ok": True}
        try:
            data = r.json()
            return data if isinstance(data, dict) else {"ok": True, "raw": data}
        except Exception:
            return {"ok": True, "text": r.text[:500]}


def parse_certificate_zip(blob: bytes) -> dict[str, str]:
    """Extract fullchain + privkey PEM from NPM download zip.

    Returns {"fullchain": pem, "privkey": pem, "cert": optional, "chain": optional}.
    """
    if not blob:
        raise ValueError("Empty certificate zip")
    # Allow raw PEM passed by mistake
    if blob.lstrip().startswith(b"-----BEGIN"):
        text = blob.decode("utf-8", errors="replace")
        return {"fullchain": text, "privkey": ""}

    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as e:
        raise ValueError(f"Not a valid certificate zip: {e}") from e

    names = zf.namelist()
    by_lower = {n.lower().split("/")[-1]: n for n in names}

    def _find(*prefixes: str) -> Optional[str]:
        for pref in prefixes:
            for low, orig in by_lower.items():
                if low.startswith(pref) and low.endswith((".pem", ".crt", ".key")):
                    return orig
                if low == pref:
                    return orig
        return None

    full_name = _find("fullchain")
    key_name = _find("privkey", "private", "key")
    cert_name = _find("cert")
    chain_name = _find("chain")

    def _read(name: Optional[str]) -> str:
        if not name:
            return ""
        return zf.read(name).decode("utf-8", errors="replace")

    fullchain = _read(full_name)
    privkey = _read(key_name)
    cert = _read(cert_name)
    chain = _read(chain_name)

    if not fullchain and cert:
        fullchain = cert + ("\n" + chain if chain else "")
    if not fullchain:
        # last resort: any .pem that is not the key
        for n in names:
            low = n.lower()
            if "priv" in low or "key" in low:
                continue
            if low.endswith(".pem") or low.endswith(".crt"):
                fullchain = zf.read(n).decode("utf-8", errors="replace")
                break
    if not privkey:
        for n in names:
            low = n.lower()
            if "key" in low or "priv" in low:
                privkey = zf.read(n).decode("utf-8", errors="replace")
                break

    if not fullchain:
        raise ValueError(
            f"Certificate zip missing fullchain (files: {', '.join(names) or 'none'})"
        )
    if not privkey:
        raise ValueError(
            f"Certificate zip missing private key (files: {', '.join(names) or 'none'})"
        )
    return {
        "fullchain": fullchain.strip() + "\n",
        "privkey": privkey.strip() + "\n",
        "cert": (cert.strip() + "\n") if cert else "",
        "chain": (chain.strip() + "\n") if chain else "",
    }


def poll(
    base_url: str,
    identity: str,
    password: str,
    *,
    tls_verify: bool = True,
) -> NpmPollResult:
    try:
        token = get_token(base_url, identity, password, tls_verify=tls_verify)
    except Exception as e:
        return NpmPollResult(ok=False, error=str(e)[:300])
    try:
        hosts = list_proxy_hosts(base_url, token, tls_verify=tls_verify)
        certs = list_certificates(base_url, token, tls_verify=tls_verify)
        return NpmPollResult(ok=True, proxy_hosts=hosts, certificates=certs)
    except Exception as e:
        return NpmPollResult(ok=False, error=str(e)[:300])
