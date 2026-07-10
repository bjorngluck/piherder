"""Minimal Markdown → HTML for trusted in-repo docs (e.g. docs/API.md).

No external dependency. Supports headings, paragraphs, fenced code, tables,
lists, bold/code, and links. Not a full CommonMark implementation.
"""
from __future__ import annotations

import html
import re
from pathlib import Path


def load_repo_markdown(relative_path: str) -> str:
    """Load a markdown file from the app working directory / image root."""
    # Prefer /app/docs in container; also allow repo-relative paths
    candidates = [
        Path(relative_path),
        Path("/app") / relative_path,
        Path(__file__).resolve().parents[2] / relative_path,
    ]
    for p in candidates:
        try:
            if p.is_file():
                return p.read_text(encoding="utf-8")
        except OSError:
            continue
    return f"# Document missing\n\nCould not load `{relative_path}`.\n"


def _inline(text: str) -> str:
    """Escape then apply limited inline markdown."""
    s = html.escape(text)
    # links [text](url)
    s = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2" class="text-primary hover:underline" target="_blank" rel="noopener">\1</a>',
        s,
    )
    # bold **text**
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    # inline code
    s = re.sub(
        r"`([^`]+)`",
        r'<code class="font-mono text-[0.85em] bg-surface border border-border rounded px-1">\1</code>',
        s,
    )
    return s


def markdown_to_html(md: str) -> str:
    if not md or not md.strip():
        return "<p class=\"text-muted\">(empty document)</p>"

    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    in_code = False
    code_lang = ""
    code_buf: list[str] = []
    table_buf: list[str] = []
    list_buf: list[str] = []
    list_ordered = False

    def flush_list() -> None:
        nonlocal list_buf, list_ordered
        if not list_buf:
            return
        tag = "ol" if list_ordered else "ul"
        out.append(f'<{tag} class="list-{"decimal" if list_ordered else "disc"} pl-5 my-2 space-y-1 text-sm">')
        for item in list_buf:
            out.append(f"<li>{_inline(item)}</li>")
        out.append(f"</{tag}>")
        list_buf = []

    def flush_table() -> None:
        nonlocal table_buf
        if not table_buf:
            return
        rows = []
        for row in table_buf:
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            rows.append(cells)
        if len(rows) < 2:
            table_buf = []
            return
        # skip separator row |---|---|
        header = rows[0]
        body = []
        for r in rows[1:]:
            if all(re.match(r"^:?-+:?$", c or "") for c in r):
                continue
            body.append(r)
        out.append('<div class="overflow-x-auto my-3">')
        out.append('<table class="w-full text-sm border border-border rounded">')
        out.append("<thead><tr>")
        for c in header:
            out.append(
                f'<th class="text-left text-xs text-muted border-b border-border px-2 py-1.5">{_inline(c)}</th>'
            )
        out.append("</tr></thead><tbody>")
        for r in body:
            out.append("<tr class=\"border-b border-border/60\">")
            for j, c in enumerate(r):
                out.append(f'<td class="px-2 py-1.5 align-top">{_inline(c)}</td>')
            out.append("</tr>")
        out.append("</tbody></table></div>")
        table_buf = []

    while i < len(lines):
        line = lines[i]

        if in_code:
            if line.strip().startswith("```"):
                code = html.escape("\n".join(code_buf))
                out.append(
                    f'<pre class="my-3 p-3 bg-surface border border-border rounded overflow-x-auto '
                    f'font-mono text-[11px] leading-relaxed"><code>{code}</code></pre>'
                )
                in_code = False
                code_buf = []
                code_lang = ""
            else:
                code_buf.append(line)
            i += 1
            continue

        if line.strip().startswith("```"):
            flush_list()
            flush_table()
            in_code = True
            code_lang = line.strip()[3:].strip()
            code_buf = []
            i += 1
            continue

        # table lines
        if "|" in line and line.strip().startswith("|"):
            flush_list()
            table_buf.append(line)
            i += 1
            continue
        else:
            flush_table()

        # lists
        m_ul = re.match(r"^[-*]\s+(.+)$", line)
        m_ol = re.match(r"^\d+\.\s+(.+)$", line)
        if m_ul:
            if list_buf and list_ordered:
                flush_list()
            list_ordered = False
            list_buf.append(m_ul.group(1))
            i += 1
            continue
        if m_ol:
            if list_buf and not list_ordered:
                flush_list()
            list_ordered = True
            list_buf.append(m_ol.group(1))
            i += 1
            continue
        flush_list()

        if not line.strip():
            i += 1
            continue

        if line.startswith("#### "):
            out.append(f'<h4 class="text-sm font-semibold text-text mt-4 mb-1">{_inline(line[5:])}</h4>')
        elif line.startswith("### "):
            out.append(f'<h3 class="text-base font-semibold text-text mt-5 mb-2">{_inline(line[4:])}</h3>')
        elif line.startswith("## "):
            out.append(
                f'<h2 class="text-lg font-semibold text-text mt-6 mb-2 pb-1 border-b border-border">'
                f"{_inline(line[3:])}</h2>"
            )
        elif line.startswith("# "):
            out.append(f'<h1 class="text-xl font-semibold text-text mt-2 mb-3">{_inline(line[2:])}</h1>')
        elif re.match(r"^---+$", line.strip()):
            out.append('<hr class="my-4 border-border">')
        else:
            out.append(f'<p class="text-sm text-text my-2 leading-relaxed">{_inline(line)}</p>')
        i += 1

    flush_list()
    flush_table()
    if in_code and code_buf:
        code = html.escape("\n".join(code_buf))
        out.append(
            f'<pre class="my-3 p-3 bg-surface border border-border rounded overflow-x-auto '
            f'font-mono text-[11px] leading-relaxed"><code>{code}</code></pre>'
        )

    return "\n".join(out)
