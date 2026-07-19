"""High-value pure helpers in integrations registry (no remote poll)."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from app.models import Integration
from app.services.integrations import registry as reg


def test_parse_dump_config():
    assert reg.parse_config(None) == {}
    assert reg.parse_config("{bad") == {}
    assert reg.parse_config('["x"]') == {}
    raw = reg.dump_config({"a": 1, "b": "x"})
    assert reg.parse_config(raw) == {"a": 1, "b": "x"}


def test_poll_interval_and_tls_verify():
    integ = SimpleNamespace(config_json='{"poll_interval_sec": 5, "tls_verify": false}')
    # clamped to MIN
    assert reg.poll_interval_sec(integ) >= reg.MIN_POLL_INTERVAL_SEC
    assert reg.tls_verify(integ) is False
    integ2 = SimpleNamespace(config_json='{"poll_interval_sec": "nope"}')
    assert reg.poll_interval_sec(integ2) == reg.DEFAULT_POLL_INTERVAL_SEC
    integ3 = SimpleNamespace(config_json="{}")
    assert reg.tls_verify(integ3) is True


def test_encrypt_decrypt_credentials_roundtrip():
    blob = reg.encrypt_credentials("key-1", username="u", password="p")
    integ = SimpleNamespace(credentials_encrypted=blob)
    c = reg.decrypt_credentials(integ)
    assert c["api_key"] == "key-1"
    assert c["username"] == "u"
    assert c["password"] == "p"
    assert reg.decrypt_api_key(integ) == "key-1"
    assert reg.has_credentials(integ) is True
    assert reg.has_kuma_login(integ) is True
    empty = SimpleNamespace(credentials_encrypted=None)
    assert reg.decrypt_credentials(empty) == {}
    assert reg.has_credentials(empty) is False
    assert reg.has_kuma_login(empty) is False


def test_encrypt_credentials_full_keeps_password():
    prev_blob = reg.encrypt_credentials("old-key", username="admin", password="secret")
    prev = SimpleNamespace(credentials_encrypted=prev_blob)
    # empty password keeps previous
    new_blob = reg.encrypt_credentials_full(
        "new-key", username="admin", password="", keep_from=prev
    )
    c = reg.decrypt_credentials(SimpleNamespace(credentials_encrypted=new_blob))
    assert c["api_key"] == "new-key"
    assert c["password"] == "secret"


def test_grafana_query_templates_and_display_names():
    integ = SimpleNamespace(
        config_json=reg.dump_config(
            {
                "query_template": "q-host",
                "query_template_container": "q-ctr",
                "display_names": {"uid1": "My Dash", "x": ""},
            }
        )
    )
    assert reg.query_template(integ) == "q-host"
    assert reg.query_template_container(integ) == "q-ctr"
    assert reg.query_template_logs(integ)  # default non-empty
    names = reg.preferred_display_names(integ)
    assert names.get("uid1") == "My Dash"
    assert reg.preferred_display_name(integ, "uid1") == "My Dash"
    assert reg.preferred_display_name(integ, "") == ""
    assert reg.normalize_grafana_kind("logs") == "logs"
    assert reg.normalize_grafana_kind("nope") == reg.GRAFANA_KIND_METRICS


def test_resolve_grafana_display_label_preference():
    integ = SimpleNamespace(
        config_json=reg.dump_config({"display_names": {"abc": "Preferred"}})
    )
    binding = SimpleNamespace(
        external_id="abc",
        external_label="ext",
        external_meta_json=None,
    )
    label, override, title = reg.resolve_grafana_display_label(
        integ,
        binding,
        meta={"grafana_title": "Grafana Title", "label_override": "Legacy"},
    )
    assert label == "Preferred"
    assert override == "Preferred"
    assert title == "Grafana Title"


def test_set_preferred_display_name_sqlite(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'reg.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        row = Integration(
            type=reg.TYPE_GRAFANA,
            name="G",
            base_url="http://g",
            enabled=True,
            config_json="{}",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        reg.set_preferred_display_name(s, row, "uid-a", "Alpha")
        assert reg.preferred_display_name(row, "uid-a") == "Alpha"
        reg.set_preferred_display_name(s, row, "uid-a", "")  # clear
        assert reg.preferred_display_name(row, "uid-a") == ""
        with pytest.raises(ValueError):
            reg.set_preferred_display_name(s, row, "", "x")


def test_is_pihole_primary():
    assert reg.is_pihole_primary(SimpleNamespace(config_json='{"is_primary": true}'))
    assert not reg.is_pihole_primary(SimpleNamespace(config_json="{}"))
