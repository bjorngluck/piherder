"""Render Docker unused-images/containers HTML for the cleanup modal."""
from __future__ import annotations

import html as html_lib
from typing import Any, Mapping


def render_unused_list_html(data: Mapping[str, Any] | None) -> str:
    """Build HTML for fetch().innerHTML from list_unused_images_and_containers().

    Host-derived names are escaped so compromised image/container labels cannot inject markup.
    """
    data = data or {}

    def _esc(s: object) -> str:
        return html_lib.escape(str(s if s is not None else ""), quote=True)

    lines: list[str] = []
    di = list(data.get("dangling_images", []) or [])
    ec = list(data.get("exited_containers", []) or [])
    if not di and not ec:
        lines.append(
            "<div class='text-muted'>No dangling images or exited containers found.</div>"
        )
    else:
        if di:
            lines.append(
                f"<div class='text-warning font-medium mb-0.5'>Dangling images "
                f"<span class='text-muted font-normal'>({len(di)})</span></div>"
            )
            lines.append(
                "<pre class='whitespace-pre-wrap text-[10px] mb-2'>"
                + "\n".join(_esc(x) for x in di)
                + "</pre>"
            )
        if ec:
            lines.append(
                f"<div class='text-warning font-medium mb-0.5 mt-1'>Exited containers "
                f"<span class='text-muted font-normal'>({len(ec)})</span></div>"
            )
            lines.append(
                "<pre class='whitespace-pre-wrap text-[10px]'>"
                + "\n".join(_esc(x) for x in ec)
                + "</pre>"
            )
    if data.get("errors"):
        lines.append(
            "<div class='text-danger mt-1'>Errors: "
            + "; ".join(_esc(e) for e in data["errors"])
            + "</div>"
        )
    if data.get("success") is False:
        lines.append(
            "<div class='text-xs text-muted mt-1'>"
            "Command may have partially failed (non-zero exit).</div>"
        )
    return "\n".join(lines)
