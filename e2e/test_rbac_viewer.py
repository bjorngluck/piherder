"""E2E B6: viewer cannot add a server (wizard / Add CTA)."""
from __future__ import annotations

import re
import time

import pytest
from playwright.sync_api import expect

from e2e.helpers import (
    complete_force_password,
    create_user_as_admin,
    desktop_nav,
    login_as_admin,
    login_with_password,
    logout,
)

pytestmark = pytest.mark.e2e

VIEWER_PASSWORD_FINAL = "ViewerPass1ok"


def test_b6_viewer_cannot_add_server(page, base_url, e2e_credentials):
    """B6: viewer has no Add CTA; GET /servers/new is 403."""
    admin_email, admin_password = e2e_credentials
    stamp = str(int(time.time() * 1000) % 10_000_000)
    viewer_email = f"viewer-{stamp}@piherder.test"
    viewer_temp = "TempViewer1x"  # must differ from final (policy)

    # 1) Admin creates viewer
    login_as_admin(page, base_url, admin_email, admin_password)
    create_user_as_admin(
        page,
        base_url,
        email=viewer_email,
        password=viewer_temp,
        role="viewer",
    )
    logout(page, base_url)

    # 2) Viewer first login → force password → dashboard
    land = login_with_password(page, base_url, viewer_email, viewer_temp)
    assert land == "force_password", f"expected force_password, got {land} url={page.url}"
    complete_force_password(page, VIEWER_PASSWORD_FINAL)

    # 3) Servers list: no Add button
    desktop_nav(page, "Servers")
    page.get_by_role("heading", name="Servers").wait_for(timeout=15_000)
    expect(page.locator('[data-testid="btn-add-server"]')).to_have_count(0)
    expect(page.locator('[data-testid="btn-add-server-empty"]')).to_have_count(0)

    # 4) Direct wizard URL is forbidden (operator+ dependency)
    # Use APIRequestContext so status is reliable (browser may wrap 403 oddly)
    api = page.context.request
    # Copy cookies for authenticated request
    r = api.get(f"{base_url}/servers/new")
    assert r.status == 403, f"expected 403 for /servers/new, got {r.status} {r.text()[:200]}"
    detail = (r.text() or "").lower()
    assert "operator" in detail or "forbidden" in detail or "403" in detail

    r2 = api.get(f"{base_url}/servers/new/advanced")
    assert r2.status == 403, f"expected 403 for advanced, got {r2.status}"
