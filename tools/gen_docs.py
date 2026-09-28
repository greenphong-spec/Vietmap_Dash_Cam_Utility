"""Regenerate DashCamMerger-{Specification,Design}.html (and the Android app spec) from .md.

The .md files are the source of truth; the .html files are a browsable,
sidebar-navigable render of them for people who don't want to read raw
Markdown. Whenever the .md files change, re-run this to keep the .html in
sync:

    pip install markdown
    python tools\\gen_docs.py

Requires the third-party `markdown` package (dev-only tool; not a runtime
dependency of the app itself, which stays stdlib-only per the design doc).
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

import markdown

STYLE = """<style>
:root{--bg:#fff;--fg:#1f2328;--muted:#57606a;--line:#d0d7de;--code:#f6f8fa;--accent:#0969da;--th:#eef2f6}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#8b949e;--line:#30363d;--code:#161b22;--accent:#58a6ff;--th:#1c2128}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{display:flex;min-height:100vh}
nav{width:300px;flex:none;border-right:1px solid var(--line);padding:16px;position:sticky;top:0;height:100vh;overflow:auto;font-size:13px}
nav a{display:block;color:var(--fg);text-decoration:none;padding:2px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
nav a:hover{color:var(--accent)}
nav .l2{padding-left:0;font-weight:600;margin-top:6px}
nav .l3{padding-left:14px}
nav .l4{padding-left:28px;color:var(--muted)}
main{flex:1;min-width:0;padding:24px 40px 80px;max-width:1100px}
h1{font-size:28px;border-bottom:1px solid var(--line);padding-bottom:8px}
h2{font-size:22px;border-bottom:1px solid var(--line);padding-bottom:6px;margin-top:40px}
h3{font-size:18px;margin-top:28px}
h4{font-size:15px;margin-top:20px}
code{font-family:Consolas,"Cascadia Mono",Menlo,monospace;font-size:0.9em;background:var(--code);padding:1px 5px;border-radius:4px}
pre{background:var(--code);border:1px solid var(--line);border-radius:6px;padding:12px 14px;overflow:auto;line-height:1.45}
pre code{background:none;padding:0;font-size:12.5px}
table{border-collapse:collapse;margin:12px 0;font-size:13.5px;display:block;overflow-x:auto;max-width:100%}
th,td{border:1px solid var(--line);padding:5px 9px;vertical-align:top;text-align:left}
th{background:var(--th)}
hr{border:0;border-top:1px solid var(--line);margin:28px 0}
ul,ol{padding-left:26px}
li{margin:3px 0}
.meta{color:var(--muted)}
@media (max-width:900px){.wrap{display:block}nav{position:static;width:auto;height:auto;border-right:0;border-bottom:1px solid var(--line)}main{padding:16px}}
</style>"""

HEADING_RE = re.compile(r"^(#{1,4})\s+(.*\S)\s*$")


def raw_headings(md_text: str) -> list[tuple[int, str]]:
    """(level, raw heading text) in document order, skipping fenced code blocks."""
    out = []
    in_fence = False
    for line in md_text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(line)
        if m:
            out.append((len(m.group(1)), m.group(2)))
    return out


def flatten_toc(tokens) -> list[str]:
    ids = []
    for t in tokens:
        ids.append(t["id"])
        ids.extend(flatten_toc(t["children"]))
    return ids


def build(md_path: Path, html_path: Path) -> None:
    text = md_path.read_text(encoding="utf-8")
    headings = raw_headings(text)  # includes the H1

    md = markdown.Markdown(extensions=["tables", "fenced_code", "toc"])
    body = md.convert(text)
    ids = flatten_toc(md.toc_tokens)
    assert len(ids) == len(headings), (
        f"{md_path.name}: {len(headings)} raw headings vs {len(ids)} toc ids "
        "-- a heading was likely added/removed inside a fenced code block detector edge case"
    )

    title = headings[0][1]
    nav_parts = [
        f'<a class="l{level}" href="#{hid}">{html.escape(raw)}</a>'
        for (level, raw), hid in zip(headings, ids)
        if level != 1
    ]
    nav = "<nav><strong>Contents</strong>" + "\n".join(nav_parts) + "</nav>"

    out = (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>{STYLE}</head>\n"
        f'<body><div class="wrap">{nav}<main>{body}</main></div></body></html>'
    )
    html_path.write_text(out, encoding="utf-8", newline="\n")
    print(f"wrote {html_path} ({len(out)} bytes, {len(nav_parts)} nav entries)")


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    build(root / "DashCamMerger-Specification.md", root / "DashCamMerger-Specification.html")
    build(root / "DashCamMerger-Design.md", root / "DashCamMerger-Design.html")
    android_spec = root / "Android_app_Ver" / "DashCamMerger-Android-Specification.md"
    if android_spec.exists():
        build(android_spec, android_spec.with_suffix(".html"))
