"""v1.6 host facts snapshot — parse OS/hardware without SSH."""
from __future__ import annotations

from app.services.host_facts import family_from_ids, parse_os_release_blob, pick_hardware
from app.services.api_summary import os_display_label


def test_parse_os_release_ubuntu():
    blob = 'ID=ubuntu\nPRETTY=Ubuntu 24.04.3 LTS\n'
    os_id, pretty = parse_os_release_blob(blob)
    assert os_id == "ubuntu"
    assert pretty == "Ubuntu 24.04.3 LTS"


def test_family_haos_and_ubuntu():
    assert family_from_ids("debian", "Home Assistant OS 14.2", "haos") == "haos"
    assert family_from_ids("ubuntu", "Ubuntu 24.04.3 LTS", None) == "ubuntu"


def test_pick_hardware_pi_then_dmi():
    assert pick_hardware(device_tree="Raspberry Pi 5 Model B Rev 1.0") == "Raspberry Pi 5 Model B Rev 1.0"
    assert pick_hardware(dmi_vendor="Dell Inc.", dmi_product="OptiPlex") == "Dell Inc. OptiPlex"
    assert pick_hardware(ha_chassis="green") == "green"


def test_os_display_prefers_pretty():
    assert os_display_label("debian", os_pretty="Ubuntu 24.04.3 LTS", os_id="ubuntu") == "Ubuntu 24.04.3 LTS"
    assert os_display_label("debian") == "Debian"
