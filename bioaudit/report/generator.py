"""Render a TestRun to a self-contained HTML report, optionally exporting to PDF."""

from __future__ import annotations

import html
import os
from datetime import datetime

from ..models import TestRun

_SEVERITY_COLORS = {
    "Critical": "#b00020", "High": "#d9534f", "Medium": "#f0ad4e",
    "Low": "#5bc0de", "Info": "#777",
}


_DARK_CSS = (
    "body,h1,h2,h3,p,li,em,strong,div,span{color:#ffffff}"
    " .owasp,.conf,.evidence{color:#ffffff}"
    " .pill,.sev{color:#ffffff}"
)


def render_html(run: TestRun, dark: bool = False) -> str:
    """`dark=True` is for the dark on-screen viewer only; exported files stay light."""
    if dark:
        return _render_screen(run)
    counts = run.counts()
    rows = []
    for f in run.ranked():
        color = _SEVERITY_COLORS.get(f.severity.label, "#777")
        rows.append(f"""
        <div class="finding">
          <span class="sev" style="background:{color}">{f.severity.label}</span>
          <strong>{html.escape(f.title)}</strong>
          <span class="owasp">{html.escape(', '.join(f.owasp))}</span>
          <span class="conf">{html.escape(f.confidence)}</span>
          <div class="evidence"><em>Evidence:</em> {html.escape(f.evidence)}</div>
          {_optional('Explanation', f.explanation)}
          {_optional('Mitigation', f.mitigation)}
          {_render_attack_path(f)}
        </div>""")

    summary = " ".join(
        f'<span class="pill" style="background:{_SEVERITY_COLORS[k]}">{k}: {v}</span>'
        for k, v in counts.items() if v
    ) or "No findings."

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>BioAudit report — {html.escape(run.package)}</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#222}}
 .pill,.sev{{color:#fff;padding:2px 8px;border-radius:4px;font-size:.8rem}}
 .finding{{border:1px solid #eee;border-left:4px solid #ccc;padding:1rem;margin:1rem 0;border-radius:4px}}
 .owasp,.conf{{color:#666;font-size:.8rem;margin-left:.5rem}}
 .evidence{{margin-top:.5rem;font-size:.9rem;color:#444;word-break:break-word}}
 h1{{margin-bottom:.2rem}}
 {_DARK_CSS if dark else ''}
</style></head><body>
<h1>BioAudit Security Report</h1>
<p><strong>Package:</strong> {html.escape(run.package)}<br>
<strong>Generated:</strong> {datetime.now():%Y-%m-%d %H:%M}</p>
<p>{summary}</p>
{_render_scope(run)}
<hr>
{''.join(rows) or '<p>No findings.</p>'}
</body></html>"""


def export(run: TestRun, output_dir: str, to_pdf: bool = False) -> str:
    os.makedirs(output_dir, exist_ok=True)
    html_content = render_html(run)
    base = os.path.join(output_dir, f"report-{run.package}-{run.id}")
    html_path = base + ".html"
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html_content)

    if to_pdf:
        try:
            from weasyprint import HTML
            pdf_path = base + ".pdf"
            HTML(string=html_content).write_pdf(pdf_path)
            return pdf_path
        except Exception:
            pass  # fall back to HTML if weasyprint isn't available
    return html_path


def _optional(label: str, value) -> str:
    if not value:
        return ""
    return f'<div class="evidence"><em>{label}:</em> {html.escape(str(value))}</div>'


def _render_scope(run: TestRun) -> str:
    tests = "".join(f"<li>{html.escape(t)}</li>" for t in run.tests_performed)
    return f"""
    <div class="scope">
      <h2>Test Scope</h2>
      {_optional('Package', run.package)}
      {_optional('Device model', run.device_model)}
      {_optional('App version', run.app_version)}
      {_optional('Android version', run.android_version)}
      {_optional('Login state', run.login_state)}
      {f'<div class="evidence"><em>Tests performed:</em><ul>{tests}</ul></div>' if tests else ''}
    </div>"""


def _render_attack_path(finding) -> str:
    if not finding.attack_path:
        return ""
    steps = "".join(f"<li>{html.escape(step)}</li>" for step in finding.attack_path)
    return f'<div class="evidence"><em>Attack path:</em><ol>{steps}</ol></div>'


def _render_screen(run: TestRun) -> str:
    """The report as shown inside the desktop app's results pane.

    That pane is a Qt QTextBrowser, which supports only part of HTML and CSS: it ignores
    rem units, and padding or borders on a div. The browser layout above relies on all
    three, so on screen every finding ran into the next. Here each finding is a one-cell
    table, which Qt does pad and frame, with inline styles only. Exported files and
    "Open full report" keep the browser layout.
    """
    from ..gui.theme import DARK as p, SEVERITY_COLOURS

    def esc(value) -> str:
        return html.escape(str(value))

    def badge(severity: str, text: str) -> str:
        colour = SEVERITY_COLOURS.get(severity.lower(), SEVERITY_COLOURS["info"])
        return (f'<span style="background-color:{colour};color:#ffffff;font-weight:600">'
                f'&nbsp;{esc(text)}&nbsp;</span>')

    def field(label: str, value) -> str:
        if not value:
            return ""
        return (f'<p style="margin:10px 0 0 0"><span style="color:{p["muted"]};font-weight:600">'
                f'{label}</span><br><span style="color:{p["text"]}">{esc(value)}</span></p>')

    def card(inner: str) -> str:
        return (f'<table width="100%" cellspacing="0" cellpadding="14" border="1" '
                f'style="border-color:{p["border"]};border-style:solid" bgcolor="{p["surface"]}">'
                f'<tr><td>{inner}</td></tr></table>'
                f'<p style="margin:0;font-size:5pt">&nbsp;</p>')

    counts = run.counts()
    # Most severe first; counts() runs Info to Critical.
    chips = "&nbsp;&nbsp;".join(badge(k, f"{v} {k}")
                                for k, v in reversed(list(counts.items())) if v)
    meta = [("Package", run.package), ("Device model", run.device_model),
            ("App version", run.app_version), ("Android version", run.android_version),
            ("Login state", run.login_state)]
    scope_rows = "".join(
        f'<tr><td style="color:{p["muted"]};padding-right:16px">{label}</td>'
        f'<td style="color:{p["text"]}">{esc(value)}</td></tr>'
        for label, value in meta if value)
    tests = "".join(f"<li>{esc(t)}</li>" for t in run.tests_performed)
    scope = (f'<p style="margin:0;color:{p["text"]};font-weight:600">Test scope</p>'
             f'<table cellspacing="0" cellpadding="2" style="margin-top:6px">{scope_rows}</table>'
             + (f'<p style="margin:8px 0 0 0;color:{p["muted"]};font-weight:600">Tests performed</p>'
                f'<ul style="margin-top:2px;color:{p["text"]}">{tests}</ul>' if tests else ""))

    findings = []
    for f in run.ranked():
        detail = " · ".join(x for x in (", ".join(f.owasp), f.confidence, f.component) if x)
        path = ""
        if f.attack_path:
            steps = "".join(f"<li>{esc(s)}</li>" for s in f.attack_path)
            path = (f'<p style="margin:10px 0 0 0;color:{p["muted"]};font-weight:600">Attack path</p>'
                    f'<ol style="margin-top:2px;color:{p["text"]}">{steps}</ol>')
        findings.append(card(
            f'<p style="margin:0">{badge(f.severity.label, f.severity.label)}&nbsp;&nbsp;'
            f'<span style="font-size:11pt;font-weight:600;color:{p["text"]}">{esc(f.title)}</span></p>'
            f'<p style="margin:4px 0 0 0;color:{p["muted"]};font-size:9pt">{esc(detail)}</p>'
            + field("Evidence", f.evidence) + field("Explanation", f.explanation)
            + field("Mitigation", f.mitigation) + path))

    return (f'<div style="font-family:\'IBM Plex Sans\',\'Segoe UI\',Arial,sans-serif">'
            f'<p style="margin:0;font-size:15pt;font-weight:700;color:{p["text"]}">'
            f'BioAudit Security Report</p>'
            f'<p style="margin:4px 0 10px 0;color:{p["muted"]}">{esc(run.package)} · generated '
            f'{datetime.now():%d %b %Y, %H:%M}</p>'
            f'<p style="margin:0 0 12px 0">{chips or "No findings."}</p>'
            + card(scope)
            + "".join(findings or [f'<p style="color:{p["muted"]}">No findings.</p>'])
            + "</div>")
