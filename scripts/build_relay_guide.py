# -*- coding: utf-8 -*-
"""Build the Relay & Front Door Guide PDF (docs/Pwllheli_Relay_Guide.pdf).

The relay started life as a live-camera stream box and quietly became the centre
of the system: it is the club's single public front door, it runs the GPS
tracking server the automatic finishes depend on, and it is the one place every
visitor is logged. That is too much to leave in a README in deploy/, so this
builds the same house-style PDF as the other three guides.

Source of truth for the procedures is deploy/live_stream/README.md; keep the two
in step. Run from the repo root:  python scripts/build_relay_guide.py
"""
import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Image, PageBreak,
    Table, TableStyle, ListFlowable, ListItem, KeepTogether, HRFlowable, NextPageTemplate
)

LOGO_PATH = os.path.join(_REPO, "static", "img", "pwllheli_sailing_club_logo.png")
OUT_PATH = os.path.join(_REPO, "docs", "Pwllheli_Relay_Guide.pdf")
# Read from the file the release process bumps. It was a hard-coded string that
# merely looked dynamic through the f-string on the cover, and sat at 0.197
# for twenty-two releases.
with open(os.path.join(_REPO, "VERSION"), encoding="utf-8") as _v:
    APP_VERSION = _v.read().strip()

NAVY = colors.HexColor("#0f314e")
NAVY_DARK = colors.HexColor("#0a2138")
GOLD = colors.HexColor("#b9800a")
MUTED = colors.HexColor("#4b5565")
LINE = colors.HexColor("#d4dce9")
NOTE_BG = colors.HexColor("#eef5ff")
NOTE_BORDER = colors.HexColor("#b9d0ff")
WARN_BG = colors.HexColor("#fff1f0")
WARN_BORDER = colors.HexColor("#f2b8b5")
CODE_BG = colors.HexColor("#f4f6fa")

PAGE_W, PAGE_H = A4
MARGIN = 2.2 * cm
CONTENT_W = PAGE_W - 2 * MARGIN

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="CoverTitle", fontName="Helvetica-Bold", fontSize=30,
                          leading=36, textColor=colors.white, alignment=TA_CENTER, spaceAfter=6))
styles.add(ParagraphStyle(name="CoverSubtitle", fontName="Helvetica", fontSize=14,
                          leading=19, textColor=colors.HexColor("#b7c6db"), alignment=TA_CENTER))
styles.add(ParagraphStyle(name="CoverMeta", fontName="Helvetica", fontSize=10.5,
                          leading=15, textColor=colors.HexColor("#7f95ae"), alignment=TA_CENTER))
styles.add(ParagraphStyle(name="CoverClub", fontName="Helvetica-Bold", fontSize=12,
                          leading=16, textColor=GOLD, alignment=TA_CENTER))
styles.add(ParagraphStyle(name="H1", fontName="Helvetica-Bold", fontSize=19,
                          leading=23, textColor=NAVY_DARK, spaceBefore=0, spaceAfter=4))
styles.add(ParagraphStyle(name="H1Kicker", fontName="Helvetica-Bold", fontSize=10.5,
                          leading=13, textColor=GOLD, spaceBefore=0, spaceAfter=2))
styles.add(ParagraphStyle(name="H2", fontName="Helvetica-Bold", fontSize=13.5,
                          leading=17, textColor=NAVY, spaceBefore=16, spaceAfter=6))
styles.add(ParagraphStyle(name="H3", fontName="Helvetica-Bold", fontSize=11,
                          leading=14, textColor=NAVY_DARK, spaceBefore=11, spaceAfter=4))
styles.add(ParagraphStyle(name="Body", fontName="Helvetica", fontSize=10, leading=14.5,
                          textColor=colors.HexColor("#1f2937"), spaceAfter=7, alignment=TA_LEFT))
styles.add(ParagraphStyle(name="Caption", fontName="Helvetica-Oblique", fontSize=8.7,
                          leading=11.5, textColor=MUTED, alignment=TA_CENTER, spaceBefore=4, spaceAfter=14))
styles.add(ParagraphStyle(name="BulletItem", fontName="Helvetica", fontSize=10, leading=14.5,
                          textColor=colors.HexColor("#1f2937"), spaceAfter=4))
styles.add(ParagraphStyle(name="Snippet", fontName="Courier", fontSize=8.6, leading=12,
                          textColor=colors.HexColor("#12263f")))
styles.add(ParagraphStyle(name="NoteText", fontName="Helvetica", fontSize=9.5, leading=13.5,
                          textColor=colors.HexColor("#24436f")))
styles.add(ParagraphStyle(name="WarnText", fontName="Helvetica", fontSize=9.5, leading=13.5,
                          textColor=colors.HexColor("#842029")))
styles.add(ParagraphStyle(name="NoteLabel", fontName="Helvetica-Bold", fontSize=8.3,
                          textColor=colors.HexColor("#24436f"), spaceAfter=2))
styles.add(ParagraphStyle(name="WarnLabel", fontName="Helvetica-Bold", fontSize=8.3,
                          textColor=colors.HexColor("#842029"), spaceAfter=2))
styles.add(ParagraphStyle(name="TocEntry", fontName="Helvetica", fontSize=10.7, leading=19,
                          textColor=colors.HexColor("#1f2937"), leftIndent=10))


def note_box(text, kind="note"):
    body_style = styles["NoteText"] if kind == "note" else styles["WarnText"]
    label_style = styles["NoteLabel"] if kind == "note" else styles["WarnLabel"]
    bg = NOTE_BG if kind == "note" else WARN_BG
    border = NOTE_BORDER if kind == "note" else WARN_BORDER
    label = "NOTE" if kind == "note" else "IMPORTANT"
    inner = Table([[Paragraph(label, label_style)], [Paragraph(text, body_style)]],
                  colWidths=[CONTENT_W - 20])
    inner.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, 0), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    box = Table([[inner]], colWidths=[CONTENT_W])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.75, border),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return [Spacer(1, 4), box, Spacer(1, 10)]


def code_block(lines):
    """A shell/config snippet. Courier in a tinted box, kept on one page."""
    body = [Paragraph(l.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") or "&nbsp;",
                      styles["Snippet"])
            for l in lines]
    t = Table([[b] for b in body], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]))
    return [Spacer(1, 3), KeepTogether(t), Spacer(1, 9)]


def chapter_heading(kicker, title):
    return [
        Paragraph(kicker.upper(), styles["H1Kicker"]),
        Paragraph(title, styles["H1"]),
        HRFlowable(width=CONTENT_W, thickness=1.4, color=GOLD, spaceAfter=12),
    ]


def bullets(items):
    return ListFlowable(
        [ListItem(Paragraph(t, styles["BulletItem"]), bulletColor=NAVY) for t in items],
        bulletType="bullet", start="circle", leftIndent=14, bulletFontSize=6,
    )


def numbered(items):
    return ListFlowable(
        [ListItem(Paragraph(t, styles["BulletItem"])) for t in items],
        bulletType="1", leftIndent=16,
    )


def field_table(rows, col_widths=None):
    col_widths = col_widths or [5.4 * cm, CONTENT_W - 5.4 * cm]
    data = [[Paragraph(f"<b>{k}</b>", styles["BulletItem"]), Paragraph(v, styles["BulletItem"])]
            for k, v in rows]
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("GRID", (0, 0), (-1, -1), 0.5, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return [Spacer(1, 3), t, Spacer(1, 10)]


def header_table(header, rows, col_widths):
    data = [[Paragraph(f"<b>{h}</b>", ParagraphStyle("th", parent=styles["BulletItem"],
                                                     textColor=colors.white)) for h in header]]
    data += [[Paragraph(c, styles["BulletItem"]) for c in r] for r in rows]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("GRID", (0, 0), (-1, -1), 0.5, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return [Spacer(1, 3), t, Spacer(1, 10)]


# ---------------------------------------------------------------------------
# The architecture diagram. Drawn rather than screenshotted: it is the one
# picture that explains why the relay matters, and it has no on-screen original.
# ---------------------------------------------------------------------------

def _box(d, x, y, w, h, title, subtitle=None, fill=colors.white, stroke=NAVY,
         title_colour=NAVY_DARK, dashed=False):
    r = Rect(x, y, w, h, fillColor=fill, strokeColor=stroke, strokeWidth=0.9)
    if dashed:
        r.strokeDashArray = [3, 2]
    d.add(r)
    ty = y + h - 13 if subtitle else y + h / 2 - 3
    d.add(String(x + w / 2, ty, title, fontName="Helvetica-Bold", fontSize=8.2,
                 fillColor=title_colour, textAnchor="middle"))
    if subtitle:
        for i, line in enumerate(subtitle):
            d.add(String(x + w / 2, ty - 11 - i * 9, line, fontName="Helvetica", fontSize=7,
                         fillColor=MUTED, textAnchor="middle"))
    return r


def _arrow(d, x1, y1, x2, y2, label=None, colour=NAVY, label_dx=0, label_dy=0):
    d.add(Line(x1, y1, x2, y2, strokeColor=colour, strokeWidth=0.9))
    # arrow head at (x2, y2), pointing along the segment
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    size = 4.4
    for sign in (1, -1):
        d.add(Line(x2, y2,
                   x2 - size * math.cos(ang - sign * 0.42),
                   y2 - size * math.sin(ang - sign * 0.42),
                   strokeColor=colour, strokeWidth=0.9))
    if label:
        d.add(String((x1 + x2) / 2 + label_dx, (y1 + y2) / 2 + label_dy, label,
                     fontName="Helvetica", fontSize=6.6, fillColor=MUTED, textAnchor="middle"))


def architecture_diagram():
    W, H = CONTENT_W, 15.6 * cm
    d = Drawing(W, H)
    mid = W / 2

    # --- Row 1: the audience -------------------------------------------------
    _box(d, mid - 115, H - 34, 230, 28, "Competitors, spectators, race officers",
         ["web browsers, anywhere"])

    # --- Row 2: Cloudflare ---------------------------------------------------
    cf_y = H - 96
    _box(d, 10, cf_y, W - 20, 46, "CLOUDFLARE",
         ["TLS  ·  caches the video segments  ·  Access / WAF  ·  tunnels (no inbound ports)"],
         fill=colors.HexColor("#fff8e8"), stroke=GOLD, title_colour=GOLD)
    _arrow(d, mid, H - 34, mid, cf_y + 46, "https", label_dx=16)

    # --- Row 3: the relay ----------------------------------------------------
    relay_y, relay_h = H - 250, 122
    _box(d, 10, relay_y, W - 20, relay_h, "", fill=colors.HexColor("#f2f7fd"), stroke=NAVY)
    d.add(String(22, relay_y + relay_h - 15, "THE RELAY  —  a Debian 12 VPS",
                 fontName="Helvetica-Bold", fontSize=8.6, fillColor=NAVY_DARK))
    d.add(String(22, relay_y + relay_h - 26, "the club's single front door",
                 fontName="Helvetica-Oblique", fontSize=7, fillColor=MUTED))

    inner_y = relay_y + 14
    col_w = (W - 60) / 4.0
    services = [
        ("Caddy", ["reverse proxy", "/live, /hut/*,", "/traccar/*, /stats", "access log"]),
        ("MediaMTX", ["HLS, on demand", "one camera pull", "however many", "viewers"]),
        ("Traccar", ["GPS server", "a port per", "device protocol", "forwards to hut"]),
        ("cloudflared", ["front door in,", "camera out over", "Access", ""]),
    ]
    for i, (name, lines) in enumerate(services):
        x = 26 + i * (col_w + 6)
        _box(d, x, inner_y, col_w, 74, name, lines)

    _arrow(d, mid, cf_y, mid, relay_y + relay_h, "tunnel", label_dx=18)

    # --- Row 4: Cloudflare again, on the hut side ---------------------------
    # The relay does NOT talk to the hut directly. Caddy reverse-proxies to
    # https://hut-origin.… and cloudflared bridges the camera over Access — both
    # go out through Cloudflare and back down the hut PC's own tunnel. Only the
    # trackers reach the relay directly, which is why they need the one open port.
    hut_w = W * 0.60
    hut_mid = 10 + hut_w / 2
    cf2_y = 132
    _box(d, 10, cf2_y, hut_w, 38, "CLOUDFLARE",
         ["hut-origin (HTTP)  ·  hut-cam (TCP + Access token)"],
         fill=colors.HexColor("#fff8e8"), stroke=GOLD, title_colour=GOLD)
    _arrow(d, hut_mid, relay_y, hut_mid, cf2_y + 38, "outbound only", label_dx=34)

    # --- Row 5/6: the hut, behind its own tunnel ----------------------------
    hut_pc_y = 66
    _box(d, 46, hut_pc_y, hut_w - 72, 40, "Hut PC (Windows)",
         ["the race app  ·  its own cloudflared tunnel  ·  no inbound port"], dashed=True)
    _arrow(d, hut_mid, cf2_y, hut_mid, hut_pc_y + 40)

    cam_y = 6
    _box(d, 46, cam_y, hut_w - 72, 34, "Hut camera",
         ["Hikvision RTSP sub-stream, on the hut LAN"], dashed=True)
    _arrow(d, hut_mid, hut_pc_y, hut_mid, cam_y + 34, "LAN", label_dx=14)

    # --- The one direct link ------------------------------------------------
    trk_x, trk_w = W * 0.68, W * 0.32 - 10
    _box(d, trk_x, hut_pc_y, trk_w, 40, "Boat trackers",
         ["Queclink GL521MG", "LTE — raw TCP, no Cloudflare"], dashed=True)
    trk_mid = trk_x + trk_w / 2
    _arrow(d, trk_mid, hut_pc_y + 40, trk_mid, relay_y, colour=GOLD)
    label_y = (hut_pc_y + 40 + relay_y) / 2
    # Right-aligned to the left of the line, so the arrow doesn't run through the text.
    d.add(String(trk_mid - 6, label_y + 4, "direct — port 5004",
                 fontName="Helvetica-Bold", fontSize=6.6, fillColor=GOLD, textAnchor="end"))
    d.add(String(trk_mid - 6, label_y - 6, "the one way in",
                 fontName="Helvetica", fontSize=6.6, fillColor=MUTED, textAnchor="end"))
    return d


def on_page(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, MARGIN - 10, PAGE_W - MARGIN, MARGIN - 10)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, MARGIN - 22, "Pwllheli Race Officer — Relay & Front Door Guide")
    canvas.drawRightString(PAGE_W - MARGIN, MARGIN - 22, f"Page {doc.page - 1}")
    canvas.setFont("Helvetica", 6.5)
    canvas.setFillColor(colors.HexColor("#9aa5b1"))
    canvas.drawCentredString(PAGE_W / 2, MARGIN - 33, "Copyright © 2026 CapeNet Ltd. All Rights Reserved.")
    canvas.restoreState()


def on_cover(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(NAVY_DARK)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    canvas.setStrokeColor(GOLD)
    canvas.setLineWidth(2)
    canvas.line(PAGE_W / 2 - 60, PAGE_H - 6.6 * cm, PAGE_W / 2 + 60, PAGE_H - 6.6 * cm)
    canvas.restoreState()


doc = BaseDocTemplate(OUT_PATH, pagesize=A4,
                      leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
                      title="Pwllheli Race Officer - Relay & Front Door Guide",
                      author="Pwllheli Sailing Club")
cover_frame = Frame(0, 0, PAGE_W, PAGE_H, id="cover", leftPadding=0, rightPadding=0,
                    topPadding=0, bottomPadding=0)
body_frame = Frame(MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN, id="body")
doc.addPageTemplates([
    PageTemplate(id="Cover", frames=[cover_frame], onPage=on_cover),
    PageTemplate(id="Body", frames=[body_frame], onPage=on_page),
])

story = []

# ============================================================== COVER
cover_logo = Image(LOGO_PATH, width=3.1 * cm, height=3.1 * cm * 477 / 600)
cover_logo.hAlign = "CENTER"
cover_table = Table([
    [Spacer(1, 4.6 * cm)],
    [cover_logo],
    [Spacer(1, 0.6 * cm)],
    [Paragraph("PWLLHELI SAILING CLUB", styles["CoverClub"])],
    [Spacer(1, 0.9 * cm)],
    [Paragraph("Pwllheli Race Officer", styles["CoverTitle"])],
    [Paragraph("Relay &amp; Front Door Guide",
               ParagraphStyle("t2", parent=styles["CoverTitle"], fontSize=22, leading=26))],
    [Spacer(1, 0.5 * cm)],
    [Paragraph("The one machine the club is reached through: front door,<br/>"
               "live camera and GPS tracking", styles["CoverSubtitle"])],
    [Spacer(1, 5.1 * cm)],
    [Paragraph(f"Covers app version {APP_VERSION}", styles["CoverMeta"])],
    [Paragraph("Internal technical reference · human-supervised prototype", styles["CoverMeta"])],
    [Spacer(1, 0.35 * cm)],
    [Paragraph("Copyright © 2026 CapeNet Ltd. All Rights Reserved.",
               ParagraphStyle("coverCopy", parent=styles["CoverMeta"], fontSize=8,
                              textColor=colors.HexColor("#5a6b80")))],
], colWidths=[PAGE_W])
cover_table.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
story.append(cover_table)
story.append(NextPageTemplate("Body"))
story.append(PageBreak())

# ============================================================== CONTENTS
story += chapter_heading("Contents", "What is in this guide")
for n, t in [
    ("1", "What the relay is, and what it does"),
    ("2", "Before you start: accounts, hostnames, hardware"),
    ("3", "Building the relay"),
    ("4", "Cloudflare: the front door and caching"),
    ("5", "The live camera stream"),
    ("6", "GPS tracking with Traccar"),
    ("7", "Knowing who visits: access logging"),
    ("8", "Running it day to day"),
    ("9", "Troubleshooting"),
    ("10", "File reference"),
]:
    story.append(Paragraph(f"<b>{n}.</b> &nbsp; {t}", styles["TocEntry"]))
story += note_box(
    "The step-by-step procedures here mirror <b>deploy/live_stream/README.md</b> in the app "
    "repository, which stays the working copy next to the config files. If the two ever "
    "disagree, believe the README and re-build this guide "
    "(<b>python scripts/build_relay_guide.py</b>).")
story.append(PageBreak())

# ============================================================== 1
story += chapter_heading("Chapter 1", "What the relay is, and what it does")
story.append(Paragraph(
    "The relay is a small Debian 12 machine — a VPS — that the club rents. It began as a way to "
    "show the hut camera publicly without the off-grid hut uploading one video stream per "
    "viewer. It has since become the piece everything else depends on: it is the club's "
    "<b>single public front door</b>, it runs the <b>GPS tracking server</b> behind the live "
    "race map and the automatic finishes, and it is the one place where <b>every visitor is "
    "logged</b>.", styles["Body"]))
story.append(Paragraph(
    "Nothing reaches the club from the internet except through it. That is worth understanding "
    "before you change anything on it.", styles["Body"]))
story.append(Paragraph(
    "Note which way round the connections go. The relay does <b>not</b> reach into the hut: Caddy reverse-proxies to <b>hut-origin</b> over Cloudflare for the app, and cloudflared bridges the camera over Cloudflare Access — both go out to Cloudflare and come back down the <i>hut PC's own</i> tunnel, which is why the hut needs no open port and the camera is never exposed. The one exception is the <b>boat trackers</b>: they open a raw TCP socket straight to the relay, because there is no tunnel client on a tracker to carry it. That single link is the only inbound access in the whole system.",
    styles["Body"]))
story.append(architecture_diagram())
story.append(Paragraph("How the whole system fits together. The relay does not reach the hut directly: "
                       "the app and the camera are both behind Cloudflare and the hut PC's own tunnel. "
                       "Only the boat trackers connect straight to the relay — which is why they are the "
                       "one thing needing an open port.", styles["Caption"]))

story.append(Paragraph("The four jobs it does", styles["H2"]))
story += field_table([
    ("Front door", "<b>pro.pwllhelisailingclub.org</b> resolves to the relay, and Caddy proxies "
     "to the race app on the hut PC. When the hut is offline it serves a holding page instead "
     "of a browser error, so the public address never simply fails."),
    ("Live camera", "MediaMTX serves the camera as HLS at <b>/live</b>, pulling from the hut "
     "<b>only while someone is watching</b> and only <b>once</b> however many are watching. "
     "Cloudflare caches the video segments, so the relay's uplink stays flat as viewers grow. "
     "Club and sponsor logos are burned in on the relay from the app's own branding settings."),
    ("GPS tracking", "Traccar receives the boats' tracker reports, and both answers the hut "
     "app's polls and pushes each new fix to it. This is what the live race map, the "
     "position-on-the-water list and the automated finishes run on."),
    ("Visitor logging", "Because every request passes through Caddy, one access log covers the "
     "race app, the competitor pages and the stream — with the real visitor IP rather than the "
     "tunnel's."),
])

story += note_box(
    "<b>The trade-off worth being honest about.</b> Folding the old separate front-door PC into "
    "the relay means a single machine now carries the whole public face of the club. If the "
    "<b>hut</b> is down, visitors get the holding page and the live camera still works. If the "
    "<b>relay</b> is down, <b>pro.pwllhelisailingclub.org</b> is unreachable altogether — race "
    "app, competitor pages, live stream and GPS tracking at once. The race office itself keeps "
    "working (the app runs on the hut PC and is reachable at "
    "<b>http://localhost:5050</b> there), so racing is never blocked; it is the public view "
    "and the tracking that stop.", kind="warn")

story.append(Paragraph("Three machines", styles["H2"]))
story += field_table([
    ("The camera", "A Hikvision IP camera on the hut LAN. It is never exposed directly; the hut "
     "PC publishes its RTSP sub-stream as a TCP hostname on its own tunnel, locked to the relay "
     "with a Cloudflare Access service token."),
    ("The hut PC", "Windows, in the start hut. Runs the race app under Waitress and its own "
     "cloudflared tunnel (<b>hut-origin</b>). Opens <b>no inbound ports</b>."),
    ("The relay", "This guide. A Debian 12 VPS: roughly 1 vCPU, 512 MB–1 GB RAM and 4 GB of "
     "disk is enough. It can equally be a container on a home server — see Chapter 3."),
])
story.append(PageBreak())

# ============================================================== 2
story += chapter_heading("Chapter 2", "Before you start: accounts, hostnames, hardware")
story.append(Paragraph("You will need", styles["H2"]))
story.append(bullets([
    "The race app running on the hut PC, <b>v0.140 or later</b> (it provides the live branding "
    "feed and the public live-stream setting) — and v0.189 or later to use push tracking.",
    "A <b>Cloudflare account</b> managing <b>pwllhelisailingclub.org</b>, with the hut already "
    "reachable through a Cloudflare Tunnel.",
    "A <b>Debian 12</b> machine for the relay itself — a VPS, or a container on a home server (Chapter 3 covers both).",
    "Router access, if you want GPS tracking — it needs one forwarded port (Chapter 6).",
]))

story.append(Paragraph("Hostnames", styles["H2"]))
story += header_table(
    ["Hostname", "What it is", "Type"],
    [["pro.pwllhelisailingclub.org", "The public front door. Points at the relay's tunnel; "
      "everything the public uses lives under it.", "Tunnel (CNAME), proxied"],
     ["hut-origin.pwllhelisailingclub.org", "The hut PC's own tunnel, carrying the race app. "
      "The relay proxies to it; the public never uses it directly.", "Tunnel (CNAME), proxied"],
     ["hut-cam.pwllhelisailingclub.org", "The camera's RTSP, published as TCP on the hut tunnel "
      "and locked to the relay with an Access service token.", "Tunnel (CNAME), proxied"],
     ["track.pwllhelisailingclub.org", "Where the boat trackers report. This one is <b>not</b> a "
      "tunnel — trackers open a raw TCP socket, which a tunnel cannot carry.", "DNS only, to your public IP"],
     ["traccar.pwllhelisailingclub.org", "Optional. Traccar's own web interface, if you choose to "
      "publish it rather than reach it over the LAN or SSH.", "Tunnel (CNAME), proxied"]],
    col_widths=[5.6 * cm, CONTENT_W - 9.4 * cm, 3.8 * cm])
story += note_box(
    "<b>track.</b> and <b>traccar.</b> cannot be the same name. A tunnel hostname has to be a "
    "CNAME to the tunnel, while the trackers need a plain A record pointing at your public IP "
    "for the forwarded port — one name cannot be both.")
story.append(PageBreak())

# ============================================================== 3
story += chapter_heading("Chapter 3", "Building the relay")
story.append(Paragraph(
    "Anything running <b>Debian 12</b> with about 1 vCPU, 512 MB–1 GB of RAM and 4 GB of disk "
    "will do. There are two ways to host it, and they differ only in where the machine lives.",
    styles["Body"]))
story += field_table([
    ("A VPS", "<b>Recommended, and what the club runs.</b> It has a public IP of its own, so "
     "the trackers reach it directly, and nothing depends on a home connection or router. Not "
     "sharing an uplink with a household is worth a good deal on a race day."),
    ("A home server", "A Debian 12 LXC on Proxmox — unprivileged, start on boot, no nesting "
     "needed. Free, and a perfectly good way to start out. The costs are that the trackers "
     "need a port forwarded on the home router, and the club's whole public face then rides "
     "on the home broadband."),
])
story.append(Paragraph(
    "Everything that follows is the same either way. <b>lxc/setup.sh</b> is a plain Debian "
    "install script despite the folder name, and the systemd units, Caddyfile and MediaMTX "
    "config are identical. As root on the relay:", styles["Body"]))
story.append(numbered([
    "Get the <b>deploy/live_stream/</b> folder from the app repository onto the machine — "
    "clone it, or on Proxmox push it from the host with "
    "<b>pct push &lt;ctid&gt; -r /path/to/deploy/live_stream /root/live_stream</b>.",
    "Run the installer. It installs MediaMTX, Caddy, cloudflared, ffmpeg, Python and Traccar, "
    "copies the config and web files into <b>/opt/relay</b>, writes <b>/etc/relay/relay.env</b> "
    "and installs the systemd services.",
]))
story += code_block(["cd /root/live_stream && bash lxc/setup.sh"])
story.append(Paragraph(
    "Then fill in <b>/etc/relay/relay.env</b>. The tunnel token comes from Chapter 4 and the "
    "camera Access token from Chapter 5, so expect to come back to this file:", styles["Body"]))
story += code_block([
    "CAMERA_URL=rtsp://USER:PASS@localhost:18554/Streaming/Channels/102",
    "MANIFEST_URL=https://pro.pwllhelisailingclub.org/api/branding/live",
    "CAMERA_FPS=15",
    "TUNNEL_TOKEN=<the pro. tunnel token, Chapter 4>",
    "CAM_HOSTNAME=hut-cam.pwllhelisailingclub.org",
    "CAM_ACCESS_ID=<Access service token Client ID, Chapter 5>",
    "CAM_ACCESS_SECRET=<Access service token Client Secret, Chapter 5>",
])
story.append(Paragraph("Start everything:", styles["Body"]))
story += code_block([
    "systemctl restart mediamtx cloudflared-tunnel cloudflared-camera",
    "systemctl reload caddy",
])
story.append(Paragraph(
    "All the services start on boot. There are five to know about:", styles["Body"]))
story += field_table([
    ("caddy", "The front door and reverse proxy. Reload after any Caddyfile change."),
    ("mediamtx", "The HLS server; starts the branded camera pull on demand."),
    ("cloudflared-tunnel", "Brings <b>pro.</b> in from Cloudflare to Caddy on port 80."),
    ("cloudflared-camera", "Opens the hut camera as <b>localhost:18554</b> on the relay."),
    ("traccar", "The GPS tracking server (Chapter 6). Only if you use tracking."),
])
story += code_block(["journalctl -u mediamtx -f          # follow any service's log",
                     "systemctl status caddy"])
story.append(PageBreak())

# ============================================================== 4
story += chapter_heading("Chapter 4", "Cloudflare: the front door and caching")
story.append(Paragraph("Point the front door at the relay", styles["H2"]))
story.append(Paragraph(
    "On the relay's dashboard-managed tunnel, add a <b>Public Hostname</b>: "
    "<b>pro.pwllhelisailingclub.org</b>, type <b>HTTP</b>, URL <b>http://localhost:80</b>. Copy "
    "that tunnel's <b>token</b> into <b>TUNNEL_TOKEN</b> in relay.env. If you are migrating from "
    "a separate front-door machine, remove <b>pro.</b> from it first — two tunnels advertising "
    "the same hostname is a confusing failure to debug.", styles["Body"]))

story.append(Paragraph("Cache the video, and only the video", styles["H2"]))
story.append(Paragraph(
    "Edge caching is what makes the stream scale: without it every viewer's segment requests "
    "come back to the relay. Add two Cache Rules under <b>Caching → Cache Rules</b>:",
    styles["Body"]))
story += field_table([
    ("Segments", "Match <b>.ts</b>, <b>.mp4</b> and <b>.m4s</b> paths on the host → "
     "<b>Eligible for cache</b>, <b>Edge TTL: Override origin → 10 seconds</b>."),
    ("Playlist", "Match <b>.m3u8</b> on the host → <b>Bypass cache</b>. The playlist changes "
     "every second; a cached one freezes the stream."),
])
story.append(Paragraph(
    "Optionally turn on <b>Smart Tiered Cache</b> for extra origin offload. Make sure "
    "<b>Development Mode is off</b> — it forces every response to bypass the cache, and it is "
    "easy to leave on after debugging.", styles["Body"]))
story += note_box(
    "<b>Never add a broad “Cache Everything” rule for pro.pwllhelisailingclub.org.</b> Keep "
    "every cache rule scoped to the video suffixes above. The race app serves its pages with "
    "<b>Cache-Control: no-store</b> — the login page in particular — and Caddy passes that "
    "straight through, so Cloudflare leaves them alone by default. A “Cache Everything” rule "
    "with <b>Edge TTL: Override origin</b> ignores <b>no-store</b> and will serve a cached login "
    "page <i>without</i> its matching session cookie, which breaks signing in with "
    "<i>“Bad request: CSRF token missing or invalid”</i> for the next visitor.", kind="warn")

story.append(Paragraph("Checking it works", styles["H2"]))
story.append(Paragraph(
    "Open the stream, then in the browser's developer tools look at the response headers of a "
    "<b>.ts</b> request. After the first <b>MISS</b> you want <b>cf-cache-status: HIT</b>. Once "
    "segments are hitting, extra viewers cost the relay nothing.", styles["Body"]))
story.append(PageBreak())

# ============================================================== 5
story += chapter_heading("Chapter 5", "The live camera stream")
story.append(Paragraph("Camera settings that matter", styles["H2"]))
story.append(Paragraph(
    "In the camera's web interface, on the <b>sub-stream</b> (the relay deliberately uses the "
    "small one):", styles["Body"]))
story.append(bullets([
    "<b>H.264</b>, not H.265 — H.264 plays in every browser with no transcoding on the relay.",
    "Around 640×480–704×576, 512–1024 kbps, 12–15 fps.",
    "<b>I-frame interval set equal to the frame rate</b> (e.g. 15). This is the single biggest "
    "latency lever: HLS can only cut a segment on a keyframe, so the Hikvision default of "
    "50–100 gives 10 s or more of delay.",
]))
story.append(Paragraph(
    "Create a <b>live-view-only camera user</b> for the relay rather than using the admin "
    "account.", styles["Body"]))

story.append(Paragraph("Publishing the camera to the relay", styles["H2"]))
story.append(Paragraph(
    "The camera stays on the hut LAN. On the hut PC's existing cloudflared tunnel, add a public "
    "hostname <b>hut-cam</b> of type <b>TCP</b> pointing at <b>CAM_IP:554</b>. Then secure it: "
    "Zero Trust → Access → Applications → a self-hosted app for that hostname with a "
    "<b>service-token</b> policy. Put the token's Client ID and Secret into relay.env. Without "
    "that policy the camera's RTSP port is effectively public.", styles["Body"]))

story.append(Paragraph("How the stream behaves", styles["H2"]))
story.append(bullets([
    "The camera is pulled <b>only while someone is watching</b>, and the pull stops about 20 "
    "seconds after the last viewer leaves.",
    "However many people watch, there is still <b>one</b> pull from the hut.",
    "Club and sponsor logos are burned in on the relay, read from the app's own branding "
    "settings — change the logos in the app, not on the relay. The manifest is re-read each "
    "time the stream starts, so restart the stream to pick up new logos.",
]))
story += note_box(
    "Turn it on in the app afterwards: <b>Settings → Video recording → Public live stream URL</b> "
    "= <b>https://pro.pwllhelisailingclub.org/live</b>. That is what makes the competitor pages "
    "show live video rather than refreshing snapshots. The relay's watch page is embedded "
    "directly in those pages, so <b>copy site/index.html to /opt/relay/site/ whenever it "
    "changes</b> — an older copy does not report that it is playing, and the app's panels stay "
    "on stills for ever.")
story.append(PageBreak())

# ============================================================== 6
story += chapter_heading("Chapter 6", "GPS tracking with Traccar")
story.append(Paragraph(
    "Boats carry Queclink <b>GL521MG</b> LTE trackers. They report to Traccar on this relay, and "
    "Traccar gets their positions to the hut app two ways at once — it answers the app's polls, "
    "and it pushes each fix as it decodes it. The live race map, the position-on-the-water list "
    "and the automated finishes all run on this.", styles["Body"]))
story.append(Paragraph(
    "Traccar is installed by <b>lxc/setup.sh</b>. It serves its web interface and REST API on "
    "port <b>8082</b>, and listens for devices on <b>a separate port per protocol</b> — one "
    "Traccar, many listeners. The port you need is the one for your device's protocol:",
    styles["Body"]))
story += header_table(
    ["Device", "Traccar protocol", "Port"],
    [["Queclink <b>GL521MG</b>, and the rest of the GL/GV series", "gl200", "<b>5004/tcp</b>"],
     ["Teltonika <b>RUTX50</b> router, FMB/FMC series", "teltonika", "5027/tcp"],
     ["Traccar Client phone app, simulate_trackers.py", "osmand", "5055 (HTTP)"]],
    col_widths=[CONTENT_W - 7.4 * cm, 4.0 * cm, 3.4 * cm])
story += note_box(
    "<b>5027 is Teltonika, not Queclink.</b> Guides before v0.197 said to use 5027 for the boat "
    "trackers — that is the port the club's RUTX50 test device uses, and it is <b>wrong for a "
    "GL521MG</b>. Point a Queclink tracker at 5027 and it reaches Traccar's Teltonika decoder, "
    "which cannot parse @Track messages: the tracker reports happily, nothing appears in "
    "Traccar, and nothing is logged anywhere to say why. Use <b>5004</b>. Traccar's own protocol "
    "reference has the port in its address — <b>traccar.org/protocol/5004-gl200/</b>.",
    kind="warn")

story.append(Paragraph("The inbound port", styles["H2"]))
story.append(Paragraph(
    "GPS tracking is the only part of the system that needs an inbound port. A tracker opens a "
    "raw TCP socket, and there is no tunnel client on the device to carry it — which is why "
    "this one cannot go through Cloudflare like everything else.", styles["Body"]))
story.append(bullets([
    "<b>On a VPS:</b> allow <b>5004/tcp</b> in the provider's firewall or security group "
    "<i>and</i> in the host firewall (<b>ufw allow 5004/tcp</b>), then point "
    "<b>track.pwllhelisailingclub.org</b> at the VPS's IP as a <b>DNS-only</b> A record — grey "
    "cloud, because Cloudflare cannot proxy a raw TCP socket.",
    "<b>On a home server:</b> forward <b>5004/tcp</b> on the router to the container, and point "
    "the same DNS-only record at your home public IP.",
]))
story += note_box(
    "Open one port per protocol you actually use, and leave the rest closed. The club currently "
    "has three: <b>5004</b> for the Queclink trackers, <b>5027</b> for the Teltonika RUTX50 on the "
    "committee boat, and <b>5055</b> for osmand. This is the only inbound access anywhere in the "
    "system — the hut PC still opens nothing at all.")
story.append(Paragraph(
    "<b>5055 is worth keeping open.</b> It carries the OsmAnd protocol, and two useful things speak "
    "it. The free <b>Traccar Client</b> phone app is the first: set its server to "
    "<b>track.pwllhelisailingclub.org:5055</b>, give it a device identifier, add that identifier on "
    "the app's Trackers page, and a phone in a pocket becomes a tracker — the cheapest way to put a "
    "rescue RIB, the committee boat or a volunteer's boat on the chart without waiting for GL521MG "
    "hardware or burning its battery. The second is <b>simulate_trackers.py</b>, which reports over "
    "the same protocol, so a whole simulated fleet can be sailed round the course through the real "
    "relay. It is plain HTTP with no authentication beyond the device identifier, so treat it as you "
    "do the other two: Traccar accepts only identifiers it knows, and nothing behind it is reachable "
    "from that port.", styles["Body"]))

story.append(Paragraph("Provisioning the trackers", styles["H2"]))
story.append(Paragraph(
    "Point each tracker at <b>track.pwllhelisailingclub.org:5004</b> with the Queclink @Track "
    "configuration commands for the model (<b>AT+GTQSS</b> / <b>AT+GTSRI</b> on this family — "
    "check the GL521MG protocol document supplied with the units). The <i>Main server "
    "IP/domain</i> field accepts a hostname.", styles["Body"]))
story += note_box(
    "The GL521MG is a battery asset tracker designed for up to a year of standby, so its "
    "out-of-the-box reporting is far too coarse to order a finish. Set a <b>fast reporting rate "
    "for race days</b> — and plan to <b>charge the units between race days</b>, because a rate "
    "quick enough to time a line crossing costs a large multiple of the standby drain. They "
    "charge wirelessly (Qi).", kind="warn")

story.append(Paragraph("Connecting the hut app", styles["H2"]))
story.append(Paragraph(
    "Caddy already exposes the REST API at <b>/traccar/*</b>. Create an API token in Traccar "
    "(user settings), then in the app: <b>Settings → GPS tracking</b> → base URL "
    "<b>https://pro.pwllhelisailingclub.org/traccar</b>, paste the token, tick <b>Enable GPS "
    "tracking</b>, save. The status box should report how many trackers are reporting.",
    styles["Body"]))

story.append(Paragraph("Pushing positions (recommended)", styles["H2"]))
story.append(Paragraph(
    "Polling costs up to a poll interval before the app sees a fix. That does not change a "
    "recorded finish <i>time</i> — crossings are interpolated between fix timestamps — but it "
    "does delay the <b>automatic horn</b> and what competitors are shown. Merge the entries "
    "from <b>traccar-forward.xml</b> into <b>/opt/traccar/conf/traccar.xml</b>, set the same "
    "secret as the <b>Push ingest token</b> in the app's Settings, and restart Traccar. Fixes "
    "then arrive the moment Traccar decodes them and finish detection runs on receipt.",
    styles["Body"]))
story += code_block([
    "# /opt/traccar/conf/traccar.xml  (see traccar-forward.xml for the full block)",
    "<entry key='forward.enable'>true</entry>",
    "<entry key='forward.type'>json</entry>",
    "<entry key='forward.url'>https://pro.pwllhelisailingclub.org/api/track/ingest</entry>",
    "<entry key='forward.header'>X-RO-Track-Token: SAME_AS_THE_APP</entry>",
    "",
    "systemctl restart traccar",
])
story += note_box(
    "A token that does not match fails <b>silently</b> — the app carries on polling, every "
    "tracker reports, and the only symptom is a late horn. Check <b>Settings → GPS tracking</b>: "
    "the status box reads <b>Push: working — N fixes received, last just now</b> when it is "
    "right. Once push is working, raise the app's poll interval to about 30 seconds; polling "
    "stays on as the safety net and back-fills anything missed while the app was down.")
story.append(Paragraph(
    "This is deliberately <b>not</b> Traccar's “forwarder-only” mode. Traccar keeps its "
    "database, so it keeps the device registry (needed to send a tracker commands, such as "
    "raising its reporting rate near the line), the history the app back-fills from, and its "
    "web interface.", styles["Body"]))

story.append(Paragraph("Automatic enrolment", styles["H2"]))
story.append(Paragraph(
    "With <b>database.registerUnknown</b> enabled, a tracker that is switched on creates itself "
    "in Traccar and then appears under <b>Trackers seen on the network</b> on the app's Trackers "
    "page, where a race officer picks its boat and presses Add — no 15-digit IMEI to read off "
    "the device. The trade-off is that the tracker port is open to the internet, so anyone who "
    "connects to it can cause a device row to appear. Nothing is tracked until a race officer "
    "assigns it to a boat; delete strays in Traccar.", styles["Body"]))

story.append(Paragraph("Reaching Traccar's own web interface", styles["H2"]))
story.append(Paragraph(
    "You rarely need it — devices, assignment and removal are all done on the app's Trackers "
    "page. It is for Traccar-side admin: users and API tokens. It does <b>not</b> work at "
    "<b>…/traccar/</b>; that page loads and then spins for ever, because Traccar's interface is "
    "a single-page app that requests its JavaScript from the <b>site root</b>, where those "
    "requests fall through to the hut app instead. Two ways that do work:", styles["Body"]))
story.append(bullets([
    "<b>On the LAN or over SSH</b> — <b>http://&lt;relay-ip&gt;:8082</b>, or from anywhere "
    "<b>ssh -L 8082:localhost:8082 root@&lt;relay&gt;</b> and then "
    "<b>http://localhost:8082</b>. Simplest, and keeps the login page off the internet.",
    "<b>Its own hostname</b> — add <b>traccar.pwllhelisailingclub.org</b> as a second public "
    "hostname on the relay's tunnel pointing at <b>http://localhost:80</b>; the Caddyfile "
    "already has the matching site block, so <b>systemctl reload caddy</b> is all this end "
    "needs.",
]))
story += note_box(
    "Publishing that hostname puts a login page on the internet. Put <b>Cloudflare Access</b> in "
    "front of it, and at the very least change Traccar's default admin password and switch "
    "Registration off under Traccar → Settings → Server.", kind="warn")
story.append(PageBreak())

# ============================================================== 7
story += chapter_heading("Chapter 7", "Knowing who visits: access logging")
story.append(Paragraph(
    "Because Caddy is the single front door, one access log covers everything — the race app, "
    "the competitor pages, the watch page and the stream. It is enabled in the Caddyfile and "
    "writes JSON to <b>/var/log/caddy/access.log</b>, rolled at 20 MiB and kept for about 90 "
    "days.", styles["Body"]))
story.append(bullets([
    "A global <b>trusted_proxies</b> / <b>client_ip_headers CF-Connecting-IP</b> block makes "
    "each line record the <b>real visitor IP</b>. Without it every request logs as "
    "<b>127.0.0.1</b> — the cloudflared peer on loopback.",
    "The high-volume HLS <b>.ts</b> segments are skipped, to keep the log about page visits.",
    "<b>Counting video viewers:</b> Cloudflare caches the segments, so most never reach the "
    "relay. Playlists are not cached, so an active viewer's periodic <b>.m3u8</b> refresh does "
    "hit Caddy — count distinct client IPs on those. For headline totals and geography across "
    "cached traffic too, use Cloudflare's own analytics.",
]))
story += note_box(
    "IP addresses are personal data under UK GDPR. Retention is bounded by the log's "
    "<b>roll_keep_for</b> setting, <b>/var/log/caddy/</b> should stay readable by root only, and "
    "the public pages should carry a short note that access is logged.", kind="warn")

story.append(Paragraph("A dashboard at /stats (optional)", styles["H2"]))
story.append(Paragraph(
    "A GoAccess HTML report can be served through the same Caddy at <b>/stats</b>; a commented "
    "block is ready in the Caddyfile. You need <b>GoAccess 1.9.2 or newer built with GeoIP2</b> "
    "— Debian's packaged version is older, so install from the official GoAccess apt repository "
    "and check with <b>goaccess --version | grep -i geo</b>.", styles["Body"]))
story.append(numbered([
    "Copy <b>relay_stats.sh</b> to <b>/opt/relay/</b>, make it executable, and run it from cron "
    "(e.g. every 10 minutes). It reads the current and rolled logs and adapts to the GoAccess "
    "version it finds.",
    "Optionally drop MaxMind GeoLite2 <b>.mmdb</b> files into <b>/opt/relay/geoip/</b> for "
    "Country, City and network panels.",
    "Make a password with <b>caddy hash-password</b>, uncomment the <b>handle /stats*</b> block "
    "and paste the hash in, then <b>caddy validate</b> and <b>systemctl reload caddy</b>.",
]))
story += note_box(
    "Do not leave <b>/stats</b> unauthenticated — it exposes visitor data. Cloudflare Access in "
    "front of the path is a stronger gate than basic auth, and can be used as well as it.",
    kind="warn")
story.append(PageBreak())

# ============================================================== 8
story += chapter_heading("Chapter 8", "Running it day to day")
story.append(Paragraph("Routine commands", styles["H2"]))
story += code_block([
    "systemctl status caddy mediamtx traccar        # is everything up?",
    "journalctl -u mediamtx -f                      # watch the stream start on demand",
    "journalctl -u traccar -f                       # watch trackers connect",
    "systemctl reload caddy                         # after editing the Caddyfile",
    "caddy validate --config /etc/caddy/Caddyfile   # check it before reloading",
])

story.append(Paragraph("After updating the app repository", styles["H2"]))
story.append(Paragraph(
    "Several files on the relay are copies of files in the app repository, and they do not "
    "update themselves. After pulling a new version, copy across whatever changed and restart "
    "the affected service:", styles["Body"]))
story += header_table(
    ["File", "Goes to", "Then"],
    [["site/index.html", "/opt/relay/site/", "nothing (served directly)"],
     ["relay_branded_source.py", "/opt/relay/", "systemctl restart mediamtx"],
     ["relay_startline.py, odm_detector.py", "/opt/relay/", "systemctl restart mediamtx"],
     ["relay_wind.py", "/opt/relay/", "systemctl restart mediamtx"],
     ["Caddyfile", "/etc/caddy/Caddyfile", "caddy validate, then systemctl reload caddy"],
     ["mediamtx.yml", "/opt/relay/", "systemctl restart mediamtx"],
     ["relay_stats.sh", "/opt/relay/", "nothing (runs from cron)"]],
    col_widths=[6.2 * cm, 4.6 * cm, CONTENT_W - 10.8 * cm])
story += note_box(
    "The watch page is the one that bites. The app's camera panels wait for it to report that "
    "it is playing before swapping from snapshots to video; an old copy reports nothing, so the "
    "panels sit on still pictures and everything <i>looks</i> like a broken stream.")

story.append(Paragraph("Health checks worth doing before a race day", styles["H2"]))
story += header_table(
    ["Check", "How", "Expect"],
    [["Front door", "open https://pro.pwllhelisailingclub.org", "the race app, not the holding page"],
     ["Live stream", "open /live", "branded video within a few seconds"],
     ["On demand", "journalctl -u mediamtx -f while opening it", "<i>runOnDemand command started</i>, then <i>stream is available</i>"],
     ["Edge caching", "DevTools → a .ts response header", "cf-cache-status: HIT after the first MISS"],
     ["Tracking", "app → Settings → GPS tracking", "<i>N tracker(s) reporting</i>"],
     ["Push", "the same status box", "<i>Push: working — N fixes received</i>"]],
    col_widths=[3.2 * cm, 6.2 * cm, CONTENT_W - 9.4 * cm])
story.append(PageBreak())

# ============================================================== 9
story += chapter_heading("Chapter 9", "Troubleshooting")
story.append(Paragraph(
    "These are the non-obvious things the working setup depends on. Nearly every fault below "
    "was met once and fixed by a specific setting, so check here before changing anything.",
    styles["Body"]))

story.append(Paragraph("The stream", styles["H3"]))
story += field_table([
    ("No video, stuck at “Connecting…”", "HLS must be <b>mpegts</b>, not low-latency. "
     "Low-latency HLS uses chunked playlist delivery that Cloudflare will not proxy, so it "
     "stalls behind the CDN."),
    ("Playlist 404s / redirect loops", "Caddy must proxy <b>/hut/*</b> <b>without</b> stripping "
     "the prefix — MediaMTX redirects to <b>/hut/index.m3u8</b> and a stripped prefix breaks it."),
    ("Player paused at 0:00 on a healthy stream", "An old <b>site/index.html</b>. From v0.187 it "
     "uses hls.js first: Chrome claims it can play HLS natively on some builds and then cannot "
     "decode it. Copy the current file to the relay."),
    ("Segments show cf-cache-status: BYPASS", "Cloudflare will not cache a response carrying "
     "<b>Set-Cookie</b> or <b>Cache-Control: private/no-cache</b>, and MediaMTX sends both — "
     "Caddy strips them on segments. Also confirm Development Mode is off."),
    ("“Timestamps are unset” / “Non-monotonous DTS”", "The camera RTSP has no usable "
     "presentation timestamps; the source script fixes it with "
     "<b>-use_wallclock_as_timestamps 1</b>."),
    ("Tears down every ~30 s, heavy buffering", "Publish to MediaMTX over <b>TCP</b> and give "
     "the input a buffer. Also do not loop the logo images at full frame rate."),
    ("“branding manifest fetch failed (403)”", "Cloudflare's bot protection blocks the default "
     "Python user-agent; the script sends a normal one. Also check Public branding is enabled "
     "in the app."),
    ("“runOnDemand … timed out” in a loop", "The branded source did not publish in time, so "
     "MediaMTX killed it mid-startup — the ffmpeg errors are the kill, not the cause. Confirm "
     "the camera itself is fine with a direct pull, then raise "
     "<b>runOnDemandStartTimeout</b>, or disable the start-line overlay to drop its extra "
     "frame grab."),
    ("“probe failed” / “Could not find codec parameters”", "Newer ffmpeg gives up before reading "
     "the sub-stream's dimensions. The relay scripts pass "
     "<b>-analyzeduration 10M -probesize 10M</b> on every RTSP read — make sure /opt/relay has "
     "the current copies."),
])

story.append(Paragraph("The front door", styles["H3"]))
story += field_table([
    ("Holding page when the hut is up", "The hut's own tunnel is down, or Caddy cannot reach "
     "<b>hut-origin</b>. Check the hut PC's cloudflared service."),
    ("“Bad request: CSRF token missing or invalid” at login",
     "Something is caching the app's HTML. Look for a “Cache Everything” rule or Page Rule on "
     "the hostname and remove it (Chapter 4)."),
    ("Caddy will not start", "<b>:2019 address already in use</b> means another Caddy is "
     "running; <b>:80 permission denied</b> means it is being run by hand rather than as the "
     "packaged service."),
])

story.append(Paragraph("Tracking", styles["H3"]))
story += field_table([
    ("A tracker never appears", "Without <b>database.registerUnknown</b>, Traccar silently "
     "rejects an id it does not know — the tracker gets no error and nothing appears anywhere. "
     "Either enable it, or add the device by IMEI first."),
    ("A device is missing from the app but visible in Traccar",
     "It probably belongs to no Traccar user — which is exactly what auto-registration creates. "
     "Traccar's own interface hides those unless <i>All Devices</i> is switched on, and their "
     "positions are not returned to the app's token at all. Adopting it in the app claims it."),
    ("“Could not read Traccar: HTTP Error 403”", "Cloudflare is bot-blocking the request. The "
     "app sends a normal user-agent from v0.167 — make sure the hut app is up to date."),
    ("Push never arrives", "The <b>forward.header</b> secret and the app's <b>Push ingest "
     "token</b> do not match, or the app is older than v0.189 and returns 400 rather than 401."),
])
story.append(PageBreak())

# ============================================================== 10
story += chapter_heading("Chapter 10", "File reference")
story.append(Paragraph(
    "Everything below lives in <b>deploy/live_stream/</b> in the app repository.",
    styles["Body"]))
story += field_table([
    ("README.md", "The working copy of these procedures, kept next to the files it describes."),
    ("lxc/setup.sh", "The installer: packages, /opt/relay, /etc/relay/relay.env, systemd units, "
     "Traccar."),
    ("lxc/systemd/", "Unit files for mediamtx, cloudflared-tunnel and cloudflared-camera."),
    ("Caddyfile", "The front door: <b>/live</b> watch page, <b>/hut/*</b> to MediaMTX, "
     "<b>/traccar/*</b> to Traccar's API, everything else proxied to the hut app with the "
     "holding-page fallback — plus the access log and the optional <b>/stats</b> block. Its "
     "<b>frame-ancestors</b> list controls who may embed the watch page; the app's own origins "
     "are in it, so reload Caddy after changing that line."),
    ("mediamtx.yml", "MediaMTX: mpegts HLS with 1-second segments, on-demand source, stream "
     "dropped 20 s after the last viewer."),
    ("relay_branded_source.py", "Pulls the camera, burns in the club and sponsor logos from the "
     "app's branding manifest, and publishes into MediaMTX. Falls back to a plain copy if "
     "branding is off or unreachable."),
    ("relay_startline.py, odm_detector.py, startline_config.json",
     "The optional start-line overlay: a colour-based detector that finds the orange ODM buoy "
     "and draws the line from the pole base to it. Best-effort and <b>prototype</b> — if "
     "anything is missing or the buoy is not confidently found, the stream simply runs without "
     "the line. Detection runs once when the stream starts, so the line is fixed for that "
     "viewing session."),
    ("relay_wind.py",
     "The optional wind readout: TWD, TWS and gust across the top of the picture, fetched from "
     "the hut's public weather endpoint and rewritten into a small file that ffmpeg re-reads as "
     "it runs, so the numbers change without restarting the stream. Off unless "
     "<b>WIND_OVERLAY=1</b>. <b>Blank rather than stale</b>: a sample older than two minutes, or "
     "one missing a direction or speed, draws nothing at all — a dropped link must not leave an "
     "old wind burned into live-looking footage. Every stream start logs which state it is in."),
    ("site/index.html", "The watch page served at <b>/live</b>, with hls.js bundled locally. "
     "Also posts its playing state to the parent window, which is how the app's camera panels "
     "know when to swap from snapshots to video."),
    ("site/hut_offline.html", "The holding page shown when the hut app is unreachable."),
    ("traccar-forward.xml", "The Traccar entries to merge for push forwarding, with the "
     "reasoning and the optional auto-enrolment setting."),
    ("relay_stats.sh", "Generates the GoAccess report for <b>/stats</b>."),
    ("docker/", "An alternative Docker Compose stack, if you would rather not install on the "
     "host directly."),
])
story += note_box(
    "The relay holds no race data. Everything it serves either comes from the hut app live, or "
    "is a config file in the repository — so rebuilding it is a matter of re-running "
    "<b>setup.sh</b> and restoring <b>/etc/relay/relay.env</b>. The one thing worth keeping a "
    "copy of is that env file, because it holds the tunnel and camera Access tokens. Traccar's "
    "own database (devices, users, tokens) lives on the relay and is <b>not</b> in the app's "
    "backups — if you use tracking, back up <b>/opt/traccar/data/</b> as well.")

doc.multiBuild(story)
print("PDF written to", OUT_PATH)
