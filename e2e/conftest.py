"""Playwright E2E fixtures — browser against a running PiHerder HTTP stack.

Requires a live base URL (compose e2e stack or CI uvicorn), not unit mocks.

  export PIHERDER_E2E_BASE_URL=http://127.0.0.1:18000
  pytest e2e -q
"""
from __future__ import annotations

import os

import pytest

from e2e.helpers import login_as_admin

# Fixed credentials for the e2e seed admin (first register on empty DB).
E2E_EMAIL = os.environ.get("PIHERDER_E2E_EMAIL", "e2e@piherder.test")
E2E_PASSWORD = os.environ.get("PIHERDER_E2E_PASSWORD", "E2eTestPass1")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "e2e: browser end-to-end tests (requires running app)"
    )


@pytest.fixture(scope="session")
def base_url() -> str:
    url = (
        os.environ.get("PIHERDER_E2E_BASE_URL")
        or os.environ.get("BASE_URL")
        or "http://127.0.0.1:18000"
    ).rstrip("/")
    return url


@pytest.fixture(scope="session")
def e2e_credentials() -> tuple[str, str]:
    return E2E_EMAIL, E2E_PASSWORD


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Desktop viewport; ignore HTTPS errors if someone points at TLS."""
    return {
        **browser_context_args,
        "viewport": {"width": 1440, "height": 900},
        "ignore_https_errors": True,
    }


@pytest.fixture
def admin_page(page, base_url, e2e_credentials):
    """Authenticated browser page as the e2e admin."""
    email, password = e2e_credentials
    login_as_admin(page, base_url, email, password)
    return page
