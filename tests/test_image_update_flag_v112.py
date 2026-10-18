"""Image update flag stays until a running container uses the pulled image."""
from app.services.docker_management import pending_registry_images


def test_pull_change_flags_even_when_nothing_is_running():
    pending = pending_registry_images(
        ["nginx:1.27"],
        {"nginx:1.27": "sha256:old"},
        {"nginx:1.27": "sha256:new"},
        "",
    )
    assert pending == ["nginx:1.27"]


def test_second_check_keeps_the_flag_while_the_container_is_old():
    running = "nginx:1.27|sha256:old\n"
    pending = pending_registry_images(
        ["nginx:1.27"],
        {"nginx:1.27": "sha256:new"},
        {"nginx:1.27": "sha256:new"},
        running,
    )
    assert pending == ["nginx:1.27"]


def test_flag_clears_when_the_running_container_matches():
    running = "docker.io/library/nginx:1.27|sha256:new\n"
    pending = pending_registry_images(
        ["nginx:1.27"],
        {"nginx:1.27": "sha256:new"},
        {"nginx:1.27": "sha256:new"},
        running,
    )
    assert pending == []


def test_one_old_replica_keeps_the_flag():
    running = "nginx:1.27|sha256:new\nnginx:1.27|sha256:old\n"
    pending = pending_registry_images(
        ["nginx:1.27"],
        {"nginx:1.27": "sha256:new"},
        {"nginx:1.27": "sha256:new"},
        running,
    )
    assert pending == ["nginx:1.27"]


def test_other_image_does_not_flag_this_one():
    running = "redis:7|sha256:other\n"
    pending = pending_registry_images(
        ["nginx:1.27"],
        {"nginx:1.27": "sha256:same"},
        {"nginx:1.27": "sha256:same"},
        running,
    )
    assert pending == []
