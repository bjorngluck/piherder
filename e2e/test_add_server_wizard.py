"""Phase B: add-host wizard chrome, identity→trust, save & exit, advanced path."""
from __future__ import annotations

import re
import time

import pytest
from playwright.sync_api import expect

from e2e.helpers import desktop_nav

pytestmark = pytest.mark.e2e


def test_b1_primary_cta_opens_wizard(admin_page):
    """B1: Servers + Add opens guided wizard at Identity."""
    page = admin_page
    desktop_nav(page, "Servers")
    page.get_by_role("heading", name="Servers").wait_for(timeout=15_000)
    page.locator('[data-testid="btn-add-server"]').click()
    expect(page).to_have_url(re.compile(r".*/servers/new"))
    wiz = page.locator('[data-testid="add-server-wizard"]')
    expect(wiz).to_be_visible()
    expect(wiz).to_have_attribute("data-wizard-step", "identity")
    expect(page.locator('[data-testid="wizard-step-indicator"]')).to_be_visible()
    expect(page.locator('[data-testid="wizard-step-identity"]')).to_have_class(
        re.compile(r"wizard-step--current")
    )
    expect(page.locator('[data-testid="wizard-form-identity"]')).to_be_visible()
    expect(page.get_by_role("heading", name="Add server")).to_be_visible()


def test_b2_identity_trust_and_connect_ui(admin_page, base_url):
    """B2: Identity → Trust (generate key) → Connect; no private key PEM in DOM."""
    page = admin_page
    stamp = str(int(time.time() * 1000) % 10_000_000)
    name = f"e2e-wiz-{stamp}"
    host = f"e2e-{stamp}.invalid"

    page.goto(f"{base_url}/servers/new", wait_until="domcontentloaded")
    page.locator('[data-testid="wizard-input-name"]').fill(name)
    page.locator('[data-testid="wizard-input-hostname"]').fill(host)
    page.locator('[data-testid="wizard-continue"]').click()

    expect(page.locator('[data-testid="add-server-wizard"]')).to_have_attribute(
        "data-wizard-step", "trust"
    )
    expect(page.locator('[data-testid="wizard-form-trust"]')).to_be_visible()
    # Generate key (default) — no password
    page.locator('[data-testid="wizard-continue"]').click()

    expect(page.locator('[data-testid="add-server-wizard"]')).to_have_attribute(
        "data-wizard-step", "connect"
    )
    expect(page.locator('[data-testid="wizard-panel-connect"]')).to_be_visible()
    expect(page.locator('[data-testid="wizard-test-connection"]')).to_be_visible()
    expect(page.locator('[data-testid="wizard-deploy-key"]')).to_be_visible()
    # Public key visible for manual install; private key never in DOM
    expect(page.locator('[data-testid="wizard-public-key-block"]')).to_be_visible()
    pub = page.locator('[data-testid="wizard-public-key"]')
    expect(pub).to_be_visible()
    pub_val = pub.input_value()
    assert pub_val.strip()
    assert "ssh-" in pub_val or "ecdsa-" in pub_val or "sk-" in pub_val
    expect(page.locator('[data-testid="wizard-copy-public-key"]')).to_be_visible()
    body = page.content()
    assert "BEGIN OPENSSH PRIVATE KEY" not in body
    assert "BEGIN RSA PRIVATE KEY" not in body
    assert name in body or host in body


def test_b4_save_and_exit_partial_host(admin_page, base_url):
    """B4: Save & exit after Trust leaves a usable host; resume wizard works."""
    page = admin_page
    stamp = str(int(time.time() * 1000) % 10_000_000)
    name = f"e2e-exit-{stamp}"
    host = f"exit-{stamp}.invalid"

    page.goto(f"{base_url}/servers/new", wait_until="domcontentloaded")
    page.locator('[data-testid="wizard-input-name"]').fill(name)
    page.locator('[data-testid="wizard-input-hostname"]').fill(host)
    page.locator('[data-testid="wizard-continue"]').click()
    expect(page.locator('[data-testid="wizard-form-trust"]')).to_be_visible()
    page.locator('[data-testid="wizard-continue"]').click()
    expect(page.locator('[data-testid="wizard-panel-connect"]')).to_be_visible()

    page.locator('[data-testid="wizard-save-exit"]').click()
    # Server detail
    expect(page).to_have_url(re.compile(r".*/servers/\d+"))
    expect(page.get_by_text(name, exact=False).first).to_be_visible()

    # Resume: open wizard with server_id → not identity (trust/connect)
    m = re.search(r"/servers/(\d+)", page.url)
    assert m
    sid = m.group(1)
    page.goto(
        f"{base_url}/servers/new?step=identity&server_id={sid}",
        wait_until="domcontentloaded",
    )
    step = page.locator('[data-testid="add-server-wizard"]').get_attribute(
        "data-wizard-step"
    )
    assert step in ("trust", "connect", "privilege", "features")
    # Explicit connect resume
    page.goto(
        f"{base_url}/servers/new?step=connect&server_id={sid}",
        wait_until="domcontentloaded",
    )
    expect(page.locator('[data-testid="wizard-panel-connect"]')).to_be_visible()


def test_b4b_clear_password_cta(admin_page, base_url):
    """Clear password CTA appears when trust stored a bootstrap password."""
    page = admin_page
    stamp = str(int(time.time() * 1000) % 10_000_000)
    name = f"e2e-pw-{stamp}"
    host = f"pw-{stamp}.invalid"

    page.goto(f"{base_url}/servers/new", wait_until="domcontentloaded")
    page.locator('[data-testid="wizard-input-name"]').fill(name)
    page.locator('[data-testid="wizard-input-hostname"]').fill(host)
    page.locator('[data-testid="wizard-continue"]').click()
    page.locator("#wiz-ssh-password").fill("TempBootstrap1")
    page.locator('[data-testid="wizard-continue"]').click()
    expect(page.locator('[data-testid="wizard-panel-connect"]')).to_be_visible()
    expect(page.locator('[data-testid="wizard-form-clear-password"]')).to_be_visible()
    page.locator('[data-testid="wizard-clear-password"]').click()
    expect(page.locator('[data-testid="wizard-flash-ok"]')).to_be_visible()
    expect(page.locator('[data-testid="wizard-no-password"]')).to_be_visible()


def test_b5_advanced_form_reachable(admin_page, base_url):
    """B5: Advanced form still reachable from wizard link."""
    page = admin_page
    page.goto(f"{base_url}/servers/new", wait_until="domcontentloaded")
    expect(page.locator('[data-testid="add-server-wizard"]')).to_be_visible()
    page.locator('[data-testid="wizard-advanced-link"]').click()
    expect(page).to_have_url(re.compile(r".*/servers/new/advanced"))
    expect(page.locator('[data-testid="add-server-advanced-form"]')).to_be_visible()
    expect(page.locator('[data-testid="add-server-advanced-title"]')).to_contain_text(
        "advanced"
    )
    page.locator('[data-testid="advanced-to-wizard"]').click()
    expect(page.locator('[data-testid="add-server-wizard"]')).to_be_visible()
