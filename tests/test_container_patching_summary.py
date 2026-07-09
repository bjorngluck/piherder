from app.services.container_patching import (
    container_patch_succeeded,
    summarize_container_patch,
)


def test_container_patch_succeeded():
    assert container_patch_succeeded({"updated": ["a"], "failed": []})
    assert not container_patch_succeeded({"updated": [], "failed": ["x"]})
    assert not container_patch_succeeded({"error": "ssh"})


def test_summarize_container_patch():
    s = summarize_container_patch(
        {
            "projects_checked": ["a", "b"],
            "updated": ["a"],
            "failed": [],
        }
    )
    assert "2 project" in s
    assert "1 updated" in s
