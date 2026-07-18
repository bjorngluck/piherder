"""Shared E2E helpers (keep conftest thin)."""
from __future__ import annotations

import re

from playwright.sync_api import Page, expect


def ensure_admin_registered(
    page: Page, base_url: str, email: str, password: str
) -> None:
    """If registration is open (empty DB), create the e2e admin; else no-op."""
    page.goto(f"{base_url}/auth/register", wait_until="domcontentloaded")
    email_input = page.locator("#reg-email")
    if email_input.count() == 0:
        return
    email_input.fill(email)
    page.locator("#reg-password").fill(password)
    page.get_by_role("button", name=re.compile(r"Create account", re.I)).click()
    page.wait_for_url(re.compile(r".*/auth/login"), timeout=30_000)


def login_as_admin(page: Page, base_url: str, email: str, password: str) -> None:
    """Register seed admin if needed, then log in and land on dashboard."""
    ensure_admin_registered(page, base_url, email, password)
    page.goto(f"{base_url}/auth/login", wait_until="domcontentloaded")
    page.locator("#login-email").fill(email)
    page.locator("#login-password").fill(password)
    page.get_by_role("button", name=re.compile(r"Log in", re.I)).click()
    page.get_by_role("heading", name="Dashboard").wait_for(timeout=30_000)


def desktop_nav(page: Page, label: str) -> None:
    """Click a primary desktop nav link by visible label."""
    link = page.locator("nav.nav-header .desktop-nav a.nav-link", has_text=label)
    expect(link).to_be_visible()
    link.click()


def open_account_menu(page: Page) -> None:
    """Open the desktop avatar / account dropdown."""
    details = page.locator("#user-account")
    expect(details).to_be_visible()
    # <details> may already be open from a prior step
    if not details.evaluate("el => el.open"):
        details.locator("summary").click()
    expect(details).to_have_attribute("open", "")
