"""The disk report as one web page, with nothing but the standard library: what pc-tidy.exe saves (it
has no reportlab), and what the guided flow saves wherever reportlab isn't installed.

It shows the drives and the ranked fixes for C: as pdf_report.py does, then the whole text report
(disk_report.render_text), so every section is there and the browser can search it.
"""

import html

from pctidy import fixes

RISK_COLORS = {"Low": "#2e7d32", "Medium": "#b26a00", "High": "#c62828"}

STYLE = """
:root { --text: #1f2328; --muted: #59636e; --line: #d0d7de; --track: #eaeef2; --accent: #0969da; --bg: #ffffff; }
@media (prefers-color-scheme: dark) {
  :root { --text: #e6edf3; --muted: #9198a1; --line: #3d444d; --track: #262c36; --accent: #4493f8; --bg: #0d1117; }
}
body { font: 15px/1.5 "Segoe UI", system-ui, sans-serif; color: var(--text); background: var(--bg);
       max-width: 980px; margin: 0 auto; padding: 24px 16px; }
h1 { margin: 0 0 4px; } h2 { margin-top: 32px; border-bottom: 1px solid var(--line); padding-bottom: 4px; }
.sub, .muted { color: var(--muted); }
.drive { display: grid; grid-template-columns: 3em minmax(0, 260px) auto; gap: 10px; align-items: center; margin: 6px 0; }
.track { background: var(--track); border-radius: 4px; height: 10px; }
.used { background: var(--accent); border-radius: 4px; height: 10px; }
table { border-collapse: collapse; width: 100%; }
th, td { border-bottom: 1px solid var(--line); padding: 8px 6px; text-align: left; vertical-align: top; }
td.num { white-space: nowrap; text-align: right; }
.risk { font-weight: 600; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font: 13px/1.45 Consolas, ui-monospace, monospace; }
"""


def _e(text):
    return html.escape(str(text))


def _drives(drives):
    out = []
    for d in drives:
        used = d["used"] / d["total"] * 100 if d["total"] else 0
        low = " (low on space)" if d["total"] and d["free"] / d["total"] < 0.15 else ""
        out.append(f'<div class="drive"><b>{_e(d["root"].rstrip(chr(92)))}</b>'
                   f'<div class="track"><div class="used" style="width:{used:.0f}%"></div></div>'
                   f'<span>{_e(fixes.gb(d["used"]))} of {_e(fixes.gb(d["total"]))} used, '
                   f'{_e(fixes.gb(d["free"]))} free{low}</span></div>')
    return "\n".join(out)


def _fixes(f):
    out = [f"<h2>Fixes for {_e(f['drive'])}</h2>",
           '<p class="muted">What to do first to free space, lowest risk first and biggest first within each '
           "risk. Each number counts exactly the files the fix removes, once.</p>"]
    if f["rows"]:
        out.append('<table><tr><th>#</th><th>Frees</th><th>Total</th><th>Risk</th><th>Fix</th></tr>')
        for n, r in enumerate(f["rows"], 1):
            total = fixes.gb(r["total"]) if r["total"] is not None else ""
            note = f'<br><span class="muted">{_e(r["note"])}</span>' if r["note"] else ""
            out.append(f'<tr><td class="num">{n}</td><td class="num">{_e(r["size"])}</td><td class="num">{_e(total)}</td>'
                       f'<td class="risk" style="color:{RISK_COLORS[r["risk"]]}">{_e(r["risk"])}</td>'
                       f'<td><b>{_e(r["title"])}</b><br>{_e(r["why"])}.<br><b>How:</b> {_e(r["how"])}<br>'
                       f'<b>Undo:</b> {_e(r["undo"])}{note}</td></tr>')
        out.append("</table>")
    elif f["scanned"]:
        out.append("<p>Nothing found that is worth doing.</p>")
    lines = ([f"<b>{_e(f['target_line'])}</b>"] if f.get("target_line") else []) + [_e(n) for n in f["notes"]]
    if lines:
        out.append("<ul>" + "".join(f"<li>{x}</li>" for x in lines) + "</ul>")
    return "\n".join(out)


def render(data, text, path):
    """Write the report to path (.html). `text` is disk_report.render_text(data)."""
    s = data["stats"]
    body = ["<h1>Disk report</h1>",
            f'<p class="sub">{_e(data["computer"])} &middot; {_e(data["generated"])} &middot; '
            f'{s["files"]:,} files ({_e(fixes.gb(s["bytes"]))}) scanned in {s["seconds"]:.0f} s</p>',
            _drives(data["drives"])]
    if data.get("fixes"):
        body.append(_fixes(data["fixes"]))
    body += ["<h2>The whole report</h2>", f"<pre>{_e(text)}</pre>"]
    page = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>Disk report</title><style>{STYLE}</style></head>\n<body>\n" + "\n".join(body) + "\n</body></html>\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
