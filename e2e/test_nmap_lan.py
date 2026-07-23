"""N9 / v0.9 E2E shells for LAN Discovery (nmap) — no live scan required."""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.helpers import desktop_nav, login_as_admin

pytestmark = pytest.mark.e2e


def _open_nmap_detail(page, base_url: str) -> None:
    """Catalog → existing LAN Discovery, or create form / new path."""
    desktop_nav(page, "Catalog")
    expect(page).to_have_url(re.compile(r".*/integrations"))

    link = page.locator('a[href*="/integrations/"]').filter(
        has_text=re.compile(r"LAN|nmap|Discovery", re.I)
    )
    if link.count() > 0:
        link.first.click()
        page.wait_for_load_state("domcontentloaded")
        return

    page.goto(f"{base_url}/integrations/new/nmap", wait_until="domcontentloaded")
    if page.locator("form").filter(
        has=page.locator('textarea[name="cidrs"], input[name="cidrs"]')
    ).count():
        cidrs = page.locator('textarea[name="cidrs"], input[name="cidrs"]').first
        if cidrs.count():
            cidrs.fill("192.168.86.0/24")
        name = page.locator('input[name="name"]')
        if name.count():
            name.fill("LAN Discovery E2E")
        page.get_by_role("button", name=re.compile(r"Save|Create|Add", re.I)).first.click()
        page.wait_for_load_state("domcontentloaded")


def test_n9_lan_discovery_add_form_and_tabs(admin_page, base_url):
    """Catalog → LAN Discovery detail: primary tabs + Overview modals + Schedules."""
    page = admin_page
    _open_nmap_detail(page, base_url)

    expect(page.locator("body")).to_contain_text(
        re.compile(r"LAN|Discovery|nmap|Devices|Schedules", re.I)
    )

    # Primary LAN tabs (Network map lives under Devices List|Map — not a section tab)
    lan_tabs = page.get_by_role("tablist", name=re.compile(r"LAN Discovery sections", re.I))
    expect(lan_tabs).to_be_visible()
    for tab_name, tab_q in (
        ("Overview", "overview"),
        ("Devices", "devices"),
        ("Schedules", "schedules"),
        ("Runs", "runs"),
    ):
        tab = lan_tabs.get_by_role("tab", name=re.compile(rf"^{tab_name}$", re.I))
        if tab.count() == 0:
            tab = page.locator(f'a[href*="tab={tab_q}"]')
        if tab.count():
            tab.first.click()
            page.wait_for_load_state("domcontentloaded")
            expect(page).to_have_url(re.compile(rf"tab={tab_q}"))

    # No Network tab inside LAN Discovery sections (Catalog still has a Network tab)
    expect(lan_tabs.get_by_role("tab", name=re.compile(r"^Network$", re.I))).to_have_count(0)

    # E3: Overview keeps status chips; Scan now / vuln pack open modals
    overview = page.locator('a[href*="tab=overview"]')
    if overview.count():
        overview.first.click()
        page.wait_for_load_state("domcontentloaded")
    expect(page.locator('[data-testid="nmap-overview-stats"]')).to_be_visible()
    expect(page.locator('[data-testid="nmap-vuln-status-strip"]')).to_be_visible()
    # E3: no Devices/Network/Jobs shortcut buttons — tabs only
    expect(page.locator('[data-testid="nmap-overview-actions"]')).to_be_visible()
    open_scan = page.locator('[data-testid="nmap-open-scan-modal"]')
    if open_scan.count():
        expect(open_scan).to_be_visible()
        expect(page.locator('[data-testid="nmap-scan-modal"]')).to_be_hidden()
        open_scan.click()
        expect(page.locator('[data-testid="nmap-scan-modal"]')).to_be_visible()
        form = page.locator('[data-testid="nmap-scan-now-form"]')
        expect(form).to_be_visible()
        expect(page.locator('[data-testid="nmap-script-preset"]')).to_be_visible()
        expect(page.locator('[data-testid="nmap-timing"]')).to_be_visible()
        expect(page.locator('[data-testid="nmap-queue-scan"]')).to_be_visible()
        page.locator('[data-testid="nmap-scan-modal-close"]').click()
        expect(page.locator('[data-testid="nmap-scan-modal"]')).to_be_hidden()
    open_vuln = page.locator('[data-testid="nmap-open-vuln-modal"]')
    if open_vuln.count():
        open_vuln.click()
        expect(page.locator('[data-testid="nmap-vuln-modal"]')).to_be_visible()
        expect(page.locator('[data-testid="nmap-vuln-update-form"]')).to_be_visible()
        page.locator('[data-testid="nmap-vuln-modal-close"]').click()
        expect(page.locator('[data-testid="nmap-vuln-modal"]')).to_be_hidden()

    # E5: Schedules list-first + add modal
    schedules_tab = page.locator('a[href*="tab=schedules"]').first
    if schedules_tab.count():
        schedules_tab.click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator('[data-testid="nmap-schedules-toolbar"]')).to_be_visible()
        add_btn = page.locator('[data-testid="nmap-schedule-add"]')
        if add_btn.count():
            expect(page.locator('[data-testid="nmap-schedule-modal"]')).to_be_hidden()
            add_btn.click()
            page.wait_for_load_state("domcontentloaded")
            expect(page).to_have_url(re.compile(r"new=1"))
            expect(page.locator('[data-testid="nmap-schedule-modal"]')).to_be_visible()
            expect(page.locator('[data-testid="nmap-schedule-form"]')).to_be_visible()
            expect(page.locator('[data-testid="nmap-schedule-name"]')).to_be_visible()
            page.locator('[data-testid="nmap-schedule-modal-close"]').click()
            page.wait_for_load_state("domcontentloaded")
            expect(page.locator('[data-testid="nmap-schedule-modal"]')).to_be_hidden()


def test_e3b_devices_list_map_views(admin_page, base_url):
    """E3b: Devices tab List | Map toggle; legacy ?tab=network → map view."""
    page = admin_page
    _open_nmap_detail(page, base_url)

    devices_tab = page.locator('a[href*="tab=devices"]').first
    expect(devices_tab).to_be_visible()
    devices_tab.click()
    page.wait_for_load_state("domcontentloaded")

    # List view chrome
    expect(page.locator('[data-testid="nmap-devices-view-toggle"]')).to_be_visible()
    expect(page.locator('[data-testid="nmap-view-list"]')).to_be_visible()
    expect(page.locator('[data-testid="nmap-view-map"]')).to_be_visible()
    expect(page.locator('[data-testid="nmap-devices-filter-bar"]')).to_be_visible()
    expect(page.locator('[data-testid="nmap-devices-search"]')).to_be_visible()
    bar = page.locator('[data-testid="nmap-devices-filter-bar"]')
    expect(bar.get_by_role("link", name=re.compile(r"^All$", re.I))).to_be_visible()

    # Map view
    page.locator('[data-testid="nmap-view-map"]').click()
    page.wait_for_load_state("domcontentloaded")
    expect(page).to_have_url(re.compile(r"view=map"))
    expect(page.locator('[data-testid="nmap-network-filter-bar"]')).to_be_visible()
    expect(page.locator('[data-testid="nmap-map-search"]')).to_be_visible()
    expect(page.locator('[data-testid="nmap-map-show-discovered"]')).to_be_visible()

    # Back to list
    page.locator('[data-testid="nmap-view-list"]').click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator('[data-testid="nmap-devices-filter-bar"]')).to_be_visible()

    # Legacy Network tab URL still serves map under Devices
    m = re.search(r"/integrations/(\d+)", page.url)
    assert m, f"expected integration detail URL, got {page.url}"
    integ_id = m.group(1)
    page.goto(
        f"{base_url}/integrations/{integ_id}?tab=network",
        wait_until="domcontentloaded",
    )
    expect(page.locator('[data-testid="nmap-devices-view-toggle"]')).to_be_visible()
    expect(page.locator('[data-testid="nmap-network-filter-bar"]')).to_be_visible()
    expect(page.locator('[data-testid="nmap-map-search"]')).to_be_visible()


def test_e4b_runs_and_schedules_mobile_cards(admin_page, base_url):
    """E4b: Runs + Schedules expose mobile card shells (visible under narrow viewport)."""
    page = admin_page
    _open_nmap_detail(page, base_url)

    # Desktop: table/list wrappers present (may be empty)
    page.locator('a[href*="tab=schedules"]').first.click()
    page.wait_for_load_state("domcontentloaded")
    # Either empty state or list+cards markup
    empty_sched = page.locator('[data-testid="nmap-schedules-empty"]')
    if empty_sched.count() and empty_sched.is_visible():
        expect(empty_sched).to_be_visible()
    else:
        expect(page.locator('[data-testid="nmap-schedules-cards"]')).to_be_attached()
        expect(page.locator('[data-testid="nmap-schedules-list"]')).to_be_attached()

    page.locator('a[href*="tab=runs"]').first.click()
    page.wait_for_load_state("domcontentloaded")
    empty_runs = page.locator('[data-testid="nmap-runs-empty"]')
    if empty_runs.count() and empty_runs.is_visible():
        expect(empty_runs).to_be_visible()
    else:
        expect(page.locator('[data-testid="nmap-runs-cards"]')).to_be_attached()
        expect(page.locator('[data-testid="nmap-runs-table"]')).to_be_attached()

    # Narrow viewport: card containers should be the mobile layout target
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator('a[href*="tab=schedules"]').first.click()
    page.wait_for_load_state("domcontentloaded")
    if not (
        page.locator('[data-testid="nmap-schedules-empty"]').count()
        and page.locator('[data-testid="nmap-schedules-empty"]').is_visible()
    ):
        expect(page.locator('[data-testid="nmap-schedules-cards"]')).to_be_visible()
        # Desktop table uses ph-only-sm-up (not hidden sm:block — base .hidden is !important)
        expect(page.locator('[data-testid="nmap-schedules-list"]')).to_be_hidden()

    page.locator('a[href*="tab=runs"]').first.click()
    page.wait_for_load_state("domcontentloaded")
    if empty_runs.count() and page.locator('[data-testid="nmap-runs-empty"]').is_visible():
        expect(page.locator('[data-testid="nmap-runs-empty"]')).to_be_visible()
    else:
        expect(page.locator('[data-testid="nmap-runs-cards"]')).to_be_visible()
        expect(page.locator('[data-testid="nmap-runs-table"]')).to_be_hidden()

    # Restore desktop for later tests in same worker (fixture is function-scoped page)
    page.set_viewport_size({"width": 1440, "height": 900})

def test_n9_viewer_cannot_create_nmap(page, base_url, e2e_credentials):
    """Viewer GET create path is 403 (operator+ only)."""
    import time

    from e2e.helpers import (
        complete_force_password,
        create_user_as_admin,
        login_with_password,
        logout,
    )

    admin_email, admin_password = e2e_credentials
    stamp = str(int(time.time() * 1000) % 10_000_000)
    viewer_email = f"nmap-view-{stamp}@piherder.test"
    viewer_temp = "TempViewer1x"

    login_as_admin(page, base_url, admin_email, admin_password)
    create_user_as_admin(
        page,
        base_url,
        email=viewer_email,
        password=viewer_temp,
        role="viewer",
    )
    logout(page, base_url)

    land = login_with_password(page, base_url, viewer_email, viewer_temp)
    if land == "force_password":
        complete_force_password(page, "ViewerPass1ok")

    api = page.context.request
    r = api.get(f"{base_url}/integrations/new/nmap")
    assert r.status == 403, f"expected 403 for nmap create, got {r.status}"
