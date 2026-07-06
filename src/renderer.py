from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class RenderContext:
    """Context for rendering a transient window."""
    window_id: str
    title: str
    html_content: str
    pinned: bool
    version: int
    native_type: str | None = None


# Sandbox wrapper: injects CSP meta tag, strips <script> tags unless allowed,
# and wraps content in a sandboxed iframe structure.
SANDBOX_CSP = (
    "default-src 'none'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self'; "
    "script-src 'none';"
)


def sanitize_html(raw_html: str, allow_scripts: bool = False) -> str:
    """Basic sanitization: escape the HTML for safe embedding.

    For now we escape the entire content and wrap it. In a future pass we can
    use a proper HTML sanitizer (e.g., bleach) to allow selective tags.
    """
    # Escape to prevent injection when embedding in the wrapper
    escaped = html.escape(raw_html, quote=True)
    return escaped


def build_sandboxed_html(
    title: str,
    html_content: str,
    allow_scripts: bool = False,
    width: str = "100%",
    height: str = "400px",
) -> str:
    """Build a self-contained HTML document that renders the generated content
    inside a sandboxed iframe with a control bar (pin/save/close)."""

    safe_title = html.escape(title)
    sanitized = sanitize_html(html_content)

    # If scripts are allowed, we use a less restrictive CSP
    csp = SANDBOX_CSP if not allow_scripts else (
        "default-src 'none'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self'; "
        "script-src 'self' 'unsafe-inline';"
    )

    # Sandbox attributes: allow-forms allow-same-origin for basic interactivity
    # but NOT allow-scripts (unless explicitly allowed)
    sandbox_attrs = "allow-forms allow-same-origin"
    if allow_scripts:
        sandbox_attrs += " allow-scripts"

    wrapper = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<title>{safe_title}</title>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: system-ui, -apple-system, sans-serif; background: #1a1a2e; color: #e0e0e0; }}
    .control-bar {{
        display: flex; align-items: center; justify-content: space-between;
        padding: 8px 12px; background: #16213e; border-bottom: 1px solid #0f3460;
    }}
    .control-bar h2 {{ font-size: 14px; font-weight: 600; color: #e0e0e0; }}
    .controls {{ display: flex; gap: 8px; }}
    .controls button {{
        padding: 4px 10px; border: 1px solid #0f3460; border-radius: 4px;
        background: #1a1a2e; color: #e0e0e0; cursor: pointer; font-size: 12px;
    }}
    .controls button:hover {{ background: #0f3460; }}
    .iframe-container {{ width: {width}; height: {height}; border: none; }}
    iframe {{ width: 100%; height: 100%; border: none; }}
</style>
</head>
<body>
<div class="control-bar">
    <h2>{safe_title}</h2>
    <div class="controls">
        <button id="pinBtn" title="Pin window">ð Pin</button>
        <button id="saveBtn" title="Save to capture">ð¾ Save</button>
        <button id="closeBtn" title="Close window">â Close</button>
    </div>
</div>
<div class="iframe-container">
    <iframe sandbox="{sandbox_attrs}" srcdoc="{sanitized}"></iframe>
</div>
<script>
    // PostMessage bridge for control actions
    document.getElementById('pinBtn').addEventListener('click', () => {{
        window.parent.postMessage({{ action: 'pin', windowId: '__WINDOW_ID__' }}, '*');
    }});
    document.getElementById('saveBtn').addEventListener('click', () => {{
        window.parent.postMessage({{ action: 'save', windowId: '__WINDOW_ID__' }}, '*');
    }});
    document.getElementById('closeBtn').addEventListener('click', () => {{
        window.parent.postMessage({{ action: 'close', windowId: '__WINDOW_ID__' }}, '*');
    }});
</script>
</body>
</html>"""
    return wrapper


def render_window(context: RenderContext, allow_scripts: bool = False) -> str:
    """Render a transient window context into a sandboxed HTML document."""
    return build_sandboxed_html(
        title=context.title,
        html_content=context.html_content,
        allow_scripts=allow_scripts,
    )


def export_artifact(window_id: str, html_content: str, output_dir: Path) -> Path:
    """Export a transient window's HTML content to a standalone file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{window_id}.html"
    output_path.write_text(html_content, encoding="utf-8")
    return output_path
