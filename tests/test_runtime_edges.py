"""RuntimeEdge accept / dismiss / manual (topology P2–P3)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import runtime_edges as re


def test_edge_key_normalizes():
    a = re.edge_key(
        from_server_id=1,
        from_project="PiHerder",
        from_container="Web",
        to_server_id=1,
        to_project="piherder",
        to_container="web",
    )
    b = re.edge_key(
        from_server_id=1,
        from_project="piherder",
        from_container="web",
        to_server_id=1,
        to_project="piherder",
        to_container="web",
    )
    assert a == b


def test_partition_filters_dismissed_and_accepted():
    session = MagicMock()
    accepted = SimpleNamespace(
        id=1,
        from_server_id=5,
        from_project="piherder",
        from_container="web",
        to_server_id=5,
        to_project="piherder",
        to_container="db",
        kind="depends_on",
        source="accepted",
        confidence=85,
        note=None,
        dismissed_at=None,
    )
    dismissed = SimpleNamespace(
        id=2,
        from_server_id=5,
        from_project="piherder",
        from_container="web",
        to_server_id=5,
        to_project="piherder",
        to_container="redis",
        kind="depends_on",
        source="suggested",
        confidence=0,
        note=None,
        dismissed_at="yes",
    )

    def _exec(stmt):
        m = MagicMock()
        # RuntimeEdge query + Server query
        m.all.return_value = [accepted, dismissed]
        return m

    session.exec.side_effect = _exec

    with patch.object(re, "server_name_map", return_value={5: "rpi"}):
        # list_edges_for_project uses session.exec — return stored rows
        with patch.object(
            re,
            "list_edges_for_project",
            return_value=[accepted, dismissed],
        ):
            part = re.partition_for_panel(
                session,
                server_id=5,
                project="piherder",
                suggestions=[
                    {"from": "web", "to": "db", "kind": "depends_on", "source": "compose", "confidence": 85},
                    {"from": "web", "to": "redis", "kind": "depends_on", "source": "compose", "confidence": 85},
                    {"from": "worker", "to": "db", "kind": "depends_on", "source": "compose", "confidence": 85},
                ],
            )

    assert len(part["confirmed"]) == 1
    assert part["confirmed"][0]["from_container"] == "web"
    assert part["confirmed"][0]["to_container"] == "db"
    # redis dismissed, db accepted → only worker→db open
    open_pairs = {(s["from"], s["to"]) for s in part["suggested"]}
    assert ("web", "db") not in open_pairs
    assert ("web", "redis") not in open_pairs
    assert ("worker", "db") in open_pairs
    assert part["dismissed_count"] >= 1


def test_accept_creates_row():
    session = MagicMock()
    session.get.return_value = None

    with patch.object(re, "find_edge", return_value=None):
        # commit/refresh no-ops
        row = re.accept_suggestion(
            session,
            from_server_id=1,
            from_project="app",
            from_container="web",
            to_server_id=1,
            to_project="app",
            to_container="db",
            user_id=9,
        )
    assert session.add.called
    assert session.commit.called
    added = session.add.call_args[0][0]
    assert added.source == "accepted"
    assert added.from_container == "web"
    assert added.to_container == "db"


def test_dismiss_sets_timestamp():
    session = MagicMock()
    with patch.object(re, "find_edge", return_value=None):
        re.dismiss_suggestion(
            session,
            from_server_id=1,
            from_project="app",
            from_container="web",
            to_server_id=1,
            to_project="app",
            to_container="db",
        )
    added = session.add.call_args[0][0]
    assert added.dismissed_at is not None
    assert added.source == "suggested"


def test_manual_edge():
    session = MagicMock()
    with patch.object(re, "find_edge", return_value=None):
        re.create_manual_edge(
            session,
            from_server_id=2,
            from_project="npm",
            from_container=None,
            to_server_id=3,
            to_project="grafana",
            to_container="grafana",
            kind="talks_to",
            note="edge proxy",
            user_id=1,
        )
    added = session.add.call_args[0][0]
    assert added.source == "manual"
    assert added.from_server_id == 2
    assert added.to_server_id == 3
    assert added.kind == "talks_to"
