"""Template catalog: builtin dirs + DB registry."""
from __future__ import annotations

import io
import json
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from ...config import settings
from ...database import engine
from ...models import ServiceTemplate
from .schema import (
    TemplateDefinition,
    TemplateError,
    definition_from_storage_json,
    definition_to_storage_json,
    load_template_dir,
)

logger = logging.getLogger(__name__)

# Sources that ship with PiHerder (disk seed / release refresh).
_OOTB_SOURCES = frozenset({"builtin", "starter"})


def source_badge(source: Optional[str]) -> Dict[str, str]:
    """Map catalog ``source`` to a short UI badge (E11 partial).

    Returns keys: kind (``ootb`` | ``user``), label, title, cls (status-pill class).
    """
    s = (source or "user").strip().lower()
    if s in _OOTB_SOURCES:
        return {
            "kind": "ootb",
            "label": "OOTB",
            "title": "Included with PiHerder — refreshed from disk while still marked builtin",
            "cls": "status-running",
        }
    if s == "git":
        return {
            "kind": "user",
            "label": "Git",
            "title": "Synced from a git catalog (operator-owned)",
            "cls": "status-running",
        }
    if s == "import":
        return {
            "kind": "user",
            "label": "Imported",
            "title": "Imported zip — editable; not overwritten by disk starters",
            "cls": "feature-off",
        }
    # user, custom, unknown → operator-owned
    return {
        "kind": "user",
        "label": "Yours",
        "title": "Operator-owned — never overwritten by built-in pack refresh",
        "cls": "feature-off",
    }


def is_ootb_source(source: Optional[str]) -> bool:
    return (source or "").strip().lower() in _OOTB_SOURCES


def builtin_templates_root() -> Path:
    """Repo-shipped templates (next to app package)."""
    # app/services/service_templates/catalog.py → repo root service_templates/
    here = Path(__file__).resolve()
    # Prefer package-relative then cwd
    candidates = [
        here.parents[3] / "service_templates",  # repo root when app is under repo
        Path.cwd() / "service_templates",
        Path("/app/service_templates"),
    ]
    for c in candidates:
        if c.is_dir():
            return c
    # default path even if missing (tests may create)
    return candidates[0]


def imported_templates_root() -> Path:
    root = Path(settings.DATA_ROOT or "/data") / "service_templates" / "imported"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _iter_builtin_dirs() -> List[Path]:
    root = builtin_templates_root()
    if not root.is_dir():
        return []
    out = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (
            (child / "template.yaml").is_file()
            or (child / "template.yml").is_file()
            or (child / "template.json").is_file()
        ):
            out.append(child)
    return out


def load_all_builtin() -> List[TemplateDefinition]:
    defs: List[TemplateDefinition] = []
    for d in _iter_builtin_dirs():
        try:
            defs.append(load_template_dir(d, source="builtin"))
        except Exception as e:
            logger.warning("Skip builtin template %s: %s", d, e)
    return defs


def ensure_builtin_templates_in_db(session: Optional[Session] = None) -> int:
    """Insert missing starter templates from disk; refresh untouched builtins.

    - Missing slug → insert from disk.
    - Existing row with ``source=builtin`` and a different checksum → update from
      disk (starters evolve with PiHerder releases).
    - ``source=user`` / import / git rows are never overwritten (operator owns them).

    Returns count of newly inserted or refreshed rows.
    """
    close = False
    if session is None:
        session = Session(engine)
        close = True
    n = 0
    try:
        for definition in load_all_builtin():
            row = session.exec(
                select(ServiceTemplate).where(ServiceTemplate.slug == definition.slug)
            ).first()
            checksum = definition.checksum()
            payload = definition_to_storage_json(definition)
            if row is None:
                row = ServiceTemplate(
                    slug=definition.slug,
                    name=definition.name,
                    description=definition.description,
                    category=definition.category,
                    version=definition.version,
                    source="builtin",
                    enabled=True,
                    definition_json=payload,
                    checksum=checksum,
                )
                session.add(row)
                n += 1
                continue
            # Refresh only pure starters still marked builtin
            if (row.source or "") == "builtin" and (row.checksum or "") != checksum:
                row.name = definition.name
                row.description = definition.description
                row.category = definition.category
                row.version = definition.version
                row.definition_json = payload
                row.checksum = checksum
                session.add(row)
                n += 1
        if n:
            session.commit()
    finally:
        if close:
            session.close()
    return n


def list_catalog(session: Session, *, include_disabled: bool = False) -> List[Dict[str, Any]]:
    ensure_builtin_templates_in_db(session)
    q = select(ServiceTemplate).order_by(ServiceTemplate.category, ServiceTemplate.name)
    rows = session.exec(q).all()
    items = []
    for r in rows:
        if not include_disabled and not r.enabled:
            continue
        var_count = 0
        secret_count = 0
        try:
            if r.definition_json:
                d = definition_from_storage_json(r.definition_json, source=r.source or "user")
                var_count = len(d.variables)
                secret_count = len(d.secret_var_names())
        except Exception:
            pass
        badge = source_badge(r.source)
        items.append(
            {
                "id": r.id,
                "slug": r.slug,
                "name": r.name,
                "description": r.description or "",
                "category": r.category or "other",
                "version": r.version or "",
                "source": r.source,
                "source_kind": badge["kind"],
                "source_label": badge["label"],
                "source_title": badge["title"],
                "source_cls": badge["cls"],
                "enabled": r.enabled,
                "checksum": r.checksum or "",
                "var_count": var_count,
                "secret_count": secret_count,
            }
        )
    # OOTB first, then operator-owned; keep category/name as secondary keys
    items.sort(
        key=lambda t: (
            0 if t.get("source_kind") == "ootb" else 1,
            (t.get("category") or "").lower(),
            (t.get("name") or "").lower(),
        )
    )
    return items


def get_template_row(session: Session, *, slug: Optional[str] = None, template_id: Optional[int] = None) -> Optional[ServiceTemplate]:
    if template_id is not None:
        return session.get(ServiceTemplate, template_id)
    if slug:
        return session.exec(select(ServiceTemplate).where(ServiceTemplate.slug == slug)).first()
    return None


def get_template_definition(
    session: Session,
    *,
    slug: Optional[str] = None,
    template_id: Optional[int] = None,
    allow_disabled: bool = False,
) -> TemplateDefinition:
    ensure_builtin_templates_in_db(session)
    row = get_template_row(session, slug=slug, template_id=template_id)
    if not row:
        raise TemplateError("Template not found")
    if not row.enabled and not allow_disabled:
        raise TemplateError("Template is disabled")
    if not row.definition_json:
        raise TemplateError("Template has no definition")
    return definition_from_storage_json(row.definition_json, source=row.source or "user")


def save_template_definition(
    session: Session,
    definition: TemplateDefinition,
    *,
    template_id: Optional[int] = None,
    mark_user: bool = True,
) -> ServiceTemplate:
    """Create or update a catalog row from a full definition.

    When mark_user is True (default for UI saves), source becomes ``user`` so
    disk starters never overwrite the row.
    """
    payload = definition_to_storage_json(definition)
    checksum = definition.checksum()
    source = "user" if mark_user else (definition.source or "user")

    row: Optional[ServiceTemplate] = None
    if template_id is not None:
        row = session.get(ServiceTemplate, template_id)
        if not row:
            raise TemplateError("Template not found")
        # Slug change: ensure unique
        if row.slug != definition.slug:
            clash = session.exec(
                select(ServiceTemplate).where(ServiceTemplate.slug == definition.slug)
            ).first()
            if clash and clash.id != row.id:
                raise TemplateError(f"Slug {definition.slug!r} already exists")
    else:
        row = session.exec(
            select(ServiceTemplate).where(ServiceTemplate.slug == definition.slug)
        ).first()

    if row is None:
        row = ServiceTemplate(
            slug=definition.slug,
            name=definition.name,
            description=definition.description,
            category=definition.category,
            version=definition.version,
            source=source,
            enabled=True,
            definition_json=payload,
            checksum=checksum,
        )
    else:
        row.slug = definition.slug
        row.name = definition.name
        row.description = definition.description
        row.category = definition.category
        row.version = definition.version
        row.source = source
        row.definition_json = payload
        row.checksum = checksum
        row.enabled = True
        from datetime import datetime

        row.updated_at = datetime.utcnow()

    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def delete_template(session: Session, *, slug: str) -> None:
    row = get_template_row(session, slug=slug)
    if not row:
        raise TemplateError("Template not found")
    session.delete(row)
    session.commit()


def import_template_from_dir(
    session: Session,
    root: Path,
    *,
    source: str = "import",
) -> ServiceTemplate:
    definition = load_template_dir(root, source=source)
    # Imports are operator-owned; treat as user so they stay editable and stable
    definition.source = "user" if source in ("import", "user") else source
    return save_template_definition(session, definition, mark_user=True)


def import_template_from_zip_bytes(session: Session, data: bytes) -> ServiceTemplate:
    """Import a zip containing template.yaml + files/."""
    if not data or len(data) > 5 * 1024 * 1024:
        raise TemplateError("Zip must be non-empty and ≤ 5 MiB")
    with tempfile.TemporaryDirectory(prefix="piherder-tpl-") as tmp:
        tmp_path = Path(tmp)
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                zf.extractall(tmp_path)
        except zipfile.BadZipFile as e:
            raise TemplateError("Invalid zip archive") from e
        # Find directory that contains template.yaml
        roots = list(tmp_path.rglob("template.yaml")) + list(tmp_path.rglob("template.yml"))
        if not roots:
            roots = list(tmp_path.rglob("template.json"))
        if not roots:
            raise TemplateError("Zip must contain template.yaml")
        # Prefer shallowest
        roots.sort(key=lambda p: len(p.parts))
        root = roots[0].parent
        # Copy into DATA_ROOT for reference
        dest = imported_templates_root() / roots[0].parent.name
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(root, dest)
        return import_template_from_dir(session, dest, source="import")
