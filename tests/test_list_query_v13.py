"""v1.3 slice 3 Deep — shared list chrome (pure)."""
from __future__ import annotations

from types import SimpleNamespace

from app.services import list_query as lq


def test_clamp_per_page_choices_and_nearest():
    assert lq.clamp_per_page(None) == 20
    assert lq.clamp_per_page("") == 20
    assert lq.clamp_per_page(10) == 10
    assert lq.clamp_per_page(100) == 100
    assert lq.clamp_per_page(12) == 10
    assert lq.clamp_per_page(80) == 100
    assert lq.clamp_per_page("nope") == 20


def test_clamp_prefers_query_over_cookie():
    assert lq.clamp_per_page(50, cookie="10") == 50
    assert lq.clamp_per_page(None, cookie="50") == 50
    assert lq.clamp_per_page("", cookie="100") == 100
    assert lq.clamp_per_page(None, cookie="nope") == 20


def test_per_page_from_request():
    req = SimpleNamespace(query_params={}, cookies={lq.COOKIE: "50"})
    assert lq.per_page_from_request(req) == 50
    req.query_params = {"per_page": "10"}
    assert lq.per_page_from_request(req) == 10


def test_parse_page():
    assert lq.parse_page(None) == 1
    assert lq.parse_page("3") == 3
    assert lq.parse_page(0) == 1
    assert lq.parse_page("x") == 1


def test_tokens_and_aliases():
    assert lq.tokens("  HA  pi-hole ") == ["ha", "pi-hole"]
    assert "homeassistant" in lq.expand("ha")
    assert "pihole" in lq.expand("pi-hole")
    assert "nginx" in lq.expand("npm")
    assert "raspberry" in lq.expand("rpi")


def test_matches_and_short_token_boundary():
    assert lq.matches("", "chase")
    assert lq.matches("ha", "homeassistant")
    assert not lq.matches("ha", "chase")
    assert lq.matches("pi-hole", "Pi-hole DNS")
    assert lq.matches("npm", "nginxproxymanager")
    assert lq.matches("lab core", "lab-core", "192.168.1.5")
    assert not lq.matches("lab core", "lab-edge")


def test_page_slice_edges():
    rows = list(range(25))
    page_rows, total, pages, page = lq.page_slice(rows, 2, 10)
    assert page_rows == list(range(10, 20))
    assert (total, pages, page) == (25, 3, 2)
    page_rows, total, pages, page = lq.page_slice(rows, 99, 10)
    assert page == 3
    assert page_rows == list(range(20, 25))
    page_rows, total, pages, page = lq.page_slice([], 3, 20)
    assert (page_rows, total, pages, page) == ([], 0, 1, 1)


def test_match_server_fields():
    row = SimpleNamespace(
        name="Hass",
        hostname="ha.local",
        ip_address="10.0.0.2",
        dns_name="home.lan",
        ssh_username="piherder",
    )
    assert lq.match_server(row, "ha")
    assert lq.match_server(row, "10.0.0.2")
    assert not lq.match_server(row, "missing")


def test_filter_docker_stack_alias_status_and_page():
    projects = [
        {
            "name": "core",
            "path": "/home/pi/docker/core",
            "has_pending_update": False,
            "containers": [
                {
                    "name": "homeassistant",
                    "compose_service": "homeassistant",
                    "image": "ghcr.io/home-assistant/home-assistant",
                    "running": True,
                }
            ],
        },
        {
            "name": "edge",
            "path": "/home/pi/docker/edge",
            "has_pending_update": True,
            "containers": [
                {
                    "name": "npm",
                    "compose_service": "app",
                    "image": "jc21/nginx-proxy-manager",
                    "running": False,
                    "has_pending_update": True,
                }
            ],
        },
        *[
            {
                "name": f"stack-{i}",
                "containers": [{"name": f"c{i}", "running": True}],
            }
            for i in range(8)
        ],
    ]
    out = lq.filter_docker_stack(projects, [], q="ha", status="all", page=1, per_page=20)
    assert [p["name"] for p in out["projects"]] == ["core"]
    assert out["total"] == 1

    upd = lq.filter_docker_stack(projects, [], q="", status="updates", page=1, per_page=20)
    assert [p["name"] for p in upd["projects"]] == ["edge"]

    paged = lq.filter_docker_stack(projects, [], q="", status="all", page=2, per_page=4)
    assert paged["page"] == 2
    assert paged["total"] == 10
    assert paged["total_pages"] == 3
    assert len(paged["projects"]) == 4

    forced = lq.filter_docker_stack(
        projects, [], q="nope", status="running", page=1, per_page=20, force_project="edge"
    )
    assert forced["forced_project"] is True
    assert forced["projects"][0]["name"] == "edge"


def test_filter_docker_orphans_last_page():
    projects = [{"name": "a", "containers": [{"name": "x", "running": True}]}]
    orphans = [{"name": "lonely", "running": False}]
    out = lq.filter_docker_stack(projects, orphans, q="", status="all", page=1, per_page=20)
    assert len(out["orphan_containers"]) == 1
    hidden = lq.filter_docker_stack(
        projects, orphans, q="zzz", status="all", page=1, per_page=20
    )
    assert hidden["orphan_containers"] == []


def test_api_limit_and_query_string():
    assert lq.clamp_api_limit(None) == 100
    assert lq.clamp_api_limit(0) == 100
    assert lq.clamp_api_limit(5) == 5
    assert lq.clamp_api_limit(999) == 100
    assert lq.parse_offset("-3") == 0
    qs = lq.query_string({"q": "ha", "filter": "all", "fav": True, "page": 2}, omit=("page",))
    assert "q=ha" in qs
    assert "filter" not in qs
    assert "fav=1" in qs
    assert "page" not in qs
