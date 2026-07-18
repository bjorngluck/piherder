"""Phase A1–A2: login shell smoke (Playwright).

A1 Open /auth/login — form visible
A2 Login as seeded admin — land on Dashboard
"""
from __future__ import annotations

import re

import pytest

from e2e.helpers import login_as_admin

pytestmark = pytest.mark.e2e


def test_a1_login_form_visible(page, base_url):
    page.goto(f"{base_url}/auth/login", wait_until="domcontentloaded")
    page.get_by_role("heading", name=re.compile(r"Sign in", re.I)).wait_for()
    assert page.locator("#login-email").is_visible()
    assert page.locator("#login-password").is_visible()
    assert page.get_by_role("button", name=re.compile(r"Log in", re.I)).is_visible()


def test_a2_login_as_admin_reaches_dashboard(page, base_url, e2e_credentials):
    email, password = e2e_credentials
    login_as_admin(page, base_url, email, password)
    page.get_by_role("heading", name="Dashboard").wait_for(timeout=15_000)
    assert "/auth/login" not in page.url
    assert "/auth/2fa" not in page.url
    assert "/auth/force-password" not in page.url
