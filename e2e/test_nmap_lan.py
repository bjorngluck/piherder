"""N9 E2E shells for LAN Discovery (nmap) — no live scan required."""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.helpers import desktop_nav, login_as_admin

pytestmark = pytest.mark.e2e


def test_n9_lan_discovery_add_form_and_tabs(admin_page, base_url):
    """Catalog → add LAN Discovery form (or open existing) + tab chrome."""
    page = admin_page
    desktop_nav(page, "Catalog")
    expect(page).to_have_url(re.compile(r".*/integrations"))

    # Prefer existing integration if present
    link = page.locator('a[href*="/integrations/"]').filter(
        has_text=re.compile(r"LAN|nmap|Discovery", re.I)
    )
    if link.count() > 0:
        link.first.click()
        page.wait_for_load_state("domcontentloaded")
    else:
        # Try catalog add path
        add = page.get_by_role("link", name=re.compile(r"Add|LAN Discovery|nmap", re.I))
        if add.count() == 0:
            page.goto(f"{base_url}/integrations/new/nmap", wait_until="domcontentloaded")
        else:
            # Integrations list may have type cards
            page.goto(f"{base_url}/integrations/new/nmap", wait_until="domcontentloaded")

        # If create form: submit minimal CIDR
        if page.locator('form').filter(has=page.locator('textarea[name="cidrs"], input[name="cidrs"]')).count():
            cidrs = page.locator('textarea[name="cidrs"], input[name="cidrs"]').first
            if cidrs.count():
                cidrs.fill("192.168.86.0/24")
            name = page.locator('input[name="name"]')
            if name.count():
                name.fill("LAN Discovery E2E")
            page.get_by_role("button", name=re.compile(r"Save|Create|Add", re.I)).first.click()
            page.wait_for_load_state("domcontentloaded")

    # Should be on nmap detail with tabs
    expect(page.locator("body")).to_contain_text(re.compile(r"LAN|Discovery|nmap|Devices|Network", re.I))

    for tab_name, tab_q in (
        ("Overview", "overview"),
        ("Devices", "devices"),
        ("Network", "network"),
        ("Schedules", "schedules"),
        ("Runs", "runs"),
    ):
        tab = page.get_by_role("link", name=re.compile(rf"^{tab_name}$", re.I))
        if tab.count() == 0:
            tab = page.locator(f'a[href*="tab={tab_q}"]')
        if tab.count():
            tab.first.click()
            page.wait_for_load_state("domcontentloaded")
            expect(page).to_have_url(re.compile(rf"tab={tab_q}"))

    # Scan-now curated controls (overview)
    overview = page.locator('a[href*="tab=overview"]')
    if overview.count():
        overview.first.click()
        page.wait_for_load_state("domcontentloaded")
    form = page.locator('[data-testid="nmap-scan-now-form"]')
    if form.count():
        expect(form).to_be_visible()
        expect(page.locator('[data-testid="nmap-script-preset"]')).to_be_visible()
        expect(page.locator('[data-testid="nmap-timing"]')).to_be_visible()
        expect(page.locator('[data-testid="nmap-queue-scan"]')).to_be_visible()


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
