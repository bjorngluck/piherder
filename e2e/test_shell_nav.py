"""Phase A3–A6: shell navigation, catalog tabs, theme toggle, logout."""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.helpers import desktop_nav, login_as_admin, open_account_menu

pytestmark = pytest.mark.e2e


def test_a3_primary_nav_shell(admin_page, base_url):
    """A3: Dashboard, Servers, Catalog, Jobs, Audit, Settings load without 5xx."""
    page = admin_page

    cases = [
        ("Dashboard", "/", "Dashboard"),
        ("Servers", "/servers", "Servers"),
        ("Catalog", "/integrations", "Integrations"),
        ("Jobs", "/jobs", "Jobs"),
        ("Audit", "/audit", "Audit log"),
        # Nav label Settings → /herder-backups (control-plane settings)
        ("Settings", "/herder-backups", "Settings"),
    ]

    for label, path_substr, heading in cases:
        desktop_nav(page, label)
        expect(page).to_have_url(re.compile(re.escape(path_substr)))
        # Dashboard uses dash-hero-title; ops pages use ops-hero-title
        hero = page.locator("h1.ops-hero-title, h1.dash-hero-title")
        expect(hero).to_have_text(heading)
        assert page.locator("body").count() == 1


def test_a4_catalog_tabs(admin_page, base_url):
    """A4: Catalog tabs Integrations / Certificates / Templates / Network."""
    page = admin_page
    desktop_nav(page, "Catalog")
    expect(page).to_have_url(re.compile(r".*/integrations"))
    expect(page.locator("h1.ops-hero-title")).to_have_text("Integrations")

    tabs = page.get_by_role("tablist", name="Catalog sections")
    expect(tabs).to_be_visible()

    tab_cases = [
        ("Certificates", "/certificates", "Certificates"),
        ("Templates", "/templates", "Templates"),
        ("Network", "/dns", "Network"),
        ("Integrations", "/integrations", "Integrations"),
    ]
    for name, path_substr, heading in tab_cases:
        tabs.get_by_role("tab", name=name).click()
        expect(page).to_have_url(re.compile(re.escape(path_substr)))
        expect(page.locator("h1.ops-hero-title")).to_have_text(heading)


def test_a5_theme_toggle(admin_page):
    """A5: Toggle theme once — document data-theme flips, no crash."""
    page = admin_page
    # Start from a known light state
    page.evaluate(
        """() => {
          localStorage.setItem('theme', 'light');
          document.documentElement.classList.remove('dark');
          document.documentElement.setAttribute('data-theme', 'light');
        }"""
    )
    page.reload(wait_until="domcontentloaded")
    page.get_by_role("heading", name="Dashboard").wait_for(timeout=15_000)

    before = page.evaluate(
        "() => document.documentElement.getAttribute('data-theme') || 'light'"
    )
    open_account_menu(page)
    page.locator('[data-action="toggle-theme"]').first.click()
    expect(page.locator("html")).not_to_have_attribute("data-theme", before)
    after = page.evaluate(
        "() => document.documentElement.getAttribute('data-theme') || 'light'"
    )
    assert after in ("light", "dark")
    # Page still usable
    expect(page.get_by_role("heading", name="Dashboard")).to_be_visible()


def test_a6_logout(page, base_url, e2e_credentials):
    """A6: Sign out returns to login form; session cookie cleared."""
    email, password = e2e_credentials
    login_as_admin(page, base_url, email, password)
    open_account_menu(page)
    page.locator("#user-account a.is-danger", has_text="Sign out").click()
    page.wait_for_url(re.compile(r".*/auth/login"), timeout=15_000)
    expect(page.locator("#login-email")).to_be_visible()
    # Cookie gone — unauthenticated HTML routes do not keep fleet nav
    cookies = page.context.cookies()
    assert not any(c.get("name") == "access_token" for c in cookies)
    page.goto(f"{base_url}/auth/login", wait_until="domcontentloaded")
    expect(page.locator(".desktop-nav")).to_have_count(0)
