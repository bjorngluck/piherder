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
    land = login_with_password(page, base_url, email, password)
    if land != "dashboard":
        page.get_by_role("heading", name="Dashboard").wait_for(timeout=30_000)


def login_with_password(page: Page, base_url: str, email: str, password: str) -> str:
    """Log in with email/password.

    Returns ``\"dashboard\"``, ``\"force_password\"``, or ``\"force_2fa\"`` depending
    on where the session lands.
    """
    page.goto(f"{base_url}/auth/login", wait_until="domcontentloaded")
    page.locator("#login-email").fill(email)
    page.locator("#login-password").fill(password)
    page.get_by_role("button", name=re.compile(r"Log in", re.I)).click()
    # Race: force-password, force-2fa, or Dashboard
    for _ in range(60):
        url = page.url or ""
        if "/auth/force-password" in url:
            return "force_password"
        if "/auth/force-2fa" in url:
            return "force_2fa"
        if page.get_by_role("heading", name="Dashboard").count():
            try:
                page.get_by_role("heading", name="Dashboard").wait_for(timeout=500)
                return "dashboard"
            except Exception:
                pass
        page.wait_for_timeout(250)
    page.get_by_role("heading", name="Dashboard").wait_for(timeout=5_000)
    return "dashboard"


def complete_force_password(page: Page, new_password: str) -> None:
    """Finish first-login password change when redirected to /auth/force-password."""
    expect(page).to_have_url(re.compile(r".*/auth/force-password"))
    page.locator("#force-new-password").fill(new_password)
    page.locator("#force-confirm-password").fill(new_password)
    page.get_by_role("button", name=re.compile(r"Save and continue", re.I)).click()
    page.get_by_role("heading", name="Dashboard").wait_for(timeout=30_000)


def create_user_as_admin(
    page: Page,
    base_url: str,
    *,
    email: str,
    password: str,
    role: str = "viewer",
) -> None:
    """Admin UI: create a user (temp password shown once). Caller must be logged in as admin."""
    page.goto(f"{base_url}/auth/users", wait_until="domcontentloaded")
    page.get_by_role("heading", name=re.compile(r"Users", re.I)).wait_for(timeout=15_000)
    page.locator("#btn-open-create-user").click()
    modal = page.locator("#create-user-modal")
    expect(modal).not_to_have_class(re.compile(r"\bhidden\b"))
    modal.locator('input[name="email"]').fill(email)
    modal.locator('input[name="password"]').fill(password)
    modal.locator('select[name="role"]').select_option(role)
    modal.locator("#create-user-submit").click()
    # Credentials modal or success banner
    expect(
        page.locator("#new-user-creds-modal, .banner-success")
    ).to_be_visible(timeout=15_000)


def logout(page: Page, base_url: str) -> None:
    """Log out via account menu (matches shell A6)."""
    open_account_menu(page)
    page.locator("#user-account a.is-danger", has_text="Sign out").click()
    page.wait_for_url(re.compile(r".*/auth/login"), timeout=15_000)


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
