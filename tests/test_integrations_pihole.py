"""Pi-hole adapter unit tests (no live Pi-hole)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.integrations import pihole as ph
from app.services.integrations import registry as reg


def test_normalize_base_url_strips_admin():
    assert (
        ph.normalize_base_url("https://pihole.example.com/admin/")
        == "https://pihole.example.com"
    )
    assert (
        ph.normalize_base_url("https://pihole.example.com/admin/gravity")
        == "https://pihole.example.com"
    )
    with pytest.raises(ValueError):
        ph.normalize_base_url("not-a-url")


def test_admin_url():
    assert ph.admin_url("https://pihole.example.com", "/gravity") == (
        "https://pihole.example.com/admin/gravity"
    )
    assert ph.admin_url("https://pihole.example.com").endswith("/admin/")


def test_parse_stats_payload_summary_shape():
    data = {
        "queries": {"total": 181018, "blocked": 20189, "percent_blocked": 11.2},
        "gravity": {"domains_being_blocked": 1332820},
        "clients": {"active": 10},
    }
    st = ph.parse_stats_payload(data)
    assert st.ok
    assert st.queries == 181018
    assert st.blocked == 20189
    assert st.percent_blocked == 11.2
    assert st.domains_on_lists == 1332820
    assert st.active_clients == 10


def test_parse_stats_payload_legacy_keys():
    data = {
        "dns_queries_today": 100,
        "ads_blocked_today": 25,
        "domains_being_blocked": 50,
        "unique_clients": 3,
    }
    st = ph.parse_stats_payload(data)
    assert st.queries == 100
    assert st.blocked == 25
    assert st.percent_blocked == 25.0


def test_encode_host_path():
    enc = ph.encode_host_path("10.0.0.1", "host.local")
    assert "10.0.0.1" in enc or "%20" in enc
    assert "host" in enc


def test_parse_host_entries():
    data = {"config": {"dns": {"hosts": ["10.0.0.1 foo.local", "10.0.0.2 bar.local"]}}}
    rows = ph._parse_host_entries(data)
    assert len(rows) == 2
    assert rows[0]["ip"] == "10.0.0.1"
    assert rows[0]["domain"] == "foo.local"


def test_parse_cname_entries():
    data = {"config": {"dns": {"cnameRecords": ["alias.local,target.local"]}}}
    rows = ph._parse_cname_entries(data)
    assert len(rows) == 1
    assert rows[0]["domain"] == "alias.local"
    assert rows[0]["target"] == "target.local"


def test_summarize_instances():
    s = ph.summarize_instances(
        [
            {
                "ok": True,
                "queries": 100,
                "blocked": 10,
                "active_clients": 2,
                "domains_on_lists": 1000,
                "is_primary": True,
            },
            {
                "ok": True,
                "queries": 50,
                "blocked": 5,
                "active_clients": 1,
                "domains_on_lists": 999,
                "is_primary": False,
            },
        ]
    )
    assert s["queries"] == 150
    assert s["blocked"] == 15
    assert s["percent_blocked"] == 10.0
    assert s["domains_on_lists"] == 1000
    assert s["active_clients"] == 3
    assert s["instance_count"] == 2


def test_is_pihole_primary_config():
    integ = MagicMock()
    integ.config_json = json.dumps({"is_primary": True})
    assert reg.is_pihole_primary(integ) is True
    integ.config_json = json.dumps({"is_primary": False})
    assert reg.is_pihole_primary(integ) is False
