"""Unit tests for nmap device type heuristics (ports + MAC vendor/OUI)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.services.nmap import device_classify as dc
from app.services.nmap import parse as np

FIXTURE = Path(__file__).parent / "fixtures" / "nmap_sample.xml"


def test_parse_mac_vendor_from_xml():
    hosts = np.parse_nmap_xml(FIXTURE.read_text(encoding="utf-8"))
    up = next(h for h in hosts if h.ip_address == "192.168.1.10")
    assert up.mac_address == "AA:BB:CC:DD:EE:FF"
    assert up.mac_vendor == "Test"


def test_classify_raspberry_pi_oui_and_hostname():
    # B8:27:EB is classic Pi OUI
    p = dc.classify_device(
        mac="B8:27:EB:12:34:56",
        hostname="pi-lab",
        ports_json=json.dumps(
            [{"port": 22, "protocol": "tcp", "state": "open", "service": "ssh"}]
        ),
    )
    assert p.kind == dc.KIND_RASPBERRY_PI
    assert p.confidence in (dc.CONF_HIGH, dc.CONF_MEDIUM)
    assert any("OUI" in r or "Pi" in r for r in p.reasons)


def test_classify_printer_ports_and_vendor():
    p = dc.classify_device(
        mac="00:80:77:AA:BB:CC",
        mac_vendor="Brother Industries, LTD",
        ports_json=json.dumps(
            [
                {"port": 9100, "protocol": "tcp", "state": "open", "service": "jetdirect"},
                {"port": 631, "protocol": "tcp", "state": "open", "service": "ipp"},
            ]
        ),
    )
    assert p.kind == dc.KIND_PRINTER
    assert p.confidence in (dc.CONF_HIGH, dc.CONF_MEDIUM)
    assert p.vendor and "Brother" in p.vendor


def test_classify_nas_synology_ports():
    p = dc.classify_device(
        mac_vendor="Synology Incorporated",
        ports_json=json.dumps(
            [
                {"port": 5000, "protocol": "tcp", "state": "open", "service": "http"},
                {"port": 5001, "protocol": "tcp", "state": "open", "service": "https"},
            ]
        ),
    )
    assert p.kind == dc.KIND_NAS


def test_classify_camera_rtsp():
    p = dc.classify_device(
        hostname="livingroom-cam",
        ports_json=json.dumps(
            [{"port": 554, "protocol": "tcp", "state": "open", "service": "rtsp"}]
        ),
    )
    assert p.kind == dc.KIND_CAMERA


def test_classify_windows_rdp_smb():
    p = dc.classify_device(
        ports_json=json.dumps(
            [
                {"port": 445, "protocol": "tcp", "state": "open", "service": "microsoft-ds"},
                {"port": 3389, "protocol": "tcp", "state": "open", "service": "ms-wbt-server"},
            ]
        ),
    )
    assert p.kind == dc.KIND_WINDOWS


def test_classify_unknown_no_signals():
    p = dc.classify_device()
    assert p.kind == dc.KIND_UNKNOWN
    assert p.confidence == dc.CONF_LOW


def test_classify_from_fixture_host():
    """Sample host: hostname pi-lab + ssh/http → Pi or server, not unknown."""
    hosts = np.parse_nmap_xml(FIXTURE.read_text(encoding="utf-8"))
    up = next(h for h in hosts if h.ip_address == "192.168.1.10")
    ports = [
        {
            "port": p.port,
            "protocol": p.protocol,
            "state": p.state,
            "service": p.service,
            "product": p.product,
        }
        for p in up.ports
    ]
    p = dc.classify_device(
        mac=up.mac_address,
        mac_vendor=up.mac_vendor,
        hostname=up.hostname,
        os_summary=up.os_summary,
        ports=ports,
    )
    # pi-lab hostname is strong Pi signal
    assert p.kind == dc.KIND_RASPBERRY_PI


def test_profile_from_device_object():
    dev = SimpleNamespace(
        mac_address="DC:A6:32:00:11:22",
        mac_vendor="Raspberry Pi Trading Ltd",
        hostname=None,
        os_summary=None,
        ports_json='[{"port":22,"state":"open","service":"ssh"}]',
    )
    p = dc.profile_from_device(dev)
    assert p.kind == dc.KIND_RASPBERRY_PI
    d = dc.profile_dict_from_device(dev)
    assert d["kind"] == dc.KIND_RASPBERRY_PI
    assert "label" in d and "reasons" in d


def test_oui_prefix():
    assert dc.oui_prefix("b8:27:eb:01:02:03") == "B827EB"
    assert dc.oui_prefix("invalid") == ""
    assert dc.oui_prefix(None) == ""


def test_device_display_name_prefers_operator_name():
    from app.services.nmap import config as nmap_cfg

    d = SimpleNamespace(
        display_name="cctv1",
        hostname="cam-abc.local",
        ip_address="10.0.0.50",
    )
    assert nmap_cfg.device_display_name(d) == "cctv1"
    d.display_name = None
    assert nmap_cfg.device_display_name(d) == "cam-abc.local"
    d.hostname = None
    assert nmap_cfg.device_display_name(d) == "10.0.0.50"


def test_set_device_display_name():
    from unittest.mock import MagicMock

    from app.services.nmap import config as nmap_cfg

    dev = SimpleNamespace(
        display_name=None,
        updated_at=None,
    )
    session = MagicMock()
    # set_device_display_name commits; mock refresh
    out = nmap_cfg.set_device_display_name(session, dev, "  cctv1  ")
    assert out.display_name == "cctv1"
    session.add.assert_called()
    session.commit.assert_called()
    out2 = nmap_cfg.set_device_display_name(session, dev, "   ")
    assert out2.display_name is None


def test_discovery_hosts_for_fabric_uses_display_name():
    from unittest.mock import MagicMock

    from app.services.nmap import config as nmap_cfg

    class Dev:
        def __init__(self):
            self.id = 9
            self.integration_id = 1
            self.ip_address = "10.0.0.50"
            self.hostname = None
            self.display_name = "cctv1"
            self.kind_override = None
            self.map_role = None
            self.mac_address = None
            self.mac_vendor = None
            self.state = "new"
            self.linked_server_id = None
            self.os_summary = None
            self.ports_json = None

    class FakeResult:
        def all(self):
            return [Dev()]

    session = MagicMock()
    session.exec = lambda q: FakeResult()
    out = nmap_cfg.discovery_hosts_for_fabric(session, fleet_ips=set(), fleet_server_ids=set())
    assert len(out) == 1
    assert out[0]["name"] == "cctv1"
    assert out[0]["display_name"] == "cctv1"


def test_kind_override_printer_to_pi():
    """Operator can fix busted classification (printer OUI → Raspberry Pi)."""
    auto = dc.classify_device(
        mac="00:80:77:AA:BB:CC",
        mac_vendor="Brother Industries, LTD",
        ports_json=json.dumps(
            [
                {"port": 9100, "protocol": "tcp", "state": "open", "service": "jetdirect"},
            ]
        ),
    )
    assert auto.kind == dc.KIND_PRINTER

    overridden = dc.apply_kind_override(auto, "raspberry_pi")
    assert overridden.kind == dc.KIND_RASPBERRY_PI
    assert overridden.overridden is True
    assert overridden.auto_kind == dc.KIND_PRINTER
    assert overridden.confidence == dc.CONF_HIGH
    assert any("override" in r.lower() for r in overridden.reasons)

    dev = SimpleNamespace(
        mac_address="00:80:77:AA:BB:CC",
        mac_vendor="Brother Industries, LTD",
        hostname=None,
        os_summary=None,
        ports_json=json.dumps(
            [{"port": 9100, "protocol": "tcp", "state": "open", "service": "jetdirect"}]
        ),
        kind_override="raspberry_pi",
    )
    p = dc.profile_from_device(dev)
    assert p.kind == dc.KIND_RASPBERRY_PI
    assert p.overridden is True


def test_normalize_kind_and_map_role():
    assert dc.normalize_kind_override("") is None
    assert dc.normalize_kind_override("auto") is None
    assert dc.normalize_kind_override("pi") == dc.KIND_RASPBERRY_PI
    assert dc.normalize_kind_override("printer") == dc.KIND_PRINTER
    assert dc.normalize_kind_override("not-a-kind") is None
    assert dc.normalize_map_role("") is None
    assert dc.normalize_map_role("router") == dc.MAP_ROLE_GATEWAY
    assert dc.normalize_map_role("gateway") == dc.MAP_ROLE_GATEWAY
    assert dc.normalize_map_role("host") is None


def test_discovery_hosts_skip_gateway_role_and_ip():
    from unittest.mock import MagicMock

    from app.services.nmap import config as nmap_cfg

    class Dev:
        def __init__(self, ip, role=None, name=None):
            self.id = hash(ip) % 10000
            self.integration_id = 1
            self.ip_address = ip
            self.hostname = None
            self.display_name = name
            self.kind_override = None
            self.map_role = role
            self.mac_address = None
            self.mac_vendor = None
            self.state = "new"
            self.linked_server_id = None
            self.os_summary = None
            self.ports_json = None

    rows = [
        Dev("10.0.0.1", role="gateway", name="udm"),
        Dev("10.0.0.50", name="cctv1"),
        Dev("10.0.0.1"),  # same gw ip without role — still skipped via gateway_ip
    ]

    class FakeResult:
        def all(self):
            return rows

    session = MagicMock()
    session.exec = lambda q: FakeResult()
    out = nmap_cfg.discovery_hosts_for_fabric(
        session,
        fleet_ips=set(),
        fleet_server_ids=set(),
        gateway_ip="10.0.0.1",
    )
    assert len(out) == 1
    assert out[0]["ip"] == "10.0.0.50"


def test_set_device_map_identity_kind_and_role():
    from unittest.mock import MagicMock, patch

    from app.services.nmap import config as nmap_cfg

    dev = SimpleNamespace(
        id=3,
        ip_address="192.168.1.1",
        display_name=None,
        kind_override=None,
        map_role=None,
        state="new",
        updated_at=None,
    )
    session = MagicMock()
    session.exec = MagicMock(return_value=MagicMock(all=lambda: []))
    with patch.object(nmap_cfg, "save_settings", create=True):
        # save_settings is imported inside; mock app_settings
        with patch("app.services.app_settings.load_settings", return_value={"network_gateway_ip": ""}):
            with patch("app.services.app_settings.save_settings") as save:
                out = nmap_cfg.set_device_map_identity(
                    session,
                    dev,
                    display_name="udm-pro",
                    kind_override="router",
                    map_role="gateway",
                    sync_network_gateway=True,
                )
                assert out.display_name == "udm-pro"
                assert out.kind_override == dc.KIND_ROUTER
                assert out.map_role == dc.MAP_ROLE_GATEWAY
                assert out.state == "known"  # map save reviews new → known
                save.assert_called()
                assert save.call_args[0][0]["network_gateway_ip"] == "192.168.1.1"


def test_mark_device_known_and_new():
    from unittest.mock import MagicMock

    from app.services.nmap import config as nmap_cfg

    session = MagicMock()
    dev = SimpleNamespace(
        state="new",
        linked_server_id=None,
        updated_at=None,
    )
    out = nmap_cfg.mark_device_known(session, dev)
    assert out.state == "known"
    out2 = nmap_cfg.mark_device_new(session, out)
    assert out2.state == "new"
    linked = SimpleNamespace(state="linked", linked_server_id=5, updated_at=None)
    assert nmap_cfg.mark_device_known(session, linked).state == "linked"
    try:
        nmap_cfg.mark_device_new(session, linked)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_discovery_hosts_for_fabric_dedupes_fleet():
    from unittest.mock import MagicMock

    from app.services.nmap import config as nmap_cfg

    class Dev:
        def __init__(self, **kw):
            self.id = kw.get("id")
            self.integration_id = kw.get("integration_id", 1)
            self.ip_address = kw["ip"]
            self.hostname = kw.get("hostname")
            self.mac_address = kw.get("mac")
            self.mac_vendor = kw.get("vendor")
            self.state = kw.get("state", "new")
            self.linked_server_id = kw.get("linked_server_id")
            self.os_summary = None
            self.ports_json = kw.get("ports_json")

    devices = [
        Dev(id=1, ip="10.0.0.1", hostname="fleet-ip-match"),
        Dev(id=2, ip="10.0.0.2", linked_server_id=7),
        Dev(id=3, ip="10.0.0.50", hostname="cam-1", state="new"),
        Dev(id=4, ip="10.0.0.51", state="ignored"),
    ]

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    session = MagicMock()
    session.exec = lambda q: FakeResult([d for d in devices if d.state != "ignored"])
    out = nmap_cfg.discovery_hosts_for_fabric(
        session,
        fleet_ips={"10.0.0.1"},
        fleet_server_ids={7},
    )
    ips = {h["ip"] for h in out}
    assert "10.0.0.50" in ips
    assert "10.0.0.1" not in ips
    assert "10.0.0.2" not in ips
    assert all(h.get("is_discovered") for h in out)
    assert all(h.get("discovery_id") for h in out)
