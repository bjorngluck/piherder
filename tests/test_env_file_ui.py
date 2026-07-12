from app.services.env_file_ui import (
    SECRET_MASK,
    redact_env_content,
    redact_project_files_for_ui,
    restore_env_content,
    restore_project_files_on_save,
)


def test_redact_env_masks_secret_keys_only():
    raw = "TZ=UTC\nMYSQL_PASSWORD=s3cret-value\nMYSQL_USER=npm\n"
    out = redact_env_content(raw)
    assert "TZ=UTC" in out
    assert "MYSQL_PASSWORD=" + SECRET_MASK in out
    assert "s3cret-value" not in out
    assert "MYSQL_USER=npm" in out  # USER alone is not a secret-name pattern


def test_restore_env_keeps_live_when_masked():
    live = "MYSQL_PASSWORD=live-pass\nTZ=UTC\n"
    submitted = "MYSQL_PASSWORD=" + SECRET_MASK + "\nTZ=Europe/Oslo\n"
    out = restore_env_content(submitted, live)
    assert "MYSQL_PASSWORD=live-pass" in out
    assert "TZ=Europe/Oslo" in out


def test_restore_env_allows_intentional_change():
    live = "MYSQL_PASSWORD=old\n"
    submitted = "MYSQL_PASSWORD=new-pass\n"
    out = restore_env_content(submitted, live)
    assert "MYSQL_PASSWORD=new-pass" in out


def test_project_files_roundtrip_masks():
    live = {
        "docker-compose.yml": "services: {}\n",
        ".env": "DB_MYSQL_PASSWORD=real-secret\nTZ=UTC\n",
        "secrets/DB_MYSQL_PASSWORD": "real-secret",
    }
    ui = redact_project_files_for_ui(live, reveal=False)
    assert "real-secret" not in ui[".env"]
    assert ui["secrets/DB_MYSQL_PASSWORD"] == SECRET_MASK
    # user only changes TZ
    ui[".env"] = "DB_MYSQL_PASSWORD=" + SECRET_MASK + "\nTZ=CET\n"
    saved = restore_project_files_on_save(ui, live)
    assert "DB_MYSQL_PASSWORD=real-secret" in saved[".env"]
    assert "TZ=CET" in saved[".env"]
    assert saved["secrets/DB_MYSQL_PASSWORD"] == "real-secret"


def test_reveal_shows_cleartext():
    files = {".env": "MYSQL_PASSWORD=abc12345\n"}
    shown = redact_project_files_for_ui(files, reveal=True)
    assert "abc12345" in shown[".env"]
