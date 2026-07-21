"""Container topology annotations: category, tags, visual stacks, order.

Operator-defined presentation only — does not change compose deploy boundaries.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from ..models import (
    ContainerAnnotation,
    ContainerAnnotationTag,
    TopologyCategory,
    TopologyTag,
    VisualServiceStack,
)

# System seeds (also in migration). ensure_vocab re-seeds if empty.
_SEED_CATEGORIES: tuple[tuple[str, str, int], ...] = (
    ("edge", "Edge", 0),
    ("app", "App", 1),
    ("queue", "Queue", 2),
    ("cache", "Cache", 3),
    ("data", "Data", 4),
    ("tooling", "Tooling", 5),
)

_SEED_TAGS: tuple[tuple[str, str, int], ...] = (
    ("web", "Web", 0),
    ("db", "DB", 1),
    ("worker", "Worker", 2),
    ("proxy", "Proxy", 3),
    ("cache", "Cache", 4),
    ("queue", "Queue", 5),
    ("edge", "Edge", 6),
    ("test", "Test", 7),
    ("other", "Other", 8),
)

_ROLE_LABELS = {
    "edge": "edge",
    "app": "app",
    "queue": "queue",
    "data": "db",
    "cache": "cache",
    "tooling": "tool",
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return (s or "stack")[:80]


def normalize_project(project: str | None) -> str:
    """Stable compose-project key for annotation storage (case-insensitive).

    Storage key only — does not invent deploys. View groups live *inside*
    one project; exact keys avoid colliding service names across projects.
    """
    return (project or "").strip().lower()


def ensure_vocab_seeded(session: Session) -> None:
    """Idempotent seed when tables exist but are empty (e.g. unit tests)."""
    n_cat = session.exec(select(TopologyCategory).limit(1)).first()
    if not n_cat:
        now = datetime.utcnow()
        for key, label, so in _SEED_CATEGORIES:
            session.add(
                TopologyCategory(
                    key=key,
                    label=label,
                    sort_order=so,
                    enabled=True,
                    is_system=True,
                    created_at=now,
                    updated_at=now,
                )
            )
    n_tag = session.exec(select(TopologyTag).limit(1)).first()
    if not n_tag:
        now = datetime.utcnow()
        for key, label, so in _SEED_TAGS:
            session.add(
                TopologyTag(
                    key=key,
                    label=label,
                    sort_order=so,
                    enabled=True,
                    is_system=True,
                    created_at=now,
                    updated_at=now,
                )
            )
    session.commit()


def list_categories(
    session: Session, *, enabled_only: bool = True
) -> list[dict[str, Any]]:
    ensure_vocab_seeded(session)
    q = select(TopologyCategory).order_by(
        TopologyCategory.sort_order, TopologyCategory.key
    )
    rows = list(session.exec(q).all())
    if enabled_only:
        rows = [r for r in rows if r.enabled]
    return [
        {
            "key": r.key,
            "label": r.label,
            "sort_order": r.sort_order,
            "enabled": r.enabled,
            "is_system": r.is_system,
            "color_token": r.color_token,
        }
        for r in rows
    ]


def list_tags(session: Session, *, enabled_only: bool = True) -> list[dict[str, Any]]:
    ensure_vocab_seeded(session)
    q = select(TopologyTag).order_by(TopologyTag.sort_order, TopologyTag.key)
    rows = list(session.exec(q).all())
    if enabled_only:
        rows = [r for r in rows if r.enabled]
    return [
        {
            "key": r.key,
            "label": r.label,
            "sort_order": r.sort_order,
            "enabled": r.enabled,
            "is_system": r.is_system,
        }
        for r in rows
    ]


def category_keys(session: Session) -> set[str]:
    return {c["key"] for c in list_categories(session, enabled_only=True)}


def tag_keys(session: Session) -> set[str]:
    return {t["key"] for t in list_tags(session, enabled_only=True)}


def add_category(
    session: Session, *, key: str, label: str, sort_order: int | None = None
) -> TopologyCategory:
    ensure_vocab_seeded(session)
    k = slugify(key)
    if not k:
        raise ValueError("invalid category key")
    existing = session.exec(
        select(TopologyCategory).where(TopologyCategory.key == k)
    ).first()
    if existing:
        raise ValueError(f"category already exists: {k}")
    if sort_order is None:
        rows = list(session.exec(select(TopologyCategory)).all())
        sort_order = (max((r.sort_order for r in rows), default=-1) + 1)
    row = TopologyCategory(
        key=k,
        label=(label or k).strip()[:80],
        sort_order=int(sort_order),
        enabled=True,
        is_system=False,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def add_tag(
    session: Session, *, key: str, label: str, sort_order: int | None = None
) -> TopologyTag:
    ensure_vocab_seeded(session)
    k = slugify(key)
    if not k:
        raise ValueError("invalid tag key")
    existing = session.exec(select(TopologyTag).where(TopologyTag.key == k)).first()
    if existing:
        raise ValueError(f"tag already exists: {k}")
    if sort_order is None:
        rows = list(session.exec(select(TopologyTag)).all())
        sort_order = (max((r.sort_order for r in rows), default=-1) + 1)
    row = TopologyTag(
        key=k,
        label=(label or k).strip()[:80],
        sort_order=int(sort_order),
        enabled=True,
        is_system=False,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def set_vocab_enabled(
    session: Session, *, kind: str, key: str, enabled: bool
) -> None:
    """kind: category | tag."""
    k = (key or "").strip().lower()
    if kind == "category":
        row = session.exec(
            select(TopologyCategory).where(TopologyCategory.key == k)
        ).first()
    elif kind == "tag":
        row = session.exec(select(TopologyTag).where(TopologyTag.key == k)).first()
    else:
        raise ValueError("kind must be category or tag")
    if not row:
        raise ValueError("not found")
    row.enabled = bool(enabled)
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()


# ── Visual service stacks ────────────────────────────────────────────────────


def list_visual_stacks(
    session: Session, *, server_id: int, project: str
) -> list[dict[str, Any]]:
    """List visual stacks for a compose project; always include implicit Main."""
    proj = normalize_project(project)
    all_rows = list(
        session.exec(
            select(VisualServiceStack)
            .where(VisualServiceStack.server_id == int(server_id))
            .order_by(VisualServiceStack.sort_order, VisualServiceStack.id)
        ).all()
    )
    rows = [r for r in all_rows if normalize_project(r.compose_project) == proj]
    out: list[dict[str, Any]] = [
        {
            "id": None,
            "name": "Main",
            "slug": "main",
            "is_default": True,
            "sort_order": -1,
            "implicit": True,
        }
    ]
    for r in rows:
        out.append(
            {
                "id": r.id,
                "name": r.name,
                "slug": r.slug,
                "is_default": bool(r.is_default),
                "sort_order": r.sort_order,
                "implicit": False,
            }
        )
    return out


def create_visual_stack(
    session: Session,
    *,
    server_id: int,
    project: str,
    name: str,
    slug: str | None = None,
) -> VisualServiceStack:
    proj = normalize_project(project)
    if not proj:
        raise ValueError("project required")
    nm = (name or "").strip()[:120]
    if not nm:
        raise ValueError("name required")
    sl = slugify(slug or nm)
    if sl == "main":
        sl = "stack-" + sl
    existing = None
    for r in session.exec(
        select(VisualServiceStack)
        .where(VisualServiceStack.server_id == int(server_id))
        .where(VisualServiceStack.slug == sl)
    ).all():
        if normalize_project(r.compose_project) == proj:
            existing = r
            break
    if existing:
        raise ValueError(f"view group slug exists: {sl}")
    rows = [
        r
        for r in session.exec(
            select(VisualServiceStack).where(
                VisualServiceStack.server_id == int(server_id)
            )
        ).all()
        if normalize_project(r.compose_project) == proj
    ]
    so = max((r.sort_order for r in rows), default=-1) + 1
    row = VisualServiceStack(
        server_id=int(server_id),
        compose_project=proj,
        name=nm,
        slug=sl,
        is_default=False,
        sort_order=so,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def delete_visual_stack(session: Session, *, stack_id: int) -> None:
    row = session.get(VisualServiceStack, int(stack_id))
    if not row:
        raise ValueError("not found")
    # Move containers back to Main (null)
    anns = list(
        session.exec(
            select(ContainerAnnotation).where(
                ContainerAnnotation.visual_stack_id == int(stack_id)
            )
        ).all()
    )
    for a in anns:
        a.visual_stack_id = None
        a.updated_at = datetime.utcnow()
        session.add(a)
    session.delete(row)
    session.commit()


def rename_visual_stack(
    session: Session, *, stack_id: int, name: str
) -> VisualServiceStack:
    row = session.get(VisualServiceStack, int(stack_id))
    if not row:
        raise ValueError("not found")
    nm = (name or "").strip()[:120]
    if not nm:
        raise ValueError("name required")
    row.name = nm
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# ── Annotations ──────────────────────────────────────────────────────────────


def _ann_key(server_id: int, project: str, container_key: str) -> tuple[int, str, str]:
    return (
        int(server_id),
        normalize_project(project),
        (container_key or "").strip(),
    )


def get_annotation(
    session: Session, *, server_id: int, project: str, container_key: str
) -> Optional[ContainerAnnotation]:
    sid, proj, ckey = _ann_key(server_id, project, container_key)
    if not proj or not ckey:
        return None
    # Prefer exact project match; also accept legacy mixed-case project rows
    rows = list(
        session.exec(
            select(ContainerAnnotation)
            .where(ContainerAnnotation.server_id == sid)
            .where(ContainerAnnotation.container_key == ckey)
        ).all()
    )
    for r in rows:
        if normalize_project(r.compose_project) == proj:
            return r
    return None


def load_annotations_map(
    session: Session, *, server_id: int, project: str
) -> dict[str, dict[str, Any]]:
    """container_key → annotation dict with tags (scoped to one compose project)."""
    proj = normalize_project(project)
    if not proj:
        return {}
    all_rows = list(
        session.exec(
            select(ContainerAnnotation).where(
                ContainerAnnotation.server_id == int(server_id)
            )
        ).all()
    )
    rows = [r for r in all_rows if normalize_project(r.compose_project) == proj]
    if not rows:
        return {}
    ids = [r.id for r in rows if r.id is not None]
    tag_rows: list[ContainerAnnotationTag] = []
    if ids:
        tag_rows = list(
            session.exec(
                select(ContainerAnnotationTag).where(
                    ContainerAnnotationTag.annotation_id.in_(ids)  # type: ignore[attr-defined]
                )
            ).all()
        )
    tags_by_ann: dict[int, list[str]] = {}
    for t in tag_rows:
        tags_by_ann.setdefault(int(t.annotation_id), []).append(t.tag_key)

    # Visual stack names for chips (same project only)
    vs_ids = {r.visual_stack_id for r in rows if r.visual_stack_id}
    vs_names: dict[int, str] = {}
    if vs_ids:
        for vs in session.exec(
            select(VisualServiceStack).where(VisualServiceStack.id.in_(vs_ids))  # type: ignore[attr-defined]
        ).all():
            if vs.id is not None:
                vs_names[int(vs.id)] = vs.name

    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        payload = {
            "id": r.id,
            "category_key": r.category_key,
            "visual_stack_id": r.visual_stack_id,
            "visual_stack_name": (
                vs_names.get(int(r.visual_stack_id))
                if r.visual_stack_id is not None
                else "Main"
            ),
            "sort_index": r.sort_index,
            "notes": r.notes,
            "tags": sorted(tags_by_ann.get(int(r.id or 0), [])),
            "compose_project": normalize_project(r.compose_project),
        }
        # Index by stored key and lowercase for lookup
        out[r.container_key] = payload
        out[r.container_key.lower()] = payload
    return out


def _get_or_create_annotation(
    session: Session, *, server_id: int, project: str, container_key: str
) -> ContainerAnnotation:
    sid, proj, ckey = _ann_key(server_id, project, container_key)
    if not proj or not ckey:
        raise ValueError("project and container_key required")
    row = get_annotation(
        session, server_id=sid, project=proj, container_key=ckey
    )
    if row:
        # Normalize legacy mixed-case project keys
        if row.compose_project != proj:
            row.compose_project = proj
            session.add(row)
        return row
    row = ContainerAnnotation(
        server_id=sid,
        compose_project=proj,
        container_key=ckey,
    )
    session.add(row)
    session.flush()
    return row


def set_annotation(
    session: Session,
    *,
    server_id: int,
    project: str,
    container_key: str,
    category_key: str | None = ...,  # type: ignore[assignment]
    tags: list[str] | None = None,
    visual_stack_id: int | None = ...,  # type: ignore[assignment]
    sort_index: int | None = ...,  # type: ignore[assignment]
    notes: str | None = ...,  # type: ignore[assignment]
    clear_category: bool = False,
    clear_visual_stack: bool = False,
) -> dict[str, Any]:
    """Upsert annotation fields. Use ellipsis default to leave field unchanged.

    ``clear_category`` / ``clear_visual_stack`` reset to heuristic / Main.
    ``tags`` when provided replaces the full tag set (must be enabled vocab keys).
    """
    ensure_vocab_seeded(session)
    row = _get_or_create_annotation(
        session,
        server_id=server_id,
        project=project,
        container_key=container_key,
    )
    if clear_category:
        row.category_key = None
    elif category_key is not ...:
        ck = (category_key or "").strip().lower() or None
        if ck is not None and ck not in category_keys(session):
            raise ValueError(f"unknown or disabled category: {ck}")
        row.category_key = ck

    if clear_visual_stack:
        row.visual_stack_id = None
    elif visual_stack_id is not ...:
        if visual_stack_id is None:
            row.visual_stack_id = None
        else:
            vs = session.get(VisualServiceStack, int(visual_stack_id))
            if not vs:
                raise ValueError("view group not found")
            if int(vs.server_id) != int(server_id) or normalize_project(
                vs.compose_project
            ) != normalize_project(project):
                raise ValueError(
                    "view group must belong to the same compose project "
                    f"(got {vs.compose_project!r}, need {project!r})"
                )
            row.visual_stack_id = int(visual_stack_id)

    if sort_index is not ...:
        row.sort_index = sort_index

    if notes is not ...:
        row.notes = (notes or "").strip()[:500] or None

    if tags is not None:
        allowed = tag_keys(session)
        clean: list[str] = []
        seen: set[str] = set()
        for t in tags:
            tk = (t or "").strip().lower()
            if not tk or tk in seen:
                continue
            if tk not in allowed:
                raise ValueError(f"unknown or disabled tag: {tk}")
            seen.add(tk)
            clean.append(tk)
        # Replace tags
        existing = list(
            session.exec(
                select(ContainerAnnotationTag).where(
                    ContainerAnnotationTag.annotation_id == row.id
                )
            ).all()
        )
        for e in existing:
            session.delete(e)
        session.flush()
        for tk in clean:
            session.add(
                ContainerAnnotationTag(annotation_id=int(row.id), tag_key=tk)
            )

    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    tag_list = [
        t.tag_key
        for t in session.exec(
            select(ContainerAnnotationTag).where(
                ContainerAnnotationTag.annotation_id == row.id
            )
        ).all()
    ]
    return {
        "id": row.id,
        "server_id": row.server_id,
        "compose_project": row.compose_project,
        "container_key": row.container_key,
        "category_key": row.category_key,
        "visual_stack_id": row.visual_stack_id,
        "sort_index": row.sort_index,
        "notes": row.notes,
        "tags": sorted(tag_list),
    }


def set_order_via_annotations(
    session: Session,
    *,
    server_id: int,
    project: str,
    names: list[str],
    merge: bool = False,
) -> list[str]:
    """Persist ordered container keys as annotation.sort_index (0..n-1).

    ``merge=False`` (default, All view): replace project order — listed names
    get 0..n-1; other containers in this project lose sort_index.

    ``merge=True`` (Main / named view-group reorder): splice the new relative
    order of the submitted names into the existing project order *in place*
    (first occurrence of any submitted name). Sibling view groups keep their
    relative order and position — reordering e2e does not append e2e after
    Main or wipe Main indices.
    """
    clean: list[str] = []
    seen: set[str] = set()
    for n in names or []:
        s = str(n or "").strip()
        if not s:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        clean.append(s)
    proj = normalize_project(project)
    sid = int(server_id)
    now = datetime.utcnow()
    keep = {n.lower() for n in clean}
    by_key: dict[str, ContainerAnnotation] = {}
    for r in session.exec(
        select(ContainerAnnotation).where(ContainerAnnotation.server_id == sid)
    ).all():
        if normalize_project(r.compose_project) != proj:
            continue
        by_key[r.container_key.lower()] = r

    existing = order_from_annotations(session, server_id=sid, project=proj)

    if merge and clean:
        # In-place splice: keep untouched names, replace the touched block once
        result: list[str] = []
        emitted = False
        for n in existing:
            if n.lower() in keep:
                if not emitted:
                    result.extend(clean)
                    emitted = True
            else:
                result.append(n)
        if not emitted:
            # First order for this subset — append after existing project order
            result.extend(clean)
        final_names = result
    else:
        final_names = list(clean)

    final_keep = {n.lower() for n in final_names}
    for i, name in enumerate(final_names):
        row = by_key.get(name.lower())
        if not row:
            row = ContainerAnnotation(
                server_id=sid,
                compose_project=proj,
                container_key=name,
                sort_index=i,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            by_key[name.lower()] = row
        else:
            row.sort_index = i
            row.compose_project = proj
            row.updated_at = now
            session.add(row)

    if not merge:
        for r in by_key.values():
            if r.container_key.lower() not in final_keep and r.sort_index is not None:
                r.sort_index = None
                r.updated_at = now
                session.add(r)
    else:
        # Merge must not leave stale indices on names removed from final_names
        # (should not happen with splice) — only clear if they were in the
        # submitted set but dropped as duplicates (already handled by clean).
        pass

    session.commit()
    return final_names if merge else clean


def order_from_annotations(
    session: Session, *, server_id: int, project: str
) -> list[str]:
    """Ordered container keys that have sort_index set."""
    proj = normalize_project(project)
    rows = [
        r
        for r in session.exec(
            select(ContainerAnnotation)
            .where(ContainerAnnotation.server_id == int(server_id))
            .where(ContainerAnnotation.sort_index.is_not(None))  # type: ignore[union-attr]
        ).all()
        if normalize_project(r.compose_project) == proj
    ]
    rows.sort(key=lambda r: (r.sort_index if r.sort_index is not None else 9999, r.id or 0))
    return [r.container_key for r in rows]


def apply_annotations_to_containers(
    session: Session,
    containers: list[dict[str, Any]],
    *,
    server_id: int,
    project: str,
    visual_stack_id: int | None | str = "all",
    guess_role,
) -> list[dict[str, Any]]:
    """Merge DB annotations into container dicts; optionally filter visual stack.

    visual_stack_id:
      - ``"all"`` (default): no filter
      - ``None`` or ``0`` or ``"main"``: only Main (null assignment)
      - int: that visual stack only
    """
    ann_map = load_annotations_map(session, server_id=server_id, project=project)
    cats = {c["key"]: c for c in list_categories(session, enabled_only=False)}
    tag_labels = {t["key"]: t["label"] for t in list_tags(session, enabled_only=False)}

    # Filter target
    filter_mode = visual_stack_id
    filter_id: int | None = None
    if filter_mode == "all" or filter_mode is ...:
        filter_id = None
        do_filter = False
    elif filter_mode is None or filter_mode == 0 or filter_mode == "main" or filter_mode == "":
        filter_id = None
        do_filter = True
    else:
        try:
            filter_id = int(filter_mode)  # type: ignore[arg-type]
            do_filter = True
        except (TypeError, ValueError):
            filter_id = None
            do_filter = False

    out: list[dict[str, Any]] = []
    for c in containers:
        row = dict(c)
        ckey = (row.get("compose_service") or row.get("name") or "").strip()
        ann = ann_map.get(ckey) or ann_map.get(ckey.lower())
        # try case-insensitive
        if not ann:
            for k, v in ann_map.items():
                if k.lower() == ckey.lower():
                    ann = v
                    break

        heur = guess_role(
            name=row.get("name") or "",
            image=row.get("image") or "",
            compose_service=row.get("compose_service") or "",
        )
        cat = (ann or {}).get("category_key") if ann else None
        role = cat if cat else heur
        if role and role not in cats and role not in _ROLE_LABELS:
            # custom category may exist disabled; still show
            pass
        row["role"] = role or "app"
        row["role_label"] = (
            (cats.get(role) or {}).get("label")
            or _ROLE_LABELS.get(role or "", role or "app")
        ).lower() if role else "app"
        # Prefer short chip label from system map when present
        if role in _ROLE_LABELS and not cat:
            row["role_label"] = _ROLE_LABELS[role]
        elif cat and cats.get(cat):
            row["role_label"] = cats[cat]["label"].lower()
        row["category_key"] = cat
        row["category_is_override"] = bool(cat)
        row["tags"] = list((ann or {}).get("tags") or [])
        row["tag_labels"] = [tag_labels.get(t, t) for t in row["tags"]]
        row["visual_stack_id"] = (ann or {}).get("visual_stack_id")
        row["visual_stack_name"] = (ann or {}).get("visual_stack_name") or "Main"
        row["annotation_project"] = normalize_project(
            (ann or {}).get("compose_project") or project
        )
        row["notes"] = (ann or {}).get("notes")
        if ann and ann.get("sort_index") is not None:
            row["order_index"] = ann["sort_index"]
            row["custom_ordered"] = True

        if do_filter:
            vsid = row.get("visual_stack_id")
            if filter_id is None:
                # Main = unassigned only (null / missing visual_stack_id)
                if vsid is not None and vsid != 0 and str(vsid).lower() not in (
                    "main",
                    "none",
                    "",
                ):
                    continue
            else:
                try:
                    if int(vsid) != int(filter_id):
                        continue
                except (TypeError, ValueError):
                    continue

        out.append(row)
    return out
