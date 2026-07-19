"""Heuristic device type from MAC vendor / OUI, open ports, hostname, OS.

Advisory only — never auto-promotes or changes device state.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence

# --- kinds (stable ids for CSS / filters) ---
KIND_UNKNOWN = "unknown"
KIND_RASPBERRY_PI = "raspberry_pi"
KIND_SERVER = "server"
KIND_WINDOWS = "windows"
KIND_NAS = "nas"
KIND_PRINTER = "printer"
KIND_ROUTER = "router"
KIND_AP = "access_point"
KIND_PHONE = "phone"
KIND_TV = "tv"
KIND_CAMERA = "camera"
KIND_IOT = "iot"
KIND_MEDIA = "media"
KIND_NETWORK = "network"

KIND_LABELS: dict[str, str] = {
    KIND_UNKNOWN: "Unknown",
    KIND_RASPBERRY_PI: "Raspberry Pi",
    KIND_SERVER: "Server / host",
    KIND_WINDOWS: "Windows PC",
    KIND_NAS: "NAS",
    KIND_PRINTER: "Printer",
    KIND_ROUTER: "Router / gateway",
    KIND_AP: "Access point",
    KIND_PHONE: "Phone / tablet",
    KIND_TV: "Smart TV",
    KIND_CAMERA: "Camera / NVR",
    KIND_IOT: "IoT / smart device",
    KIND_MEDIA: "Media server",
    KIND_NETWORK: "Network gear",
}

# Short labels for dense UI (map cards)
KIND_SHORT: dict[str, str] = {
    KIND_UNKNOWN: "?",
    KIND_RASPBERRY_PI: "Pi",
    KIND_SERVER: "Host",
    KIND_WINDOWS: "Win",
    KIND_NAS: "NAS",
    KIND_PRINTER: "Print",
    KIND_ROUTER: "Router",
    KIND_AP: "AP",
    KIND_PHONE: "Phone",
    KIND_TV: "TV",
    KIND_CAMERA: "Cam",
    KIND_IOT: "IoT",
    KIND_MEDIA: "Media",
    KIND_NETWORK: "Net",
}

CONF_HIGH = "high"
CONF_MEDIUM = "medium"
CONF_LOW = "low"


@dataclass
class DeviceProfile:
    kind: str = KIND_UNKNOWN
    label: str = KIND_LABELS[KIND_UNKNOWN]
    short: str = KIND_SHORT[KIND_UNKNOWN]
    vendor: str = ""
    confidence: str = CONF_LOW
    reasons: list[str] = field(default_factory=list)
    score: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _norm_mac_digits(mac: Optional[str]) -> str:
    if not mac:
        return ""
    return re.sub(r"[^0-9A-Fa-f]", "", mac).upper()


def oui_prefix(mac: Optional[str]) -> str:
    """First 6 hex digits of MAC (OUI), or empty."""
    d = _norm_mac_digits(mac)
    return d[:6] if len(d) >= 6 else ""


# Curated home/lab OUI prefixes → (kind, weight, reason label)
# Not a full IEEE table — high-signal vendors only.
OUI_RULES: dict[str, tuple[str, int, str]] = {
    # Raspberry Pi
    "B827EB": (KIND_RASPBERRY_PI, 55, "Raspberry Pi OUI"),
    "DCA632": (KIND_RASPBERRY_PI, 55, "Raspberry Pi OUI"),
    "E45F01": (KIND_RASPBERRY_PI, 55, "Raspberry Pi OUI"),
    "28CD4C": (KIND_RASPBERRY_PI, 55, "Raspberry Pi OUI"),
    "2CFDA1": (KIND_RASPBERRY_PI, 55, "Raspberry Pi OUI"),
    "D83ADD": (KIND_RASPBERRY_PI, 55, "Raspberry Pi OUI"),
    # Espressif (ESP8266/ESP32 IoT)
    "240AC4": (KIND_IOT, 45, "Espressif OUI"),
    "30AEA4": (KIND_IOT, 45, "Espressif OUI"),
    "A020A6": (KIND_IOT, 45, "Espressif OUI"),
    "84F3EB": (KIND_IOT, 45, "Espressif OUI"),
    "CC50E3": (KIND_IOT, 45, "Espressif OUI"),
    "246F28": (KIND_IOT, 45, "Espressif OUI"),
    "3C71BF": (KIND_IOT, 45, "Espressif OUI"),
    # Ubiquiti
    "FCECDA": (KIND_NETWORK, 40, "Ubiquiti OUI"),
    "802AA8": (KIND_NETWORK, 40, "Ubiquiti OUI"),
    "24A43C": (KIND_NETWORK, 40, "Ubiquiti OUI"),
    "788A20": (KIND_NETWORK, 40, "Ubiquiti OUI"),
    "E063DA": (KIND_NETWORK, 40, "Ubiquiti OUI"),
    "B4FBE4": (KIND_NETWORK, 40, "Ubiquiti OUI"),
    # MikroTik
    "4C5E0C": (KIND_NETWORK, 40, "MikroTik OUI"),
    "6C3B6B": (KIND_NETWORK, 40, "MikroTik OUI"),
    "D4CA6D": (KIND_NETWORK, 40, "MikroTik OUI"),
    "48A98A": (KIND_NETWORK, 40, "MikroTik OUI"),
    # HP printers / enterprise
    "3C4A92": (KIND_PRINTER, 35, "HP OUI"),
    "A0D3C1": (KIND_PRINTER, 35, "HP OUI"),
    "9C8E99": (KIND_PRINTER, 35, "HP OUI"),
    # Brother
    "008077": (KIND_PRINTER, 40, "Brother OUI"),
    "001BA9": (KIND_PRINTER, 40, "Brother OUI"),
    "30055C": (KIND_PRINTER, 40, "Brother OUI"),
    # Canon
    "00BB3A": (KIND_PRINTER, 35, "Canon OUI"),
    "180675": (KIND_PRINTER, 35, "Canon OUI"),
    # Epson
    "001B38": (KIND_PRINTER, 35, "Epson OUI"),
    # Synology
    "001132": (KIND_NAS, 45, "Synology OUI"),
    "00C09F": (KIND_NAS, 45, "Synology OUI"),
    "0011D8": (KIND_NAS, 45, "Synology OUI"),
    # QNAP
    "00E04C": (KIND_NAS, 35, "QNAP-ish OUI"),  # Realtek often on QNAP NICs — lower weight
    # Amazon
    "0C47C9": (KIND_IOT, 40, "Amazon OUI"),
    "74C246": (KIND_IOT, 40, "Amazon OUI"),
    "44650D": (KIND_IOT, 40, "Amazon OUI"),
    "F0D2F1": (KIND_IOT, 40, "Amazon OUI"),
    "40B4CD": (KIND_IOT, 40, "Amazon OUI"),
    "68A40E": (KIND_IOT, 40, "Amazon OUI"),
    # Google / Nest
    "F4F5D8": (KIND_IOT, 40, "Google OUI"),
    "54A050": (KIND_IOT, 40, "Google OUI"),
    "3C5AB4": (KIND_IOT, 40, "Google OUI"),
    "18B430": (KIND_IOT, 40, "Nest OUI"),
    # Philips Hue bridge-ish
    "001788": (KIND_IOT, 35, "Philips OUI"),
    # Sonos
    "000E58": (KIND_MEDIA, 40, "Sonos OUI"),
    "5CAAFD": (KIND_MEDIA, 40, "Sonos OUI"),
    "B8E937": (KIND_MEDIA, 40, "Sonos OUI"),
    # Apple (weak alone — phone/laptop ambiguous)
    "F0D1A9": (KIND_PHONE, 20, "Apple OUI"),
    "ACDE48": (KIND_PHONE, 20, "Apple OUI"),
    "A4C361": (KIND_PHONE, 20, "Apple OUI"),
    "3C22FB": (KIND_PHONE, 20, "Apple OUI"),
    # Samsung (weak alone)
    "8C7712": (KIND_TV, 18, "Samsung OUI"),
    "001632": (KIND_TV, 18, "Samsung OUI"),
}

# Vendor string substrings from nmap (case-insensitive)
VENDOR_RULES: list[tuple[re.Pattern[str], str, int, str]] = [
    (re.compile(r"raspberry\s*pi", re.I), KIND_RASPBERRY_PI, 55, "vendor: Raspberry Pi"),
    (re.compile(r"espressif", re.I), KIND_IOT, 50, "vendor: Espressif"),
    (re.compile(r"ubiquiti|ubnt", re.I), KIND_NETWORK, 45, "vendor: Ubiquiti"),
    (re.compile(r"mikrotik", re.I), KIND_NETWORK, 45, "vendor: MikroTik"),
    (re.compile(r"cisco", re.I), KIND_NETWORK, 40, "vendor: Cisco"),
    (re.compile(r"tp-?link", re.I), KIND_NETWORK, 35, "vendor: TP-Link"),
    (re.compile(r"netgear", re.I), KIND_NETWORK, 35, "vendor: NETGEAR"),
    (re.compile(r"asus", re.I), KIND_NETWORK, 30, "vendor: ASUS"),
    (re.compile(r"hewlett.?packard|\bhp\b|hp\s*inc", re.I), KIND_PRINTER, 40, "vendor: HP"),
    (re.compile(r"brother", re.I), KIND_PRINTER, 45, "vendor: Brother"),
    (re.compile(r"canon", re.I), KIND_PRINTER, 40, "vendor: Canon"),
    (re.compile(r"epson", re.I), KIND_PRINTER, 40, "vendor: Epson"),
    (re.compile(r"xerox", re.I), KIND_PRINTER, 40, "vendor: Xerox"),
    (re.compile(r"kyocera", re.I), KIND_PRINTER, 40, "vendor: Kyocera"),
    (re.compile(r"synology", re.I), KIND_NAS, 50, "vendor: Synology"),
    (re.compile(r"qnap", re.I), KIND_NAS, 50, "vendor: QNAP"),
    (re.compile(r"seagate|lacie", re.I), KIND_NAS, 35, "vendor: storage brand"),
    (re.compile(r"amazon|ring\s*llc", re.I), KIND_IOT, 40, "vendor: Amazon"),
    (re.compile(r"google|nest\s*labs", re.I), KIND_IOT, 40, "vendor: Google/Nest"),
    (re.compile(r"philips|signify", re.I), KIND_IOT, 35, "vendor: Philips"),
    (re.compile(r"sonos", re.I), KIND_MEDIA, 45, "vendor: Sonos"),
    (re.compile(r"roku", re.I), KIND_TV, 40, "vendor: Roku"),
    (re.compile(r"samsung", re.I), KIND_TV, 22, "vendor: Samsung"),
    (re.compile(r"\blg\b|l\s*g\s*electronics", re.I), KIND_TV, 22, "vendor: LG"),
    (re.compile(r"sony", re.I), KIND_TV, 20, "vendor: Sony"),
    (re.compile(r"apple", re.I), KIND_PHONE, 22, "vendor: Apple"),
    (re.compile(r"hikvision|dahua|axis\s*communications|reolink", re.I), KIND_CAMERA, 50, "vendor: camera brand"),
    (re.compile(r"intel", re.I), KIND_SERVER, 12, "vendor: Intel"),
    (re.compile(r"vmware", re.I), KIND_SERVER, 35, "vendor: VMware"),
]

HOSTNAME_RULES: list[tuple[re.Pattern[str], str, int, str]] = [
    (re.compile(r"^(pi|rpi|raspberry)[-_]?\w*", re.I), KIND_RASPBERRY_PI, 40, "hostname looks like Pi"),
    (re.compile(r"raspberry|raspbian", re.I), KIND_RASPBERRY_PI, 40, "hostname mentions Pi"),
    (re.compile(r"printer|print|hp[-_]|epson|brother|canon|laserjet|deskjet", re.I), KIND_PRINTER, 40, "hostname looks like printer"),
    (re.compile(r"nas|diskstation|synology|qnap|truenas|freenas|storage", re.I), KIND_NAS, 40, "hostname looks like NAS"),
    (re.compile(r"router|gateway|firewall|opnsense|pfsense|unifi[-_]?gw|udm", re.I), KIND_ROUTER, 40, "hostname looks like router"),
    (re.compile(r"\bap[-_]|access[-_]?point|unifi[-_]?ap|eap[-_]?", re.I), KIND_AP, 35, "hostname looks like AP"),
    (re.compile(r"iphone|ipad|android|galaxy|pixel[-_]|oneplus", re.I), KIND_PHONE, 40, "hostname looks like phone"),
    (re.compile(r"camera|cam[-_]|nvr|ipc[-_]|reolink|ring[-_]?", re.I), KIND_CAMERA, 40, "hostname looks like camera"),
    (re.compile(r"tv[-_]|smart[-_]?tv|roku|chromecast|firestick|bravia|shield", re.I), KIND_TV, 35, "hostname looks like TV"),
    (re.compile(r"plex|jellyfin|emby|kodi|sonos", re.I), KIND_MEDIA, 35, "hostname looks like media"),
    (re.compile(r"echo|alexa|google[-_]?home|hue[-_]?bridge|esp\d*|tasmota|shelly", re.I), KIND_IOT, 35, "hostname looks like IoT"),
    (re.compile(r"desktop|laptop|workstation|pc[-_]?\d*|win[-_]?", re.I), KIND_WINDOWS, 25, "hostname looks like PC"),
    (re.compile(r"server|srv[-_]|vps|node[-_]?\d*|docker|k8s|kube", re.I), KIND_SERVER, 25, "hostname looks like server"),
]

OS_RULES: list[tuple[re.Pattern[str], str, int, str]] = [
    (re.compile(r"raspberry|raspbian|raspios", re.I), KIND_RASPBERRY_PI, 45, "OS: Raspberry Pi"),
    (re.compile(r"windows", re.I), KIND_WINDOWS, 35, "OS: Windows"),
    (re.compile(r"ios|iphone|ipad", re.I), KIND_PHONE, 40, "OS: Apple mobile"),
    (re.compile(r"android", re.I), KIND_PHONE, 35, "OS: Android"),
    (re.compile(r"synology|diskstation", re.I), KIND_NAS, 45, "OS: Synology"),
    (re.compile(r"qnap|qts", re.I), KIND_NAS, 45, "OS: QNAP"),
    (re.compile(r"openwrt|pfsense|opnsense|vyos|routeros", re.I), KIND_ROUTER, 40, "OS: network OS"),
    (re.compile(r"linux|ubuntu|debian|centos|fedora|arch", re.I), KIND_SERVER, 12, "OS: Linux"),
]

# Port sets: any open port in the set contributes weight once per rule
# (ports: set, services optional substrings, kind, weight, reason)
PORT_RULES: list[tuple[set[int], frozenset[str], str, int, str]] = [
    ({9100, 515, 631}, frozenset(), KIND_PRINTER, 45, "print services (9100/515/631)"),
    ({445, 139}, frozenset({"microsoft-ds", "netbios-ssn", "smb"}), KIND_WINDOWS, 30, "SMB / Windows shares"),
    ({3389}, frozenset({"ms-wbt-server", "rdp"}), KIND_WINDOWS, 35, "RDP open"),
    ({548}, frozenset({"afp"}), KIND_NAS, 30, "AFP (often NAS)"),
    ({5000, 5001}, frozenset(), KIND_NAS, 35, "Synology DSM ports"),
    ({8080}, frozenset(), KIND_NAS, 8, "alt HTTP (weak)"),
    ({554, 8554}, frozenset({"rtsp"}), KIND_CAMERA, 40, "RTSP (camera)"),
    ({8000, 37777, 34567}, frozenset(), KIND_CAMERA, 25, "common camera ports"),
    ({1883, 8883}, frozenset({"mqtt"}), KIND_IOT, 35, "MQTT"),
    ({5683}, frozenset({"coap"}), KIND_IOT, 30, "CoAP"),
    ({53}, frozenset({"domain", "dns"}), KIND_ROUTER, 20, "DNS (gateway-ish)"),
    ({67, 68}, frozenset({"dhcp", "bootps", "bootpc"}), KIND_ROUTER, 25, "DHCP"),
    ({161}, frozenset({"snmp"}), KIND_NETWORK, 15, "SNMP"),
    ({32400}, frozenset(), KIND_MEDIA, 40, "Plex"),
    ({8096, 8920}, frozenset(), KIND_MEDIA, 35, "Jellyfin / Emby"),
    ({8123}, frozenset(), KIND_IOT, 35, "Home Assistant"),
    ({22}, frozenset({"ssh"}), KIND_SERVER, 15, "SSH open"),
    ({80, 443}, frozenset({"http", "https", "http-proxy"}), KIND_SERVER, 8, "web ports"),
]


def _ports_from_json(ports_json: Optional[str]) -> list[dict[str, Any]]:
    if not ports_json:
        return []
    try:
        data = json.loads(ports_json)
        if not isinstance(data, list):
            return []
        return [p for p in data if isinstance(p, dict)]
    except Exception:
        return []


def _open_port_set(ports: Sequence[dict[str, Any] | Any]) -> tuple[set[int], set[str]]:
    """Return open TCP/UDP port numbers and lowercased service names."""
    nums: set[int] = set()
    services: set[str] = set()
    for p in ports:
        if isinstance(p, dict):
            state = str(p.get("state") or "").lower()
            if state and state != "open":
                continue
            try:
                nums.add(int(p.get("port") or 0))
            except (TypeError, ValueError):
                pass
            svc = str(p.get("service") or "").strip().lower()
            if svc:
                services.add(svc)
            prod = str(p.get("product") or "").strip().lower()
            if prod:
                services.add(prod)
        else:
            # ParsedPort-like
            state = str(getattr(p, "state", "") or "").lower()
            if state and state != "open":
                continue
            try:
                nums.add(int(getattr(p, "port", 0) or 0))
            except (TypeError, ValueError):
                pass
            svc = str(getattr(p, "service", "") or "").strip().lower()
            if svc:
                services.add(svc)
            prod = str(getattr(p, "product", "") or "").strip().lower()
            if prod:
                services.add(prod)
    nums.discard(0)
    return nums, services


def _add_score(
    scores: dict[str, int], reasons: dict[str, list[str]], kind: str, weight: int, reason: str
) -> None:
    if weight <= 0 or not kind:
        return
    scores[kind] = scores.get(kind, 0) + weight
    reasons.setdefault(kind, [])
    if reason not in reasons[kind]:
        reasons[kind].append(reason)


def classify_device(
    *,
    mac: Optional[str] = None,
    mac_vendor: Optional[str] = None,
    hostname: Optional[str] = None,
    os_summary: Optional[str] = None,
    ports_json: Optional[str] = None,
    ports: Optional[Sequence[Any]] = None,
) -> DeviceProfile:
    """Classify device from available discovery signals.

    Prefer *ports* (list of dicts or ParsedPort) if given; else parse *ports_json*.
    """
    scores: dict[str, int] = {}
    reasons: dict[str, list[str]] = {}
    vendor = (mac_vendor or "").strip()

    # --- OUI ---
    oui = oui_prefix(mac)
    if oui and oui in OUI_RULES:
        kind, w, reason = OUI_RULES[oui]
        _add_score(scores, reasons, kind, w, reason)

    # --- nmap vendor string ---
    if vendor:
        for pat, kind, w, reason in VENDOR_RULES:
            if pat.search(vendor):
                _add_score(scores, reasons, kind, w, reason)
                break  # first vendor match is enough

    # --- hostname ---
    hn = (hostname or "").strip()
    if hn:
        for pat, kind, w, reason in HOSTNAME_RULES:
            if pat.search(hn):
                _add_score(scores, reasons, kind, w, reason)
                # allow multiple hostname hits (e.g. pi-nas unlikely)

    # --- OS ---
    os_s = (os_summary or "").strip()
    if os_s:
        for pat, kind, w, reason in OS_RULES:
            if pat.search(os_s):
                _add_score(scores, reasons, kind, w, reason)
                break

    # --- ports ---
    port_list: Sequence[Any]
    if ports is not None:
        port_list = ports
    else:
        port_list = _ports_from_json(ports_json)
    open_ports, open_svcs = _open_port_set(port_list)

    if open_ports:
        for port_set, svc_need, kind, w, reason in PORT_RULES:
            if not (open_ports & port_set):
                continue
            if svc_need and not (open_svcs & svc_need):
                # still allow pure port match if no service names yet
                if not open_svcs:
                    pass
                else:
                    # services present but none match — still count ports for strong rules
                    if kind in (KIND_PRINTER, KIND_CAMERA, KIND_MEDIA, KIND_NAS):
                        pass
                    elif w < 20:
                        continue
            _add_score(scores, reasons, kind, w, reason)

        # product-based bumps
        for svc in open_svcs:
            if "synology" in svc or "diskstation" in svc:
                _add_score(scores, reasons, KIND_NAS, 40, f"service product: {svc[:40]}")
            if "openssh" in svc:
                _add_score(scores, reasons, KIND_SERVER, 8, "OpenSSH")
            if "microsoft" in svc or "iis" in svc:
                _add_score(scores, reasons, KIND_WINDOWS, 20, f"Microsoft service: {svc[:40]}")
            if "cups" in svc or "ipp" in svc:
                _add_score(scores, reasons, KIND_PRINTER, 25, "IPP/CUPS")
            if "rtsp" in svc:
                _add_score(scores, reasons, KIND_CAMERA, 30, "RTSP service")
            if "plex" in svc:
                _add_score(scores, reasons, KIND_MEDIA, 35, "Plex")

    if not scores:
        profile = DeviceProfile(
            kind=KIND_UNKNOWN,
            label=KIND_LABELS[KIND_UNKNOWN],
            short=KIND_SHORT[KIND_UNKNOWN],
            vendor=vendor,
            confidence=CONF_LOW,
            reasons=["no strong signals yet — run inventory for ports"],
            score=0,
        )
        return profile

    # Winner
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    best_kind, best_score = ranked[0]

    # Confidence from score + agreement
    second = ranked[1][1] if len(ranked) > 1 else 0
    if best_score >= 50 and best_score - second >= 15:
        conf = CONF_HIGH
    elif best_score >= 30:
        conf = CONF_MEDIUM
    else:
        conf = CONF_LOW

    # If top two are close, prefer more specific kinds over generic server
    if len(ranked) > 1 and best_score - second < 10:
        specific = {
            KIND_RASPBERRY_PI,
            KIND_PRINTER,
            KIND_NAS,
            KIND_CAMERA,
            KIND_ROUTER,
            KIND_AP,
            KIND_PHONE,
            KIND_TV,
            KIND_IOT,
            KIND_MEDIA,
            KIND_WINDOWS,
            KIND_NETWORK,
        }
        for k, sc in ranked[:3]:
            if k in specific and sc >= second:
                best_kind, best_score = k, sc
                conf = CONF_MEDIUM if sc >= 30 else CONF_LOW
                break

    why = reasons.get(best_kind, [])[:5]
    if vendor and vendor not in " ".join(why):
        # surface vendor even if not the winning rule text
        if not any("vendor:" in r or "OUI" in r for r in why):
            why = [f"MAC vendor: {vendor[:60]}"] + why
            why = why[:5]

    return DeviceProfile(
        kind=best_kind,
        label=KIND_LABELS.get(best_kind, best_kind),
        short=KIND_SHORT.get(best_kind, "?"),
        vendor=vendor,
        confidence=conf,
        reasons=why or [f"score {best_score}"],
        score=best_score,
    )


def profile_from_device(device: Any) -> DeviceProfile:
    """Classify an NmapDevice-like object."""
    return classify_device(
        mac=getattr(device, "mac_address", None),
        mac_vendor=getattr(device, "mac_vendor", None),
        hostname=getattr(device, "hostname", None),
        os_summary=getattr(device, "os_summary", None),
        ports_json=getattr(device, "ports_json", None),
    )


def profile_dict_from_device(device: Any) -> dict[str, Any]:
    return profile_from_device(device).to_dict()
