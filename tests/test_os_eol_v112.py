"""OS line warns when a detected release is outside the supported list."""
from types import SimpleNamespace

from app.services import host_facts as facts


def test_supported_releases_stay_quiet():
    assert facts.os_support_warning("ubuntu", "Ubuntu 22.04.5 LTS") is None
    assert facts.os_support_warning("ubuntu", "Ubuntu 24.04.2 LTS") is None
    assert facts.os_support_warning("ubuntu", "Ubuntu 26.04 LTS") is None
    assert facts.os_support_warning("debian", "Debian GNU/Linux 12 (bookworm)") is None
    assert facts.os_support_warning("debian", "Debian GNU/Linux 13 (trixie)") is None
    assert facts.os_support_warning("raspbian", "Raspbian GNU/Linux 12 (bookworm)") is None
    assert facts.os_support_warning("raspios", "Debian GNU/Linux 13 (trixie)") is None


def test_interim_ubuntu_and_old_debian_warn():
    assert facts.os_support_warning("ubuntu", "Ubuntu 24.10") == facts.OS_SUPPORT_WARNING
    assert facts.os_support_warning("ubuntu", "Ubuntu 20.04.6 LTS") == facts.OS_SUPPORT_WARNING
    assert (
        facts.os_support_warning("debian", "Debian GNU/Linux 11 (bullseye)")
        == facts.OS_SUPPORT_WARNING
    )
    assert (
        facts.os_support_warning("raspbian", "Raspbian GNU/Linux 11 (bullseye)")
        == facts.OS_SUPPORT_WARNING
    )


def test_haos_and_unknown_families_do_not_warn():
    assert facts.os_support_warning("haos", "Home Assistant OS 16.1", "haos") is None
    assert facts.os_support_warning("debian", "Home Assistant OS 16.1", "haos") is None
    assert facts.os_support_warning("fedora", "Fedora Linux 42") is None
    assert facts.os_support_warning("ubuntu", "Ubuntu") is None
    assert facts.os_support_warning(None, None) is None


def test_host_page_places_the_warning_next_to_the_os_line():
    from pathlib import Path

    from app.templates import templates

    page = Path("app/templates/server_detail.html").read_text()
    assert "os_support_warning(server.os_id, server.os_pretty, server.os_type)" in page
    assert ">Unsupported</span>" in page
    modal = Path("app/templates/partials/server_detail_action_modals.html").read_text()
    assert modal.count("os_support_warning") >= 2
    rendered = templates.env.from_string(
        "{{ os_support_warning('debian', 'Debian GNU/Linux 11 (bullseye)') }}"
    ).render()
    assert rendered == "Unsupported release"


def test_warning_is_only_a_label():
    """The helper returns text. It does not call the patch or upgrade path."""
    assert facts.os_support_warning("ubuntu", "Ubuntu 18.04.6 LTS") == "Unsupported release"
    assert "does not upgrade" in (facts.os_support_warning.__doc__ or "")


def test_snapshot_carries_the_warning():
    server = SimpleNamespace(
        host_facts_json=None,
        host_facts_at=None,
        host_facts_status="ok",
        hostname="host-a.local",
        hardware=None,
        arch=None,
        os_pretty="Ubuntu 24.10",
        os_id="ubuntu",
        os_type="ubuntu",
        host_facts_error=None,
        cpu_cores=None,
        cpu_load=None,
        memory_total_bytes=None,
        memory_used_bytes=None,
        disk_total_bytes=None,
        disk_used_bytes=None,
    )
    snap = facts.snapshot_from_server(server)
    assert snap["os_support_warning"] == facts.OS_SUPPORT_WARNING

    server.os_pretty = "Ubuntu 24.04.1 LTS"
    assert facts.snapshot_from_server(server)["os_support_warning"] is None
