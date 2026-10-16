"""Turns the data from disk_report.py into a readable PDF."""

import os
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import classify

GB = 1024 ** 3

# colors: ink for text, one accent hue for bars, quiet grays for rules and tracks
TEXT = colors.HexColor("#0b0b0b")
TEXT2 = colors.HexColor("#52514e")
MUTED = colors.HexColor("#8a8984")
RULE = colors.HexColor("#e4e3df")
TRACK = colors.HexColor("#ecebe7")
ACCENT = colors.HexColor("#2a78d6")

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
FRAME_W = PAGE_W - 2 * MARGIN


def _register_fonts():
    """A TrueType font with full Unicode, so folder names in any language print correctly."""
    win = os.environ.get("WINDIR", r"C:\Windows")
    candidates = [
        (os.path.join(win, "Fonts", "segoeui.ttf"), os.path.join(win, "Fonts", "segoeuib.ttf")),
        (os.path.join(win, "Fonts", "arial.ttf"), os.path.join(win, "Fonts", "arialbd.ttf")),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ]
    for regular, bold in candidates:
        if os.path.exists(regular) and os.path.exists(bold):
            try:
                pdfmetrics.registerFont(TTFont("Body", regular))
                pdfmetrics.registerFont(TTFont("Body-Bold", bold))
                registerFontFamily("Body", normal="Body", bold="Body-Bold", italic="Body", boldItalic="Body-Bold")
                return "Body", "Body-Bold", True
            except Exception:
                continue
    return "Helvetica", "Helvetica-Bold", False


FONT, BOLD, UNICODE = _register_fonts()


def t(text):
    """Text made safe for a Paragraph."""
    text = str(text)
    if not UNICODE:
        text = text.encode("latin-1", "replace").decode("latin-1")
    return escape(text)


def gb(size, digits=1):
    return f"{size / GB:.{digits}f} GB"


# ---------- styles ----------
S = {
    "title": ParagraphStyle("title", fontName=BOLD, fontSize=20, leading=24, textColor=TEXT, spaceAfter=2),
    "sub": ParagraphStyle("sub", fontName=FONT, fontSize=9, leading=12, textColor=TEXT2, spaceAfter=10),
    "h2": ParagraphStyle("h2", fontName=BOLD, fontSize=13, leading=16, textColor=TEXT, spaceBefore=14, spaceAfter=2),
    "intro": ParagraphStyle("intro", fontName=FONT, fontSize=8.5, leading=11.5, textColor=TEXT2, spaceAfter=6),
    "body": ParagraphStyle("body", fontName=FONT, fontSize=8.5, leading=11, textColor=TEXT),
    "cell": ParagraphStyle("cell", fontName=FONT, fontSize=8, leading=10.5, textColor=TEXT, wordWrap="CJK"),
    "path": ParagraphStyle("path", fontName=FONT, fontSize=7.5, leading=9.5, textColor=TEXT2, wordWrap="CJK"),
    "size": ParagraphStyle("size", fontName=FONT, fontSize=8, leading=10.5, textColor=TEXT, alignment=TA_RIGHT),
    "head": ParagraphStyle("head", fontName=BOLD, fontSize=7.5, leading=10, textColor=TEXT2),
    "headr": ParagraphStyle("headr", fontName=BOLD, fontSize=7.5, leading=10, textColor=TEXT2, alignment=TA_RIGHT),
    "group": ParagraphStyle("group", fontName=BOLD, fontSize=8.5, leading=11, textColor=TEXT),
    "bullet": ParagraphStyle("bullet", fontName=FONT, fontSize=8.5, leading=11.5, textColor=TEXT,
                             leftIndent=10, bulletIndent=0, spaceAfter=2),
}


def P(text, style="cell"):
    return Paragraph(text, S[style])


def table(header, rows, widths, group_rows=()):
    """A plain table: bold header, thin rules between rows, size column right-aligned."""
    head = [P(t(h), "headr" if i == 0 else "head") for i, h in enumerate(header)]
    tbl = Table([head] + rows, colWidths=widths, repeatRows=1)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, TEXT2),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for r in group_rows:   # a heading row spanning the table, e.g. an app type
        style += [("SPAN", (0, r), (-1, r)), ("TOPPADDING", (0, r), (-1, r), 8),
                  ("LINEBELOW", (0, r), (-1, r), 0.6, TEXT2)]
    tbl.setStyle(TableStyle(style))
    return tbl


def _bar(d, x, y, w, h, color):
    """A bar growing from x, rounded at the data end and square at the baseline."""
    if w <= 0:
        return
    r = min(3, h / 2, w / 2)
    d.add(Rect(x, y, w, h, rx=r, ry=r, fillColor=color, strokeColor=None))
    if w > r:
        d.add(Rect(x, y, r, h, fillColor=color, strokeColor=None))


def drive_meters(drives):
    row_h, bar_h, label_w, bar_w = 22, 10, 34, 250
    d = Drawing(FRAME_W, row_h * len(drives))
    for i, drv in enumerate(drives):
        y = d.height - (i + 1) * row_h + (row_h - bar_h) / 2
        used_frac = drv["used"] / drv["total"] if drv["total"] else 0
        d.add(String(0, y + 1.5, drv["root"].rstrip("\\"), fontName=BOLD, fontSize=10, fillColor=TEXT))
        d.add(Rect(label_w, y, bar_w, bar_h, rx=3, ry=3, fillColor=TRACK, strokeColor=None))
        _bar(d, label_w, y, bar_w * used_frac, bar_h, ACCENT)
        note = f"{gb(drv['used'], 0)} of {gb(drv['total'], 0)} used   {gb(drv['free'], 0)} free"
        if drv["total"] and drv["free"] / drv["total"] < 0.15:
            note += "   (low on space)"
        d.add(String(label_w + bar_w + 10, y + 1.5, note, fontName=FONT, fontSize=8.5, fillColor=TEXT))
    return d


def category_chart(categories):
    row_h, bar_h, label_w, value_w = 19, 10, 110, 60
    bar_area = FRAME_W - label_w - value_w
    top = max((b for _c, b in categories), default=1) or 1
    d = Drawing(FRAME_W, row_h * len(categories))
    for i, (cat, b) in enumerate(categories):
        y = d.height - (i + 1) * row_h + (row_h - bar_h) / 2
        d.add(String(0, y + 1.5, cat, fontName=FONT, fontSize=8.5, fillColor=TEXT))
        w = bar_area * b / top
        _bar(d, label_w, y, w, bar_h, ACCENT)
        d.add(String(label_w + w + 6, y + 1.5, gb(b), fontName=FONT, fontSize=8.5, fillColor=TEXT))
    return d


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(FONT, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, 10 * mm, "pc-tidy disk report")
    canvas.drawRightString(PAGE_W - MARGIN, 10 * mm, f"page {doc.page}")
    canvas.restoreState()


def section(story, title, intro=None):
    story.append(P(t(title), "h2"))
    if intro:
        story.append(P(intro, "intro"))


def render(data, path):
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=18 * mm,
                            title="Disk report", author="pc-tidy")
    story = []
    s = data["stats"]
    story.append(P("Disk report", "title"))
    story.append(P(t(f"{data['computer']}   {data['generated']}   "
                     f"{s['files']:,} files ({gb(s['bytes'])}) scanned in {s['seconds']:.0f} s"), "sub"))

    # --- drives and summary ---
    story.append(drive_meters(data["drives"]))
    summary = []
    if data["cleanup"]:
        summary.append(f"<b>Clean up:</b> about {t(gb(sum(c['size'] for c in data['cleanup']), 0))} "
                       "in items the report recognizes (see below, then cleanup.ps1).")
    m = data.get("move")
    if m and m["candidates"]:
        summary.append(f"<b>Move:</b> {t(gb(sum(c['size'] for c in m['candidates']), 0))} of personal files "
                       f"could move from C: to {t(m['dest'])} with move_off_c.py.")
    if summary:
        story.append(Spacer(1, 6))
        story += [Paragraph(x, S["bullet"], bulletText="\u2022" if UNICODE else "-") for x in summary]

    # --- categories ---
    if data["categories"]:
        section(story, "Where the space goes",
                "Every folder sorted into one category, all scanned drives together. "
                "System looks bigger than it is because Windows counts some files twice (hard links).")
        story.append(category_chart(data["categories"]))

    # --- clean up ---
    section(story, "Things you can clean up",
            "Recognized items and what to do. Settings named cleanup.ps1: ... are switches in cleanup.ps1.")
    if data["cleanup"]:
        rows = [[P(t(gb(c["size"])), "size"),
                 [P(f"<b>{t(c['what'])}</b>"), P(t(c["path"]), "path")],
                 P(t(c["todo"]))] for c in data["cleanup"]]
        story.append(table(["Size", "Item", "What to do"], rows, [17 * mm, 88 * mm, FRAME_W - 105 * mm]))
    else:
        story.append(P("Nothing recognized.", "body"))

    # --- move off C: ---
    if m is not None:
        section(story, "Could move to another drive",
                f"Personal folders and big files on C: that can safely live on {t(m['dest'])}. "
                "Run <b>python move_off_c.py</b> to pick and move them (it can undo)."
                if m["dest"] else "No other drive was found, so nothing can be moved.")
        if m["candidates"]:
            rows = [[P(t(gb(c["size"])), "size"), P(t(c["path"]), "cell")] for c in m["candidates"]]
            story.append(table(["Size", "Folder or file"], rows, [17 * mm, FRAME_W - 17 * mm]))
        if m["tips"]:
            story.append(Spacer(1, 6))
        for tip in m["tips"]:
            story.append(Paragraph(t(tip), S["bullet"], bulletText="\u2022" if UNICODE else "-"))

    # --- apps by type ---
    if data["apps"]:
        section(story, "Installed apps by type",
                "Sizes come from the installers and are often missing (?) or rough. "
                "Uninstall in Settings > Apps, or put the exact name in cleanup.ps1: AppsToUninstall.")
        rows, groups = [], []
        for app_t in classify.APP_TYPES:
            group = [a for a in data["apps"] if a["type"] == app_t]
            if not group:
                continue
            groups.append(len(rows) + 1)   # +1 for the header row
            total = sum(a["size"] for a in group)
            count = f"{len(group)} app{'s' if len(group) != 1 else ''}"
            size_text = gb(total) if total else "sizes unknown"
            rows.append([P(f"{t(app_t)}   <font color='#52514e'>{count}, {t(size_text)}</font>",
                           "group"), "", ""])
            for a in group:
                rows.append([P(t(gb(a["size"])) if a["size"] else "?", "size"), P(t(a["name"])),
                             P("LLM guess" if a.get("llm") else "", "path")])
        story.append(table(["Size", "App", ""], rows, [17 * mm, FRAME_W - 42 * mm, 25 * mm], groups))

    # --- folder trees ---
    for root, tree_rows in data["trees"]:
        section(story, f"Biggest folders on {root.rstrip(chr(92))}",
                "Folders over 1 GB, biggest first. Labels show what the report recognizes.")
        rows = []
        for size, depth, name, label in tree_rows:
            style = ParagraphStyle("indent", parent=S["cell"], leftIndent=depth * 9)
            text = t(name) + (f"   <font color='#52514e'>{t(label)}</font>" if label else "")
            rows.append([P(t(gb(size)), "size"), Paragraph(text, style)])
        story.append(table(["Size", "Folder"], rows, [17 * mm, FRAME_W - 17 * mm]))

    # --- biggest files ---
    section(story, "Biggest files")
    rows = [[P(t(gb(f["size"])), "size"),
             [P(t(f["path"])), P(t(f["what"]), "path")] if f["what"] else P(t(f["path"]))]
            for f in data["big_files"]]
    story.append(table(["Size", "File"], rows, [17 * mm, FRAME_W - 17 * mm]))

    # --- good to know ---
    if data["info"]:
        section(story, "Good to know", "Usually leave these alone.")
        rows = [[P(t(gb(i["size"])), "size"), [P(f"<b>{t(i['what'])}</b>"), P(t(i["path"]), "path")],
                 P(t(i["todo"]))] for i in data["info"]]
        story.append(table(["Size", "Item", "Note"], rows, [17 * mm, 88 * mm, FRAME_W - 105 * mm]))

    # --- LLM descriptions ---
    if data["llm_folders"]:
        section(story, "What the local AI thinks these folders are",
                "Big folders no rule recognizes, described by a small model that can be wrong. "
                "This is a description, not advice to delete.")
        rows = [[P(t(gb(f["size"])), "size"), [P(t(f["what"])), P(t(f["path"]), "path")]]
                for f in data["llm_folders"]]
        story.append(table(["Size", "Folder"], rows, [17 * mm, FRAME_W - 17 * mm]))

    if s["denied"]:
        story.append(Spacer(1, 10))
        story.append(P(t(f"{s['denied']} folders could not be opened. Running PowerShell as administrator "
                         "includes most of them (a few system folders stay locked)."), "intro"))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
