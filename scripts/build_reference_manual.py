# -*- coding: utf-8 -*-
import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
import os
from PIL import Image as PILImage
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Image, PageBreak,
    Table, TableStyle, ListFlowable, ListItem, KeepTogether, HRFlowable, NextPageTemplate
)
from reportlab.platypus.tableofcontents import TableOfContents

G1_DIR = os.path.join(_HERE, "screenshots")
REF_DIR = os.path.join(_HERE, "ref_screens")
LOGO_PATH = os.path.join(_REPO, "static", "img", "pwllheli_sailing_club_logo.png")
OUT_PATH = os.path.join(_REPO, "docs", "Pwllheli_Race_Officer_Reference_Manual.pdf")

# Read the version from the file the release process bumps, so the cover cannot
# drift from the app the way it did between v0.197 and v0.219.
with open(os.path.join(_REPO, "VERSION"), encoding="utf-8") as _v:
    APP_VERSION = _v.read().strip()

# The install chapter walks the reader through unpacking a release, so it has to
# name the folder they will actually see. Typed in, it stuck at v0_131 while the
# app reached v0.262 — the reader's first impression of how current the manual is.
RELEASE_FOLDER = "pwllheli_race_officer_v" + APP_VERSION.replace(".", "_")

NAVY = colors.HexColor("#0f314e")
NAVY_DARK = colors.HexColor("#0a2138")
GOLD = colors.HexColor("#b9800a")
MUTED = colors.HexColor("#4b5565")
LINE = colors.HexColor("#d4dce9")
NOTE_BG = colors.HexColor("#eef5ff")
NOTE_BORDER = colors.HexColor("#b9d0ff")
WARN_BG = colors.HexColor("#fff1f0")
WARN_BORDER = colors.HexColor("#f2b8b5")
CODE_BG = colors.HexColor("#0f172a")
CODE_FG = colors.HexColor("#e2e8f0")

PAGE_W, PAGE_H = A4
MARGIN = 2.2 * cm
CONTENT_W = PAGE_W - 2 * MARGIN

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="CoverTitle", fontName="Helvetica-Bold", fontSize=28,
                           leading=33, textColor=colors.white, alignment=TA_CENTER, spaceAfter=6))
styles.add(ParagraphStyle(name="CoverSubtitle", fontName="Helvetica", fontSize=13.5,
                           leading=18, textColor=colors.HexColor("#b7c6db"), alignment=TA_CENTER))
styles.add(ParagraphStyle(name="CoverMeta", fontName="Helvetica", fontSize=10.5,
                           leading=15, textColor=colors.HexColor("#7f95ae"), alignment=TA_CENTER))
styles.add(ParagraphStyle(name="CoverClub", fontName="Helvetica-Bold", fontSize=12,
                           leading=16, textColor=GOLD, alignment=TA_CENTER))
styles.add(ParagraphStyle(name="PartLabel", fontName="Helvetica-Bold", fontSize=10.5,
                           leading=13, textColor=GOLD, spaceBefore=0, spaceAfter=2))
styles.add(ParagraphStyle(name="H1", fontName="Helvetica-Bold", fontSize=18,
                           leading=22, textColor=NAVY_DARK, spaceBefore=0, spaceAfter=4))
styles.add(ParagraphStyle(name="H2", fontName="Helvetica-Bold", fontSize=13, leading=16.5,
                           textColor=NAVY, spaceBefore=15, spaceAfter=6))
styles.add(ParagraphStyle(name="H3", fontName="Helvetica-Bold", fontSize=11, leading=14.5,
                           textColor=NAVY_DARK, spaceBefore=10, spaceAfter=4))
styles.add(ParagraphStyle(name="Body", fontName="Helvetica", fontSize=9.6, leading=13.8,
                           textColor=colors.HexColor("#1f2937"), spaceAfter=7, alignment=TA_LEFT))
styles.add(ParagraphStyle(name="Caption", fontName="Helvetica-Oblique", fontSize=8.5,
                           leading=11.2, textColor=MUTED, alignment=TA_CENTER, spaceBefore=4, spaceAfter=14))
styles.add(ParagraphStyle(name="BulletItem", fontName="Helvetica", fontSize=9.6, leading=13.8,
                           textColor=colors.HexColor("#1f2937"), spaceAfter=4))
styles.add(ParagraphStyle(name="GlossaryTerm", fontName="Helvetica-Bold", fontSize=10,
                           leading=13, textColor=NAVY_DARK, spaceBefore=9, spaceAfter=2))
styles.add(ParagraphStyle(name="NoteText", fontName="Helvetica", fontSize=9.2, leading=13,
                           textColor=colors.HexColor("#24436f")))
styles.add(ParagraphStyle(name="WarnText", fontName="Helvetica", fontSize=9.2, leading=13,
                           textColor=colors.HexColor("#842029")))
styles.add(ParagraphStyle(name="NoteLabel", fontName="Helvetica-Bold", fontSize=8,
                           textColor=colors.HexColor("#24436f"), spaceAfter=2))
styles.add(ParagraphStyle(name="WarnLabel", fontName="Helvetica-Bold", fontSize=8,
                           textColor=colors.HexColor("#842029"), spaceAfter=2))
styles.add(ParagraphStyle(name="CodeBlock", fontName="Courier", fontSize=8.4, leading=12,
                           textColor=CODE_FG))
styles.add(ParagraphStyle(name="TOCHeading", fontName="Helvetica-Bold", fontSize=10.3, leading=20,
                           textColor=colors.HexColor("#1f2937")))
styles.add(ParagraphStyle(name="TOCPart", fontName="Helvetica-Bold", fontSize=11.5, leading=26,
                           textColor=GOLD, spaceBefore=8))


def resolve(path_or_name):
    if os.path.isabs(path_or_name):
        return path_or_name
    for d in (REF_DIR, G1_DIR):
        p = os.path.join(d, path_or_name)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(path_or_name)


def fit_image(filename, max_w=CONTENT_W, max_h=13.0 * cm):
    path = resolve(filename)
    with PILImage.open(path) as im:
        iw, ih = im.size
    scale = min(max_w / iw, max_h / ih)
    img = Image(path, width=iw * scale, height=ih * scale)
    img.hAlign = "CENTER"
    return img


def figure(filename, caption_text, max_h=13.0 * cm, max_w=CONTENT_W):
    img = fit_image(filename, max_w=max_w, max_h=max_h)
    cap = Paragraph(caption_text, styles["Caption"])
    return KeepTogether([img, cap])


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
    text = "<br/>".join(l.replace(" ", "&nbsp;") for l in lines)
    p = Paragraph(text, styles["CodeBlock"])
    box = Table([[p]], colWidths=[CONTENT_W])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return [box, Spacer(1, 8)]


def part_heading(part_label, kicker, title):
    return [
        Paragraph(part_label.upper(), styles["PartLabel"]),
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
    """rows: list of (field, description) tuples."""
    col_widths = col_widths or [4.6 * cm, CONTENT_W - 4.6 * cm]
    data = [["FIELD / CONTROL", "WHAT IT DOES"]] + [
        [Paragraph(f, ParagraphStyle("ft", parent=styles["Body"], fontSize=8.6, fontName="Helvetica-Bold",
                                      textColor=NAVY_DARK, spaceAfter=0, wordWrap=None)),
         Paragraph(d, ParagraphStyle("fd", parent=styles["Body"], fontSize=9, spaceAfter=0))]
        for f, d in rows
    ]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return [t, Spacer(1, 8)]


def on_page(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, MARGIN - 10, PAGE_W - MARGIN, MARGIN - 10)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, MARGIN - 22, "Pwllheli Race Officer — Reference Manual")
    canvas.drawRightString(PAGE_W - MARGIN, MARGIN - 22, f"Page {doc.page}")
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


class RefDocTemplate(BaseDocTemplate):
    def __init__(self, *args, **kwargs):
        BaseDocTemplate.__init__(self, *args, **kwargs)
        self._bm_counter = 0

    def build(self, flowables, **kwargs):
        self._bm_counter = 0
        BaseDocTemplate.build(self, flowables, **kwargs)

    def afterFlowable(self, flowable):
        if flowable.__class__.__name__ == "Paragraph" and flowable.style.name == "H1":
            text = flowable.getPlainText()
            if text in ("About this manual", "Contents"):
                return
            self._bm_counter += 1
            key = f"h1-{self._bm_counter}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=0, closed=0)
            self.notify("TOCEntry", (0, text, self.page, key))


doc = RefDocTemplate(OUT_PATH, pagesize=A4,
                      leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
                      title="Pwllheli Race Officer - Reference Manual",
                      author="Pwllheli Sailing Club")

cover_frame = Frame(0, 0, PAGE_W, PAGE_H, id="cover", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
body_frame = Frame(MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN, id="body")
doc.addPageTemplates([
    PageTemplate(id="Cover", frames=[cover_frame], onPage=on_cover),
    PageTemplate(id="Body", frames=[body_frame], onPage=on_page),
])

story = []

# ============================================================== COVER PAGE
cover_logo = Image(LOGO_PATH, width=3.0 * cm, height=3.0 * cm * 477 / 600)
cover_logo.hAlign = "CENTER"
cover_table = Table(
    [
        [Spacer(1, 3.6 * cm)],
        [cover_logo],
        [Spacer(1, 0.55 * cm)],
        [Paragraph("PWLLHELI SAILING CLUB", styles["CoverClub"])],
        [Spacer(1, 0.8 * cm)],
        [Paragraph("Pwllheli Race Officer", styles["CoverTitle"])],
        [Paragraph("Reference Manual", ParagraphStyle("t2", parent=styles["CoverTitle"], fontSize=20, leading=24))],
        [Spacer(1, 0.5 * cm)],
        [Paragraph("Installation, hardware integration and every admin function",
                    styles["CoverSubtitle"])],
        [Spacer(1, 4.6 * cm)],
        [Paragraph("Covers: Windows setup &amp; deployment · horn, audio &amp; RTSP camera hardware ·<br/>"
                    "weather station · Cloudflare remote access · every Settings and admin page",
                    ParagraphStyle("covers", parent=styles["CoverMeta"], leading=16))],
        [Spacer(1, 0.6 * cm)],
        [Paragraph(f"Covers app version {APP_VERSION}", styles["CoverMeta"])],
        [Paragraph("Internal race-office / IT-volunteer reference · human-supervised prototype", styles["CoverMeta"])],
        [Spacer(1, 0.35 * cm)],
        [Paragraph("Copyright © 2026 CapeNet Ltd. All Rights Reserved.",
                    ParagraphStyle("coverCopy", parent=styles["CoverMeta"], fontSize=8,
                                    textColor=colors.HexColor("#5a6b80")))],
    ],
    colWidths=[PAGE_W],
)
cover_table.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
story.append(cover_table)
story.append(NextPageTemplate("Body"))
story.append(PageBreak())

# ============================================================== ABOUT
story.append(Paragraph("ABOUT THIS MANUAL", styles["PartLabel"]))
story.append(Paragraph("About this manual", styles["H1"]))
story.append(HRFlowable(width=CONTENT_W, thickness=1.4, color=GOLD, spaceAfter=12))
story.append(Paragraph(
    "This is the complete reference manual for Pwllheli Race Officer: every admin screen and setting, "
    "plus the hardware and services it integrates with — the start-hut horn, VHF/audio announcements, "
    "an RTSP IP camera for finish video, a weather station feed, and Cloudflare for remote/public access. "
    "It is written for whoever sets up and maintains the system: a race officer, a club IT volunteer, "
    "or club committee member.", styles["Body"]))
story.append(Paragraph(
    "If you only need to run a series of races day-to-day, the shorter <b>Race Officer's Guide</b> "
    "(<i>Pwllheli_Race_Officer_Series_Guide.pdf</i>) is a faster read. If you're a competitor wanting to "
    "know what the public pages show, see the <b>Competitor's Guide</b> "
    "(<i>Pwllheli_Competitor_Guide.pdf</i>). This manual is the one to reach for when installing the "
    "system, wiring up hardware, or looking up what a specific setting does.", styles["Body"]))
story += note_box(
    "Pwllheli Race Officer is a <b>human-supervised</b> tool. It automates timing, hardware signals and "
    "publishing, but the race officer and race committee remain responsible for every official decision — "
    "verify what the app shows against what is actually happening at the venue.")
story.append(Paragraph("How this manual is organised:", styles["Body"]))
story.append(bullets([
    "<b>Part I — Getting started:</b> installing on Windows, production deployment, first login and the "
    "Dashboard.",
    "<b>Part II — Boats, marks and courses:</b> the boat database and the club's fixed marks/courses.",
    "<b>Part III — Running a series of races:</b> the full race-day workflow from series setup to publishing "
    "results.",
    "<b>Part IV — Hardware integration:</b> the horn, manual horn-input sensing, central VHF audio, the "
    "weather station, off-grid hut power monitoring, and RTSP camera/video recording.",
    "<b>Part V — Remote access:</b> Cloudflare Tunnel, Cloudflare R2 public video/image publishing, and the "
    "public live camera stream.",
    "<b>Part VI — Administration and reference:</b> rating sources, polars, branding, backup/restore, "
    "environment variables, status codes and a good-practice checklist.",
]))

story.append(PageBreak())

# ============================================================== TOC
story.append(Paragraph("CONTENTS", styles["PartLabel"]))
story.append(Paragraph("Contents", styles["H1"]))
story.append(HRFlowable(width=CONTENT_W, thickness=1.4, color=GOLD, spaceAfter=12))
toc = TableOfContents()
toc.levelStyles = [styles["TOCHeading"]]
toc.dotsMinLevel = 0
story.append(toc)

story.append(NextPageTemplate("Body"))
story.append(PageBreak())

# ================================================================================================
# PART I — GETTING STARTED
# ================================================================================================
story += part_heading("Part I — Getting started", "Chapter 1", "Installing on Windows")
story.append(Paragraph(
    "Pwllheli Race Officer is a Python/Flask application served by Waitress. This chapter covers a clean "
    "install on a Windows PC; Python 3.10+ is required.", styles["Body"]))

story.append(Paragraph("1. Unpack the release", styles["H2"]))
story.append(Paragraph(
    "Create a normal, writable folder such as <b>C:\\RaceOfficer</b>, place the release ZIP there, and "
    f"extract it — you should end up with a folder like <b>C:\\RaceOfficer\\{RELEASE_FOLDER}</b>.",
    styles["Body"]))
story += note_box(
    "Do not run the app directly from the ZIP file, an email attachment, Downloads, or a OneDrive-synced "
    "folder — it needs a normal local folder it can write a database into. If Windows offered to "
    "<b>disable the path length limit</b> during Python install, accept it.")

story.append(Paragraph("2. Install Python", styles["H2"]))
story.append(Paragraph(
    "Download the 64-bit Windows installer from python.org. On the first installer screen, tick "
    "<b>“Add python.exe to PATH”</b>, then click <b>Install Now</b>. Verify from a new Command Prompt:",
    styles["Body"]))
story += code_block(["py --version", "py -m pip --version"])

story.append(Paragraph("3. Create a virtual environment and install dependencies", styles["H2"]))
story.append(Paragraph("From the app folder, in Command Prompt (not PowerShell — see note below):", styles["Body"]))
story += code_block([
    f"cd /d C:\\RaceOfficer\\{RELEASE_FOLDER}",
    "py -m venv .venv",
    ".venv\\Scripts\\activate.bat",
    "python -m pip install --upgrade pip",
    "python -m pip install -r requirements.txt",
])
story += note_box(
    "The beginner guide uses <b>Command Prompt</b> rather than PowerShell specifically to avoid PowerShell's "
    "execution-policy prompts when activating a virtual environment.")

story.append(Paragraph("4. First run", styles["H2"]))
story += code_block(["python app.py"])
story.append(Paragraph(
    "This starts Waitress on <b>0.0.0.0:5050</b> by default. Open <b>http://localhost:5050/admin</b> for "
    "the race-office dashboard, or <b>http://localhost:5050</b> for the public read-only current-race page. "
    "Leave the window open; <b>Ctrl+C</b> stops the app.", styles["Body"]))

story.append(Paragraph("5. First login", styles["H2"]))
story.append(Paragraph(
    "Username <b>admin</b>. If you haven't set a password via the environment variable below, the app "
    "generates one and writes it to <b>runtime/initial_admin_password.txt</b> the first time it runs.",
    styles["Body"]))
story.append(Paragraph("To set your own password before the database is first created:", styles["Body"]))
story += code_block(["set RO_INITIAL_ADMIN_PASSWORD=choose-a-good-password-here", "python app.py"])
story.append(Paragraph(
    "This only affects first-run account creation. To change a password later, use Settings → Users "
    "(Chapter 3). Any pre-existing default <b>admin</b>/<b>admin</b> credential is automatically replaced "
    "on startup unless <b>RO_ALLOW_DEFAULT_ADMIN=1</b> is explicitly set.", styles["Body"]))

story.append(Paragraph("Upgrading to a new version", styles["H2"]))
story.append(numbered([
    "Stop the running app (or the Scheduled Task — see Chapter 2).",
    "Back up the <b>data/</b> folder (Backup / restore in the app, or copy the folder directly).",
    "Unzip the new version into a <b>new</b> folder — don't overwrite the old one in place.",
    "Copy your existing <b>data/</b> folder into the new version's folder.",
    "Start the app and check Settings — serial ports, camera URL, weather station address and FFmpeg path "
    "carry over via <b>data/race_officer.db</b>, but are worth a quick visual check after any upgrade.",
]))
story += note_box(
    "After an upgrade, existing browser sessions are treated as stale and everyone is asked to log in again "
    "— this is deliberate, not a bug. A same-version restart does not force re-login.")

story.append(PageBreak())

# ------------------------------------------------------------ Chapter 2
story += part_heading("Part I — Getting started", "Chapter 2", "Production deployment on Windows")
story.append(Paragraph(
    "For the race-hut PC, the app should start automatically at login rather than being run from a Command "
    "Prompt window left open. The <b>deploy\\windows\\</b> folder provides a Windows Scheduled Task set up "
    "for this, registered under the task name <b>“Pwllheli Race Officer”</b>.", styles["Body"]))

story.append(Paragraph("The five deployment scripts", styles["H2"]))
story += (field_table([
    ("install_startup_task", "Prepares the virtual environment (via start_race_officer -SetupOnly), then "
     "registers the Scheduled Task: trigger “at logon” for the current user, hidden window, auto-restart up "
     "to 3 times, and starts it immediately."),
    ("start_race_officer", "The actual worker script, used both by the task and for manual/diagnostic runs. "
     "Creates runtime folders, creates the venv if missing, reinstalls dependencies only when "
     "requirements.txt has changed (hash-checked), sets default RO_HOST/RO_PORT/RO_THREADS, and logs to "
     "runtime\\logs\\race_officer.log."),
    ("restart_startup_task", "Stops and restarts the Scheduled Task — use after an upgrade or a hardware "
     "settings change that needs a fresh process."),
    ("status_startup_task", "Reports task state, last run result, does a live HTTP check against "
     "/admin, and tails the last 20 lines of the log file."),
    ("uninstall_startup_task", "Stops and removes the Scheduled Task. Does not delete the app folder or "
     "data — safe to run before installing a new version's task."),
]))
story.append(Paragraph(
    "Typical order of use: <b>install</b> once, <b>status</b> to confirm it's healthy, <b>restart</b> after "
    "changes, <b>uninstall</b> before moving to a new version folder (then <b>install</b> again from the new "
    "folder, since the task points at the folder it was installed from).", styles["Body"]))

story += note_box(
    "The task deliberately runs <b>non-elevated</b>, in the signed-in user's own interactive session (not "
    "as a Windows Service). This is required for desktop audio (VHF announcements), USB/serial horn "
    "hardware, and cameras — services have restricted access to interactive audio and USB devices. If a "
    "black console window is left open after login, that means an old-style task/script is installed; fix "
    "by re-running uninstall then install.")

story.append(Paragraph("Environment variables", styles["H2"]))
story += (field_table([
    ("RO_HOST", "Bind address for Waitress. Default 0.0.0.0 (all network interfaces)."),
    ("RO_PORT", "Listening port. Default 5050."),
    ("RO_THREADS", "Waitress worker thread count. Default 8."),
    ("RO_SECRET_KEY", "Flask session signing key. If unset, a random key is generated once and persisted to "
     "runtime/secret_key.txt so restarts don't log everyone out. Set a strong value explicitly for any "
     "internet-facing deployment (see Chapter 17)."),
    ("RO_INITIAL_ADMIN_PASSWORD", "Sets the first-run admin password. Only effective before the database/"
     "first admin user exists."),
    ("RO_ALLOW_DEFAULT_ADMIN", "Set to 1 to stop the app auto-replacing an existing insecure admin/admin "
     "credential pair."),
    ("RO_WEATHER_ALLOWED_HOSTS", "Comma-separated hostname allow-list further restricting which weather-"
     "station addresses are accepted (see Chapter 14)."),
], col_widths=[6.6 * cm, CONTENT_W - 6.6 * cm]))

story += note_box(
    "Windows-specific gotchas: check for <b>case-only duplicate paths</b> before zipping a release (Windows "
    "treats them as the same file); keep the race-office PC's clock <b>NTP or GPS disciplined</b> — every "
    "signal and finish time depends on it; and don't let the PC sleep during racing.", kind="warn")

story.append(PageBreak())

# ------------------------------------------------------------ Chapter 3
story += part_heading("Part I — Getting started", "Chapter 3", "First login, users and the Dashboard")
story.append(Paragraph("Logging in", styles["H2"]))
story.append(Paragraph(
    "Open <b>http://&lt;pc-address&gt;:5050/admin</b> and sign in. The public competitor pages "
    "(<b>Competitor's Guide</b>) never require a login — only the race-office side does.", styles["Body"]))
story.append(figure("crop_login.png", "The sign-in page.", max_h=7.5 * cm))

story.append(Paragraph("User roles", styles["H2"]))
story.append(Paragraph(
    "Every login account has one of two roles:", styles["Body"]))
story.append(bullets([
    "<b>Admin</b> — full access to everything, including Settings and restoring a backup.",
    "<b>Race officer</b> — can run races and use everything needed on race day, but <b>Settings is "
    "read-only</b> and <b>restoring a backup is not allowed</b> (downloading one is fine). Opening Settings as "
    "a race officer shows every value greyed out, with a “Read-only · administrator access required” note in "
    "place of the Save button. The limit is enforced on the server too, not just hidden in the page.",
]))
story.append(Paragraph("Managing users (Settings → Users)", styles["H2"]))
story.append(Paragraph(
    "Users management is admin only. Every race-office login is a row here: username, display name, role "
    "(an <b>Admin</b>/<b>Race officer</b>/<b>Mark layer</b> dropdown), whether it may set mark positions, "
    "status (Active/Inactive), and last login. Each row has "
    "its own inline Save (edit display name, role, status, or set a new password) and a Delete button — except "
    "for the account you're currently signed in as. Add a user with the form at the bottom: Username, Display "
    "name, Role and Password. At least one active administrator must always remain, so the app refuses to "
    "demote, deactivate or delete the last admin.", styles["Body"]))
story.append(figure("s_users.png", "Settings → Users: existing accounts and the add-user form.", max_h=11 * cm))
story.append(Paragraph("The three roles", styles["H2"]))
story.append(Paragraph(
    "<b>Admin</b> changes everything, including Settings and restoring a backup. <b>Race officer</b> runs "
    "races and uses everything race day needs, but sees Settings read-only and cannot restore a backup "
    "(they can still download one). <b>Mark layer</b> is much smaller than either: it reaches the phone page "
    "for re-measuring a mark and nothing else. Signing in lands straight on that page, and any other page "
    "sends them back to it.", styles["Body"]))
story.append(Paragraph(
    "The reason for a third role is that the person who takes a RIB out after a storm is often neither an "
    "administrator nor the duty race officer, and that job needs one button on a phone — not the start "
    "sequence, the finish times and the results as well. A race officer who should <i>also</i> be able to do "
    "it gets the <b>Set marks</b> tickbox instead, so they do not need a second account. Administrators and "
    "mark layers always may, so the tickbox is not shown on their rows.", styles["Body"]))
story.append(Paragraph("Changing your own password", styles["H2"]))
story.append(Paragraph(
    "Because Settings is read-only for race officers, every signed-in user has a <b>My account</b> page for "
    "changing their <i>own</i> password — open it from the username in the top bar (or go to <b>/account</b>). It "
    "asks for the current password to confirm the change, enforces a minimum length, and keeps you signed in. "
    "Administrators can still reset anyone's password from Settings &#8594; Users.", styles["Body"]))

story.append(Paragraph("The Dashboard", styles["H2"]))
story.append(Paragraph(
    "The landing page after login. A single at-a-glance view: the currently active race and its countdown, "
    "live wind, horn/video configuration status, and the club's own start-line, finish-line and radio notes.",
    styles["Body"]))
story.append(figure("02_dashboard.png", "The Dashboard."))

story.append(PageBreak())

# ================================================================================================
# PART II — BOATS, MARKS AND COURSES
# ================================================================================================
story.append(Paragraph("Finding today's race", styles["H2"]))
story.append(Paragraph(
    "The side menu carries a <b>Current race</b> link between Dashboard and New race, which opens the race sheet "
    "for the race the club is running now. The current race is the latest one that still has boats <b>racing</b>, "
    "and otherwise simply the latest race — so creating a new race sheet does not take the screen away from a "
    "race still being sailed. If a display or a page seems stuck on the wrong race, look at the previous race's "
    "sheet for a boat nobody finished or retired.", styles["Body"]))

story += part_heading("Part II — Boats, marks and courses", "Chapter 4", "The boat database")
story.append(Paragraph(
    "Boats are stored once, with both IRC and YTC ratings, so they don't need re-entering for every race. "
    "A rating is copied into a race entry as a snapshot the moment the boat is added to that race — editing "
    "the boat record later only affects races you add it to afterwards (an individual entry's rating can "
    "still be corrected on the race's Entries &amp; finish times tab).", styles["Body"]))
story.append(figure("boats_list.png", "The Boat database: search, and the boat list with IRC/YTC ratings.",
                     max_h=10 * cm))

story.append(Paragraph("Adding a boat", styles["H2"]))
story.append(Paragraph("There are two ways to add or update a boat, both from Boats → Add / lookup boat:",
                        styles["Body"]))
story.append(bullets([
    "<b>Search IRC + YTC</b> — type a boat name or sail number and the app queries both configured rating "
    "sources at once (Chapter 18), showing matching rows from each list side by side. Selecting a row copies "
    "its fields into the form below.",
    "<b>Manual entry</b> — type the details directly: boat name, sail number, owner, design, "
    "IRC TCC, YTC rating, IRC certificate number/issue date/year, club and notes.",
]))
story.append(figure("boat_form.png", "Add / lookup boat: search both rating sources, or fill in the form "
                                     "manually.", max_h=15.5 * cm))
story.append(Paragraph(
    "Two further pages support <b>bulk</b> import: importing a page of matches from the configured IRC CSV "
    "listing, and importing from the configured YTC Google Sheet/CSV listing — useful for loading a whole "
    "fleet at the start of a season rather than one boat at a time.", styles["Body"]))
story += note_box(
    "The <b>Include inactive</b> checkbox on the Boats list is off by default, so deactivated boats stay out "
    "of the way without being permanently deleted.")

story.append(PageBreak())

# ------------------------------------------------------------ Chapter 5
story += part_heading("Part II — Boats, marks and courses", "Chapter 5", "Marks and courses")
story.append(Paragraph(
    "The Marks page lists the club's marks. <b>Administrators</b> can <b>add</b> a simple mark (code, name, "
    "position in decimal degrees, buoy and top-mark — the position text is generated automatically) and "
    "<b>delete</b> a mark that is not in use, directly from this page. A mark cannot be deleted while it is "
    "used in a fixed course or as the start/finish seaward mark, or if it is a compound mark or one of its "
    "components — the page shows <b>In use</b> with the reason instead of a Delete button. Race officers see "
    "the list read-only. Fixed courses, the start/finish line and compound marks are still edited by hand in "
    "the underlying JSON files (<b>data/courses.json</b>, <b>data/start_finish.json</b>, and compound entries "
    "in <b>data/marks.json</b>).", styles["Body"]))
story.append(Paragraph("Waypoints", styles["H2"]))
story.append(Paragraph(
    "A <b>waypoint</b> bends a leg round a headland. It is not a mark: boats are not asked to round it, it is "
    "absent from the course board, the announcement, the shortening options and a boat's marks-rounded count, "
    "and nothing is drawn for it on the chart. The course changes direction there and that is all.", styles["Body"]))
story.append(Paragraph(
    "It exists because a straight line from mark 2 to the Gwylan Islands crosses Mynytho, Llangian and Trwyn "
    "Cilan. Drawing a course over land is the visible half; the arithmetic is the rest. That leg measures "
    "<b>10.90 nm</b> straight against <b>13.06 nm</b> round the corner — 20% short, for the whole leg, in "
    "distance-to-go and in the projected leaderboard — and a single bearing of 243°T stands in for "
    "211°T then 278°T. With the wind at 145°T that is a 66° TWA reach and a 133° TWA "
    "broad reach being modelled as one leg at 98°, so the polar projection is wrong for both halves.",
    styles["Body"]))
story.append(Paragraph(
    "Add one on the Marks page with the <b>Waypoint</b> box ticked; it takes no rounding radius. In the manual "
    "course builder it offers a single <b>Add</b> rather than Port and Starboard, since boats pass a turning "
    "point rather than leaving it on a hand, and it appears as a grey <b>via</b> chip. It can be used more than "
    "once in a course — an out-and-back round the Gwylans wants it on both legs. The club's two are "
    "<b>TC</b> (Trwyn Cilan) and <b>TP</b> (Porth Ceiriad).", styles["Body"]))
story += note_box(
    "A boat passes a waypoint by <b>drawing level with it</b>, not by coming near it — a gate rather than a "
    "circle. A radius cannot do the job: boats beating past a headland pass a long way offshore, so it would "
    "have to be a mile or more, and the radius test fires on proximity alone with no departure test to hold it "
    "back — a 2 km radius on a waypoint 12.6 km down the leg would advance the boat while it was still 84% "
    "short of the corner. The gate cannot fire early and does not care how far off the boat passes.")
story.append(Paragraph(
    "A waypoint used by a fixed course, or by any race's made-up course, cannot be deleted; the app names the "
    "race. Deleting one would straighten a leg back across the land on a race already sailed, changing its "
    "recorded length and bearings after the event. Full detail in <b>docs/WAYPOINTS.md</b>.", styles["Body"]))

story.append(figure("marks_top.png", "The Marks page (foot of the list): the club's two waypoints, marks that are "
                                     "in use and so cannot be deleted, and the Add a mark form.",
                     max_h=11 * cm))

story.append(Paragraph("Marks move: editing and re-measuring", styles["H2"]))
story.append(Paragraph(
    "A laid mark drags in a storm, and the app then looks for boats rounding a buoy that is no longer there. "
    "That used to be as bad as it sounds: rounding was judged within a radius of the mark's <i>recorded</i> "
    "position, and the course walk is <b>sequential</b>, so a buoy 60&#160;m adrift made every boat read as "
    "never having rounded it and stalled every mark behind it — a fleet stuck at the same mark and no GPS "
    "finishes at all. The rounding gate absorbs a drag of that order (Chapter 24): it reaches 750&#160;m "
    "along a line through the mark on the hand the course requires, so it asks which side of the mark a boat "
    "went, not how close it came.", styles["Body"]))
story.append(Paragraph(
    "What a wrong position still costs is worth stating, because it no longer announces itself. Distance to "
    "go, the leg bearings and the chart all measure to the recorded position. And a boat passing the correct "
    "side of the <i>buoy</i> but the wrong side of the <i>recorded</i> position is on the hand the gate barely "
    "reaches, leaving only the radius and the departure test to catch it. So a position is not a constant: it "
    "is a measurement that needs retaking after heavy weather. There are two ways.", styles["Body"]))
story.append(Paragraph(
    "<b>By typing it in.</b> <b>Marks &#8594; Edit</b> on any simple mark, for a position from a chart "
    "plotter or a survey. Administrators only. The same panel carries the mark's own <b>rounding radius</b>: "
    "leave it blank and the mark follows the figure in Settings (50&#160;m by default), including when that "
    "figure changes later. Set it only for a mark that needs its own — a buoy on a long scope swings a wide "
    "circle, while the same generous figure at a mark rounded twice in one course would start counting "
    "roundings that never happened.", styles["Body"]))
story.append(figure("marks_edit.png", "Marks → Edit: position, buoy, top mark and the mark's own rounding "
                                      "radius. Blank follows the Settings value.", max_h=9 * cm))
story.append(Paragraph(
    "<b>From a phone on the water.</b> <b>Marks &#8594; Set a position from the water</b> is a page of its "
    "own, built to be used one-handed in a small boat: take the RIB to the mark, wait for the fix to settle, "
    "pick the mark and set it. It shows the position and the accuracy the phone claims, and how far the mark "
    "is about to move — the button reads <i>“Move 4 60&#160;m to here”</i> rather than “Save”. A fix the "
    "phone itself calls worse than 25&#160;m is refused, and a move of more than 2&#160;km must be confirmed; "
    "both are enforced on the server, not only in the page.", styles["Body"]))
story.append(figure("marks_ping.png", "Set a position from the water: the fix and its accuracy, the mark "
                                      "picker, and how far the mark will move.", max_h=12 * cm))
story.append(Paragraph(
    "It needs the club's <b>https</b> address: browsers do not give a position to a page served over plain "
    "http, so this cannot work from a bare LAN address however many times the button is pressed. If the page "
    "sits on “waiting for GPS…”, the phone has not been asked or has refused — and on an iPhone <i>each "
    "browser</i> is granted location separately, under its own name in Settings &#8594; Privacy &amp; "
    "Security &#8594; Location Services, so allowing it for Safari does nothing for Chrome. The page names "
    "which browser it is in and what to check.", styles["Body"]))
story.append(Paragraph(
    "Every change records <b>who set it, when, from what, and the accuracy claimed</b>, and keeps the "
    "position it replaced. That history is read: a race is drawn, replayed and analysed with the marks as "
    "they stood when it was sailed, so correcting a mark today does not redraw races already run. A race is "
    "pinned to <i>when it ended</i>, which is what makes the usual case work — a drag is normally discovered "
    "mid-race, and a correction made while the race is still being sailed counts for that race, including "
    "the boats that had already rounded.", styles["Body"]))

story.append(Paragraph("Compound marks", styles["H2"]))
story.append(Paragraph(
    "Some marks represent a physical feature — an island group, or a pair of rocks — rather than a single "
    "buoy. A compound <b>parent</b> mark (e.g. <b>Y</b> = Gwylan Islands, <b>A</b> = Carreg Y Trai/St "
    "Tudwal's Islands) has no coordinates of its own; it lists real-mark <b>components</b> (e.g. Ya/Yb) and "
    "a port/starboard rounding order. Course boards, the public course sequence and audio announcements only "
    "ever name the parent (e.g. “round Yp”), while the course chart, leg analysis and length calculations "
    "expand it to the real physical corner points. The manual course builder only offers the parent marks, "
    "never the raw corners.", styles["Body"]))
story.append(figure("marks_compound.png", "Compound marks Y and A, with their real-position components "
                                          "Ya/Yb and Aa/Ab.", max_h=10 * cm))

story.append(PageBreak())

# ================================================================================================
# PART III — RUNNING A SERIES OF RACES
# ================================================================================================
story += part_heading("Part III — Running a series of races", "Chapter 6", "Series setup")
story.append(Paragraph(
    "A series (e.g. “Autumn Series 2025”) is a named group of races scored together with discards. "
    "<b>Add series</b> on the <b>Series</b> page opens a form carrying the whole setup — name, discards, "
    "rating bands and default start plan — so a series arrives configured rather than blank. It is the "
    "same form as <b>Edit series details</b> on the series itself, which is where it is changed afterwards.",
    styles["Body"]))
story.append(figure("03_series_list.png", "The Series page. Winter Series 2027 has no races in it yet and can be deleted; every series that has been raced shows In use instead."))
story.append(Paragraph(
    "A series is <b>deleted</b> from the same list, or from the bottom of <b>Edit series details</b>, and "
    "only when nothing is in it: one still holding races shows <i>In use</i> and names how many are in the "
    "way. Empty it first — each race's series is changed on its own “Course &amp; start” tab, to another "
    "series or to none — or delete the races. Neither a cascade nor an orphan is offered on purpose: a "
    "series carries the rating bands, start plan and discard profile its races were <i>scored under</i>, so "
    "deleting it out from under them would drop a season from the standings while every race went on "
    "claiming to belong to it.", styles["Body"]))

story.append(Paragraph("Discards and constitution", styles["H2"]))
story.append(Paragraph(
    "The <b>discard profile</b> is a comma-separated list (e.g. <b>0,0,1,1,1,1,2,2,2</b>) giving the number "
    "of a competitor's worst scores dropped once 1, 2, 3… races are completed; the last value is reused if "
    "more races are sailed than the list has entries. <b>Minimum races to constitute series</b> sets when a "
    "class's results stop being labelled provisional.", styles["Body"]))
story.append(figure("crop_classes.png", "Discard profile, constitution threshold, and the series-level "
                                         "publish/export buttons (Chapter 12).", max_h=9 * cm))

story.append(Paragraph("Rating-band classes", styles["H2"]))
story.append(Paragraph(
    "Up to three IRC and three YTC classes, each with a name, a numeral class flag, and an optional rating "
    "band (e.g. IRC Class 1 = 1.000 and above; leave a bound blank for open-ended). Boats are grouped into "
    "these classes automatically by their rating.", styles["Body"]))
story.append(figure("crop_classes2.png", "IRC rating-band classes with numeral flags and rating bands.",
                     max_h=9 * cm))

story.append(Paragraph("Default start plan", styles["H2"]))
story.append(Paragraph(
    "Up to six starts, each with a time offset in minutes from the first actual start (five minutes after "
    "the first warning signal) and a choice of which classes start on it. This becomes the default for every "
    "new race added to the series; it can still be reviewed per race.", styles["Body"]))
story.append(figure("crop_startplan.png", "The default start plan.", max_h=8 * cm))

story.append(PageBreak())

# ------------------------------------------------------------ Chapter 7
story += part_heading("Part III — Running a series of races", "Chapter 7", "Creating a race and setting its course")
story.append(Paragraph(
    "Race creation is deliberately minimal: name, series, and an optional bulk-add of every active boat. "
    "A race takes its classes from its series' rating bands, so there is nothing to type about class or "
    "fleet. The course and warning time are set next, on the race sheet itself.",
    styles["Body"]))
story.append(figure("07_new_race_form.png", "Creating a race."))

story.append(Paragraph("Choosing the finish line", styles["H2"]))
story.append(Paragraph(
    "Almost every race finishes where it starts, on the club line between the CHPSC Bridge window and the "
    "ODM (mark <b>O</b>), and the <b>Finish line</b> selector on the Course &amp; start tab can be left "
    "alone. Some races are not sailed to it: an <b>ISORA</b> passage race finishes on the transit between "
    "the Pwllheli Fairway Buoy (mark <b>F</b>) and the bridge at Plas Heli, bearing 297° magnetic — a line "
    "about 1.6&#160;km long whose shore end is nearly 800&#160;m from the club one. Pick it and everything "
    "follows: the chart draws it, GPS finishes are detected on it, and the competitor page and clubhouse "
    "display show it.", styles["Body"]))
story.append(Paragraph(
    "<b>The start line does not change.</b> Races start on the club line whatever they finish on, so with a "
    "different finish selected the chart draws two lines, marked <i>Start line</i> and <i>Finish line</i>. "
    "Boats <b>cross</b> the finish line rather than rounding the mark at the end of it: when a course's last "
    "mark is the finish line's own mark — O on the club line, F on the ISORA one — no rounding is required "
    "there. A course ending anywhere else, including a shortened one, still has to reach the line. The "
    "seaward end of each line is a mark, so re-measuring it moves the line with it; ping F from the water "
    "and the ISORA finish follows.", styles["Body"]))
story.append(Paragraph(
    "Getting this wrong does not fail loudly. The crossing test simply never fires, and every boat sits "
    "unfinished at the end of a race that has plainly finished — so set it before the start.", styles["Body"]))

story.append(Paragraph("Course & start tab", styles["H2"]))
story.append(Paragraph(
    "<b>A “?” beside a heading opens the explanation.</b> The race page keeps the controls at the top and the reasoning one click away, so what stays on screen is what changes what you do next — <i>AP is up since 14:02</i>, <i>this race has started</i>, <i>whole minutes only</i>. The <i>why</i> and the worked examples are behind the <b>?</b>, and everything in those popups is also in this manual.", styles["Body"]))
story.append(Paragraph(
    "Set the <b>first warning signal time</b> — the actual start always follows five minutes later. Choose a "
    "course by picking a fixed course number, using <b>Recommend course</b> to have the app suggest "
    "a standard course from the live start-hut wind and a chosen boat polar, or <b>Build manual course</b> "
    "to lay out a one-off course from individual marks.", styles["Body"]))
story.append(figure("crop_course_top.png", "Course & start: warning-signal time and course selection.",
                     max_h=9 * cm))
story.append(Paragraph(
    "Once a course is selected, the course chart and a predicted leg-by-leg timing table (distance, bearing, "
    "suggested sail and target boat speed) are calculated from the chosen polar and latest wind.",
    styles["Body"]))
story.append(figure("crop_course_bottom.png", "The Course &amp; start tab below the form: the course chart, the fleet's position on the water, and the predicted leg-by-leg timing.", max_h=15.5 * cm))

story.append(PageBreak())

# ------------------------------------------------------------ Chapter 8
story += part_heading("Part III — Running a series of races", "Chapter 8", "Adding entries")
story.append(Paragraph("On the Add entries tab, boats are added from the boat database, either individually or "
                       "in bulk:", styles["Body"]))
story.append(bullets([
    "<b>Bulk add</b> — every active boat, or every boat already in a given class, in one click.",
    "<b>Add from boat database</b> — look up one boat and add it, optionally overriding its class for this "
    "race.",
]))
story.append(figure("09_race_tab2_entries.png", "Add entries: current entries, bulk-add and add-from-database "
                                                 "controls."))
story += note_box(
    "Boats without a rating for a given system are still entered — they're simply excluded from that "
    "system's result table until a rating is added.")

story.append(PageBreak())

# ------------------------------------------------------------ Chapter 9
story += part_heading("Part III — Running a series of races", "Chapter 9", "Running the start sequence")
story.append(Paragraph(
    "The Start console & log tab is the race-day working screen. The scheduled signal plan is generated "
    "automatically from the warning time and start plan.", styles["Body"]))
story.append(figure("10_race_tab3_start.png", "Start console &amp; log: postponing, manual controls, the "
                                               "scheduled signal plan and the horn/race event log."))
story.append(Paragraph("Manual controls cover situations the schedule can't predict:", styles["Body"]))
story.append(bullets([
    "<b>Fire horn now</b> — sound the horn immediately, outside the scheduled plan.",
    "<b>Individual recall</b> — log that specific boats were called back over the line.",
    "<b>General Recall</b> — log a full recall of the start.",
    "<b>Postpone (AP)</b> — the real thing, not a note: two horn blasts, the start sequence held, and AP on every page that shows flags. See below.",
    "<b>Abandon</b> — log an abandoned race.",
]))
story += note_box(
    "The visual flag panel models the normal warning/preparatory/start class-flag sequence, but does not "
    "model every recall, postponement or abandonment flag combination — the race officer remains responsible "
    "for the actual flags flown and the on-the-water decision.", kind="warn")

story.append(Paragraph("Postponing a start", styles["H2"]))
story.append(Paragraph(
    "If the wind dies, the line is not ready or the fleet is not there, <b>postpone — do not move the "
    "start time</b>. Moving it signals nothing to the fleet, and if the sequence is already running it "
    "drops the rest of it: boats hear a warning, then no preparatory signal and no gun. Postponing is on "
    "this tab because this is the tab that is open before a start.", styles["Body"]))
story += field_table([
    ("AP", "Races not started are postponed. The warning signal will be made one minute after "
           "AP is removed."),
    ("AP over H", "Races not started are postponed. Further signals ashore."),
    ("AP over A", "Races not started are postponed. No more racing today."),
])
story.append(Paragraph(
    "Flying one sounds <b>two horn blasts</b>, holds the start sequence — no warning, preparatory or "
    "starting signal sounds while the flag is up — shows the flag on the race sheet, the competitor pages "
    "and the clubhouse display, and records it in the race log. The race keeps its scheduled time: nothing "
    "is rescheduled behind anybody’s back.", styles["Body"]))
story.append(Paragraph(
    "When the wind is back, say <b>when AP comes down</b> — whole minutes, as with the first warning "
    "signal, and at least ninety seconds away so the announcement a minute beforehand has room. <b>The "
    "warning signal is one minute after that and the gun five minutes after the warning</b>, so choosing "
    "14:20 gives a warning at 14:21 and a first gun at 14:26. That is the point of the flag: you decide "
    "when you are ready, not what time the wind will return. The fleet is told when you set the time, again "
    "if you change it, a minute before the flag moves, and once more as it comes down with its single "
    "sound.", styles["Body"]))
story += note_box(
    "AP postpones races that have <i>not started</i>. After the first gun the button is not offered: stopping "
    "a race under way is abandonment (flag N), which this app does not signal — if the wind has died "
    "mid-race, shorten the course instead. A race postponed <i>before</i> its gun can always still be "
    "lowered, so the flag cannot get stuck up.", kind="warn")
story.append(Paragraph(
    "The scheduled signal plan below follows the postponement: signals due before the flag comes down are "
    "struck through and marked <i>held</i>, because they will not be made, and the announcement and the flag "
    "coming down appear as rows of their own. The start video follows it too — the clip booked for the "
    "old gun is cancelled, and a new one booked for the gun that actually happens.", styles["Body"]))
story.append(figure("race_tab_start_postponed.png", "AP flying: the flag in the panel, the clock reading "
                                                    "<i>Postponed</i>, the minute it comes down and the times "
                                                    "that follow from it — and the signal plan with the held "
                                                    "signals struck through above the two AP rows."))

story.append(PageBreak())

# ------------------------------------------------------------ Chapter 10
story += part_heading("Part III — Running a series of races", "Chapter 10", "Recording finishes")
story.append(Paragraph(
    "As boats cross the line, use the Entries & finish times tab. The blue <b>Finish</b> button records the "
    "finish time, sounds the horn and adds a log entry in one action. For anything other than a normal "
    "finish, set a status instead (see the status-code table in Chapter 21).", styles["Body"]))
story.append(figure("11_race_tab4_finish.png", "Entries & finish times: one row per boat with its ratings, "
                                                "status and finish/elapsed time."))
story.append(Paragraph(
    "Each entry's IRC/YTC rating is editable here — useful for a post-entry correction. A live finish-camera "
    "view can also be shown from this tab when a camera is configured (Chapter 16).", styles["Body"]))
story.append(Paragraph("Shortening the course", styles["H2"]))
story.append(Paragraph(
    "If the wind drops or time runs short, the <b>Shorten course</b> tab (between <i>Start console &amp; log</i> "
    "and <i>Entries &amp; finish times</i>) shortens the course at a mark: choose a mark from the course sequence "
    "and press <b>Call shortened course</b>. The app sounds <b>two horn blasts</b> and then — once the horns have "
    "finished, so it is not drowned out — makes a central-audio announcement — “Shortened course called on mark "
    "&lt;mark&gt; — after this mark proceed to finish” — repeated a few seconds later. It flies International Code "
    "flag <b>S</b> (blue square on white) in the flag panel on both the race and public competitor pages until all "
    "boats are no longer racing, records the call in the race log, shows a <b>Shortened course</b> banner, and "
    "truncates the shown course (and predicted-time/leg analysis) to end at that mark with <b>→ Finish</b> after it "
    "— the course chart drawing a dashed final leg from that mark directly to the finish line. Boats round the "
    "chosen mark and sail directly to the finish; finish times are recorded here as usual. <b>Clear shortened "
    "course</b> undoes a mistaken call. The announcement needs central audio enabled (Chapter 13) to be heard over "
    "the VHF/PA.", styles["Body"]))
story.append(figure("race_tab_shorten.png", "The Shorten course tab: pick a mark from the course and press "
                                            "Call shortened course.", max_h=11 * cm))

story.append(PageBreak())

# ------------------------------------------------------------ Chapter 11
story += part_heading("Part III — Running a series of races", "Chapter 11", "Race and series results")
story.append(Paragraph(
    "The Results tab calculates corrected results as soon as finish times/statuses are recorded — there is "
    "no separate “compute” step. IRC corrected time = elapsed × IRC TCC; YTC corrected time = elapsed × 1000 "
    "÷ YTC number. Separate tables are produced per rating-band class, with an overall table where classes "
    "share a start.", styles["Body"]))
story.append(figure("crop_results_top.png", "Race results (1 of 2): status and the first results table.",
                     max_h=15.5 * cm))
story.append(PageBreak())
story.append(figure("crop_results_bottom.png", "Race results (2 of 2): remaining classes and any boats "
                                                "excluded from a table for missing a rating.", max_h=15.5 * cm))
story.append(Paragraph(
    "<b>Download IRC/YTC CSV</b> exports this race's results. On the series page, <b>Series results</b> "
    "rolls every scored race into running totals automatically — discarded scores appear struck through, "
    "ties are broken per RRS Appendix A8 (race ties use A7 equal-points scoring), and each table shows "
    "whether it is yet constituted.", styles["Body"]))
story.append(figure("06_series_detail_results.png", "Series results, with running totals and discards."))

story.append(PageBreak())

# ------------------------------------------------------------ Chapter 12
story += part_heading("Part III — Running a series of races", "Chapter 12", "Publishing results")
story.append(Paragraph(
    "Publishing is done from the <b>Series</b> page and always produces a document covering every race in "
    "the series: <b>Download CSV</b> (spreadsheet), <b>Preview HTML</b> (opens it in a tab to look at), "
    "<b>Download HTML</b> (a single self-contained file to upload by hand) and, from v0.281, "
    "<b>Publish to website</b>.", styles["Body"]))
story.append(Paragraph(
    "<b>Publish to website</b> uploads the document to the club's public Cloudflare R2 bucket — the "
    "same bucket the public race videos use, configured under Settings \u2192 Video recording, so there "
    "is one set of credentials and one public base URL rather than two. The button is hidden until that "
    "bucket is configured, and each series publishes into <b>results/series-&lt;id&gt;/</b>.",
    styles["Body"]))
story.append(Paragraph(
    "Every publish writes <b>two</b> objects: a dated one, cached for a year and never overwritten, "
    "which is the permanent record of that publish; and <b>latest.html</b>, overwritten each time and "
    "cached for only sixty seconds. The short cache is the point of the pair. The latest link is what "
    "goes on the club website, and a long cache there would leave Cloudflare serving Saturday's "
    "standings on Wednesday. The dated key carries seconds, so two publishes a minute apart are two "
    "records rather than one file with two entries pointing at it.", styles["Body"]))
story.append(Paragraph(
    "<b>Published\u2026</b> lists what has gone out, with the club-website link in a field to copy. The "
    "app records each publish in its own <b>published_results</b> table rather than listing the bucket, "
    "so the competitor page can show the latest link without a signed request to Cloudflare on a public "
    "page. Nothing is recorded unless the upload succeeded, and a failure reports what went wrong rather "
    "than asking you to try again.", styles["Body"]))
story.append(figure("04_series_detail_top.png", "The series toolbar: Add race to series, Download CSV, "
                                                 "Preview HTML and Download HTML."))
story.append(figure("13_publish_html.png", "The published results document: branding banner, jump-to-race "
                                            "toolbar, and colour-coded top-three places."))
story += note_box(
    "The published file is a snapshot, provisional as of when it was published. After any correction, "
    "press <b>Publish to website</b> again — the app does not keep the club website in sync on its "
    "own. The link itself never needs changing; only the publishing does.",
    kind="warn")

story.append(Paragraph("CSV exports are Sailwave import files", styles["H2"]))
story.append(Paragraph(
    "The club scores and keeps its published history in <b>Sailwave</b>, so the CSV downloads on the race and "
    "series pages exist to be imported rather than read. Each is one header row of field names Sailwave "
    "recognises, then <b>one row per competitor per race</b>, races told apart by <b>RaceNo</b> — the shape its "
    "importer reads, needing no column mapping.", styles["Body"]))
story.append(bullets([
    "<b>One file per rating system.</b> A Sailwave series is scored under one system and a competitor carries one "
    "<b>Rating</b>, but this app produces IRC and YTC from the same finish times — so each page offers both, "
    "formatted the way each system is written (IRC TCC to three decimals, YTC whole). Mixing them in one file "
    "would silently rescore a fleet.",
    "<b>Finishing places are deliberately not exported.</b> Sailwave scores from elapsed time and rating; sending "
    "an order as well would import this app's arithmetic and then ask Sailwave to redo it, with nothing to say "
    "which wins if they disagreed. <b>Elapsed</b> is sent alongside <b>Start</b> and <b>Finish</b>, because a race "
    "here can have per-class start times.",
    "Races are numbered by their <b>position in the series</b>, so importing week by week lands each race in its "
    "own Sailwave race rather than overwriting race 1 every time. The series export is every race in one file; "
    "the standings are not exported, because working out totals and discards is the reason for importing.",
]))
story.append(Paragraph(
    "The readable version of the same results is still the HTML publish above. The full column reference and "
    "import steps are in <b>docs/WEBSITE_PUBLISHING.md</b>.", styles["Body"]))

story.append(PageBreak())

# ================================================================================================
# PART IV — HARDWARE INTEGRATION
# ================================================================================================
story += part_heading("Part IV — Hardware integration", "Chapter 13",
                       "Horn, manual input sensing and central audio")
story.append(Paragraph(
    "Configured in <b>Settings → Horn settings &amp; Test</b>, split into three subsections: horn output, "
    "manual horn input sensing, and start automation/central audio.", styles["Body"]))
story.append(figure("s_hardware.png", "Settings → Horn settings & Test: all three subsections.", max_h=16 * cm))

story.append(Paragraph("Horn output", styles["H2"]))
story.append(Paragraph(
    "The app uses <b>pyserial</b> to assert <b>DTR</b> (or, in legacy/non-ProLog setups, RTS) on a serial "
    "adapter for a configured blast duration (50–5000 ms) — this matches the ProLog-style race-office "
    "interface. Fields: <b>Serial port</b> (e.g. COM3 on Windows, /dev/ttyUSB0 on Linux — leave blank for "
    "simulation mode, which logs events without firing hardware), <b>Output line</b> (DTR/RTS), "
    "<b>Default blast duration</b>. <b>Save and test horn output</b> fires a real test blast.", styles["Body"]))
story += note_box(
    "Never drive a horn directly from a serial adapter, and never wire horn voltage or a horn switch "
    "directly to CTS/DSR/DCD/RI. Use a relay, opto-isolated relay board or transistor driver, with proper "
    "isolation and voltage limiting. A robust arrangement keeps the existing horn button/circuit working "
    "independently of the PC, with the PC's horn output driving an isolated relay contact in parallel.",
    kind="warn")

story.append(Paragraph("Manual horn input sensing", styles["H2"]))
story.append(Paragraph(
    "Optional feedback that timestamps the moment the physical horn button is pressed, using the relay's "
    "feedback contact: <b>DTR</b> (pin 4) drives the horn relay; <b>RTS</b> (pin 7) is held asserted as the "
    "feedback-contact common; the relay returns RTS to <b>DCD</b> (pin 1) when idle and to <b>CTS</b> "
    "(pin 8) when the horn/manual button is active. The app treats CTS-asserted as authoritative for "
    "“active”, using DCD only as an internal idle diagnostic. Status is polled via an authenticated JSON "
    "API; if unreachable the UI shows “Input status unavailable” rather than a raw error. Only manual "
    "horn events — not automatic sequence horns — can be assigned as a boat's finish time from the race "
    "log.", styles["Body"]))
story += note_box(
    "Enabling manual horn input sensing forces the horn output line to DTR regardless of the Output-line "
    "selector, since RTS is needed as the sensing common in that wiring.")

story.append(Paragraph("Central audio (VHF announcements)", styles["H2"]))
story.append(Paragraph(
    "Speech is generated on the race-office PC itself (via <b>pyttsx3</b>/SAPI on Windows, with OS "
    "command-line fallbacks elsewhere), not in the browser — so VHF announcements keep running while the "
    "race officer uses another page, a tablet, or the split-screen view. Route the PC's audio output "
    "through a mixer, isolation transformer or approved radio audio interface into the VHF/PA input; keep "
    "levels conservative and test before racing, and keep the official horn circuit independent of the "
    "audio feed. Settings fields: <b>Enable automatic horn signals</b>, <b>Enable VHF/audio announcements</b>, "
    "<b>Normal speech rate</b> (default 185) and <b>Countdown speech rate</b> (default 285, used for the "
    "final ten-second count and “Start.”). <b>Save and test central audio</b> speaks a test phrase.",
    styles["Body"]))
story.append(Paragraph(
    "If the VHF is keyed by <b>VOX</b> (voice-activated transmit), the radio can take a moment to open, clipping "
    "the first word. Enable <b>Play a VOX wake-up tone before announcements</b> and set the <b>VOX tone lead "
    "(seconds)</b> (default 2): a short tone is played that many seconds before each spoken announcement — "
    "standard and pursuit — so the radio is already transmitting when speech begins. The tone is scheduled ahead "
    "of the announcement, so it does not shift the countdown or signal timing.", styles["Body"]))

story.append(Paragraph("How the signals line up", styles["H2"]))
story.append(Paragraph(
    "Per configured start, relative to that start's own start signal:", styles["Body"]))
signal_rows = [
    ["−5:00", "Warning signal (1 sound)", "Class numeral pennant(s) raised"],
    ["−4:00", "Preparatory signal (1 sound)", "Pennant(s) + P raised"],
    ["−1:00", "One-minute signal (1 sound)", "P lowered; pennant(s) remain up"],
    ["0:00", "Start signal (1 sound)", "Pennant(s) lowered"],
]
sig_table = Table([["TIME", "SIGNAL", "FLAGS AFTER"]] + signal_rows,
                   colWidths=[2.6 * cm, 6.2 * cm, CONTENT_W - 8.8 * cm])
sig_table.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ("GRID", (0, 0), (-1, -1), 0.5, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
]))
story.append(sig_table)
story.append(Spacer(1, 8))
story.append(Paragraph(
    "Spoken announcements are layered around these: a course announcement at −9:00/−7:00/−3:00, a "
    "“stand by 15 seconds” call 15 seconds before each horn, spoken “Five minutes / Four minutes / One "
    "minute / Thirty seconds / Twenty seconds”, then a spoken countdown from ten to one, and “Start.” at "
    "zero. If two starts' signals coincide (e.g. Start 2's warning falls on Start 1's start), the scheduler "
    "coalesces them into a single horn sound rather than sounding twice.", styles["Body"]))
story += note_box(
    "There is no built-in protest or redress workflow, and the current flag panel does not yet model every "
    "AP/X/First Substitute/N/S combination. The horn remains the authoritative signal — browser pages only "
    "display the countdown and flags.")

story.append(PageBreak())

# ------------------------------------------------------------ Chapter 14
story += part_heading("Part IV — Hardware integration", "Chapter 14", "Weather station")
story.append(Paragraph(
    "Configured in <b>Settings → Weather Station</b>. Two modes, chosen by radio button:", styles["Body"]))
story.append(bullets([
    "<b>Weather station polling</b> — poll a configured URL/IP and use its latest wind sample.",
    "<b>Manual input</b> — use a TWD/TWS typed in below; no polling is attempted.",
]))
story.append(figure("s_weather.png", "Settings → Weather Station: pick the source, then only that source's "
                                      "fields are shown.", max_h=11 * cm))
story.append(Paragraph("Fields and behaviour", styles["H2"]))
story += (field_table([
    ("Weather station URL or IP", "Default format is http://&lt;ip&gt;/get_livedata_info? (a bare IP has "
     "this path appended automatically). Response is expected as JSON."),
    ("Poll interval seconds", "1–120 seconds; the background poller runs continuously regardless of which "
     "page is open, with a 4-second request timeout."),
    ("Wind direction offset °", "Corrects for a weather station not aligned to true north; applied before "
     "the reading is stored."),
    ("Manual TWD ° / TWS kt", "Used only when Manual input is selected."),
]))
story.append(Paragraph(
    "The app reads three fields from the station's <b>common_list</b> JSON array by id: <b>0x0A</b> (wind "
    "direction), <b>0x0B</b> (wind speed) and <b>0x0C</b> (gust), converting units (kt, mph, m/s, km/h all "
    "recognised; unitless values are assumed m/s). On any failure the status line reads “Could not read "
    "weather station: …” rather than silently going stale.", styles["Body"]))
story += note_box(
    "The configured URL is validated before use: it must resolve to a routable address — <b>localhost, "
    "link-local, multicast and unspecified addresses are rejected</b> by default (normal club-LAN private "
    "addresses are fine). <b>RO_WEATHER_ALLOWED_HOSTS</b> (Chapter 2) can further restrict accepted "
    "hostnames. If validation fails, the app falls back to the built-in default URL rather than erroring.")
story.append(Paragraph(
    "Every successful poll is stored as a wind-history sample, feeding the wind-history chart, course "
    "recommendation, race leg analysis and the public course-analysis view.", styles["Body"]))
story.append(Paragraph(
    "Ambient samples are pruned after 24 hours, but a sample falling within an hour either side of a race is "
    "kept indefinitely. The wind a race was sailed in is part of that race's record — the replay's wind gauge "
    "reads it back years later, and the Chart and Course analysis tabs show the average over the race once "
    "everyone has finished. Trimming it to the last day would have left every race older than yesterday with "
    "a gauge and no wind to put in it.", styles["Body"]))

story.append(PageBreak())

# ------------------------------------------------------------ Chapter 15
story += part_heading("Part IV — Hardware integration", "Chapter 15", "Hut power monitoring (Victron VE.Direct)")
story.append(Paragraph(
    "For an off-grid hut, the app can monitor the 12&nbsp;V power system from three Victron devices over their "
    "<b>VE.Direct</b> serial ports: a <b>SmartShunt</b> battery monitor, a <b>Phoenix</b> IP43 charger, and a "
    "<b>SmartSolar</b> MPPT solar controller. Each device connects to the race-office PC with its own "
    "VE.Direct-to-USB cable, so each appears as a separate COM port. Monitoring is <b>read-only</b> — the app "
    "reads the telemetry the devices broadcast and never sends commands to them.", styles["Body"]))
story.append(Paragraph("Configuration (Settings → Hut power)", styles["H2"]))
story.append(figure("s_power_settings.png", "Settings → Hut power: the three device COM ports, sample "
                                            "interval, history retention and the simulator toggle.", max_h=8 * cm))
story += (field_table([
    ("SmartShunt / Phoenix / SmartSolar port", "The COM port for each device (e.g. COM4). Find them in Windows "
     "Device Manager or the VictronConnect app. Leave a port blank to skip that device."),
    ("Sample interval (seconds)", "How often a combined reading is stored, 5–3600 s (default 30)."),
    ("History retention (days)", "Samples older than this are purged, 1–3650 (default 365). History lives in a "
     "separate database, so this does not affect the race data or its backups."),
    ("Simulate readings", "Generates plausible battery/solar values so the dashboard card and history graph can "
     "be demoed before the cables are wired. Any real configured port overrides the simulator for that device."),
]))
story += note_box(
    "History is stored in <b>data/power_history.db</b>, separate from the race database. It is <b>not</b> "
    "included in the Backup / restore sections and is excluded from release ZIPs, so continuous telemetry "
    "never bloats the race data.")
story.append(Paragraph("Dashboard card", styles["H2"]))
story.append(Paragraph(
    "The <b>Hut power</b> card on the Dashboard shows the battery state of charge, voltage and current, solar "
    "input, charger state, and the estimated hut consumption. It refreshes every few seconds.", styles["Body"]))
story.append(figure("s_power_card.png", "The Dashboard Hut power card (shown here with the simulator on).",
                    max_h=7 * cm))
story.append(Paragraph("Hut consumption (derived load)", styles["H2"]))
story.append(Paragraph(
    "The SmartShunt measures only the battery's net current, so the hut's actual DC load is worked out from all "
    "three devices: <b>load = solar current + charger current − battery current</b>, times the bus voltage. A "
    "source that is off or not connected counts as zero, and the figure is clamped at zero against measurement "
    "noise. The consumption reading is therefore only fully accurate when all three devices are reporting.",
    styles["Body"]))
story.append(Paragraph("History graph", styles["H2"]))
story.append(Paragraph(
    "The <b>Hut power</b> item in the sidebar (grouped with Documentation and Backup) opens a history page with a "
    "selectable time range. It plots battery state of charge and voltage, solar input with hut consumption "
    "overlaid for comparison, and battery charge/discharge current.", styles["Body"]))
story.append(figure("s_power_history.png", "The Hut power history page: battery, power (solar in vs hut load) "
                                           "and battery current over the selected range.", max_h=13 * cm))

story.append(PageBreak())

# ------------------------------------------------------------ Chapter 16
story += part_heading("Part IV — Hardware integration", "Chapter 16", "RTSP camera and video recording")
story.append(Paragraph(
    "Configured in <b>Settings → Video Recording</b>. This chapter covers the <b>RTSP IP camera</b> path "
    "(select <b>Video source → RTSP stream</b>) — the app also supports a USB webcam, not covered here.",
    styles["Body"]))
story.append(figure(os.path.join(REF_DIR, "s_video_1.png"), "Camera input and recording mode, and the "
                                                             "rolling live-preview buffer.", max_h=10.5 * cm))
story.append(Paragraph("RTSP setup", styles["H2"]))
story += (field_table([
    ("RTSP recording/main stream URL", "e.g. rtsp://user:pass@192.168.1.157:554/Streaming/Channels/101 "
     "(channel 101 = main stream on a Hikvision-style camera). Credentials are embedded directly in the URL."),
    ("Recording mode", "Camera stream copy (recommended) copies the camera's existing H.264/H.265 stream "
     "without re-encoding — much smaller CPU load and file overhead; the alternative re-encodes with an "
     "app-drawn timestamp overlay."),
    ("Stream-copy buffer format", "Fragmented MP4 (recommended) or MPEG-TS as an advanced fallback."),
    ("RTSP timestamp mode", "Camera timestamps (recommended for Hikvision RTSP) vs wall-clock arrival time "
     "(fallback only — can show pause/jump artefacts if the camera's own timestamps are unusable)."),
    ("FFmpeg path", "Defaults to relying on ffmpeg being on PATH; set an explicit path if it isn't."),
    ("RTSP preview/sub-stream URL", "Optional second, lower-resolution stream (e.g. channel 102) so the "
     "live preview doesn't compete with the main recording stream for bandwidth/CPU."),
]))
story.append(Paragraph(
    "FFmpeg keeps a rolling buffer in <b>runtime/video/buffer/</b>; clips are cut from it for start signals, "
    "the Finish button, and manual horn events, using <b>Pre-event</b>/<b>Post-event seconds</b>, and are "
    "saved to <b>data/video_clips/</b> (back this up — Chapter 19). <b>Buffer minutes</b> and "
    "<b>Segment seconds</b> control the rolling buffer's size and granularity.", styles["Body"]))
story += note_box(
    "For Hikvision-style stream-copy: use standard H.264/H.265 (not Smart Codec/H.264+/H.265+), constant "
    "bitrate, and an I-frame interval around one second. Low-bitrate or smart-codec streams can look like "
    "periodic freezes, since the app faithfully copies whatever sparse frames the camera actually sends.")

story.append(figure(os.path.join(REF_DIR, "s_video_2.png"), "Public video and live-image publishing "
                                                             "(Cloudflare R2) — see Chapter 17.", max_h=10.5 * cm))

story.append(Paragraph("Camera PTZ presets", styles["H2"]))
story.append(Paragraph(
    "For Hikvision-style cameras with ISAPI PTZ presets, the app can automatically switch between an "
    "<b>idle/zoomed-out</b> preset and a <b>race start/finish</b> preset — switching to the race preset a "
    "configurable number of seconds before the first actual start, and back to idle once no entry is "
    "racing. Requests are authenticated PUT calls to "
    "<b>/ISAPI/PTZCtrl/channels/&lt;channel&gt;/presets/&lt;preset&gt;/goto</b>.", styles["Body"]))
story.append(figure(os.path.join(REF_DIR, "s_video_3.png"), "Camera zoom/PTZ presets, recorder status, and the "
                                                             "public live-stream link.", max_h=14.5 * cm))
story += note_box(
    "A successful manual preset test (<b>Save and test idle preset</b> / <b>…race preset</b>) holds that "
    "preset for 30 seconds before the automatic scheduler can switch it back — giving you time to actually "
    "see whether the camera moved. If a camera login fails, automatic switching pauses itself to avoid "
    "repeatedly locking the camera's admin account.")

story.append(PageBreak())

# ================================================================================================
# PART V — REMOTE ACCESS
# ================================================================================================
story += part_heading("Part V — Remote access", "Chapter 17", "Cloudflare Tunnel, public video and live stream")
story.append(Paragraph(
    "Cloudflare Tunnel lets competitors reach the public pages (and the race officer reach /admin) from "
    "outside the club network, without opening inbound ports on the race-hut router.", styles["Body"]))

story.append(Paragraph("Setting up the tunnel", styles["H2"]))
story.append(numbered([
    "Run the app on the race-office PC (ideally as the Windows Scheduled Task from Chapter 2) and confirm "
    "<b>http://localhost:5050/admin</b> works locally first.",
    "In the Cloudflare dashboard, configure a <b>public hostname</b> that forwards to "
    "<b>http://localhost:5050</b> with the path left <b>blank</b> — forward the whole app, not just "
    "/public.",
    "Give competitors the bare hostname, e.g. <b>https://pro.yourclub.org</b>.",
    "Race officers use the same hostname's <b>/admin</b> route and log in as normal.",
]))
story += note_box(
    "Do not tunnel only <b>/public</b>. The public pages also load CSS, JavaScript and images from "
    "<b>/static/...</b> — tunnelling only /public makes the page render as unstyled plain text. This "
    "warning appears in three places in the app's own documentation and code comments, because it's the "
    "single most common setup mistake.", kind="warn")

story.append(Paragraph("Security model", styles["H2"]))
story.append(Paragraph(
    "The Tunnel exposes the <b>whole</b> app, including <b>/admin</b> — security relies entirely on Flask "
    "login, not on restricting which routes the tunnel forwards. Set a strong, explicit "
    "<b>RO_SECRET_KEY</b> (Chapter 2) for any internet-facing deployment. Public routes are a deliberately "
    "narrow allow-list (static assets, the public competitor pages, public video/live-frame routes, and "
    "read-only course/weather APIs) — horn control, finish-time entry, settings, boat management, rating "
    "import and user administration are never reachable without logging in.", styles["Body"]))
story += note_box(
    "There is no rotatable “public share link”/token feature in the current version — the public URLs "
    "(<b>/public/current</b>, <b>/public/race/&lt;id&gt;</b>) are "
    "plain, keyless and always readable by anyone who reaches them. Don't put anything sensitive in a "
    "public-facing race name or note field.")
story.append(Paragraph(
    "One responsive page serves phones, tablets and PCs. On a phone the wide tables become one card per boat. "
    "The <b>competitor home page</b> carries the analog wind dial (the same instrument and script as the "
    "dashboard, from <b>templates/partials/wind_gauge.html</b>) and a countdown to the current race in its "
    "header, then three tabs — <b>Races</b> (the whole year grouped into rolled-up "
    "series, current series open and current race highlighted), <b>Wind</b> and <b>Live camera</b> (which starts "
    "on opening the tab, with no checkbox). A <b>race page</b> is three tabs — <b>Entries</b> (or <b>Start "
    "times</b> for a pursuit), <b>Chart</b> and <b>Course analysis</b> — gaining a fourth, <b>Leader "
    "board</b>, once every boat has stopped racing; the leader board then opens first and the Chart/Course "
    "analysis tabs switch to the <b>average wind over the race</b> (warning signal to last finish, TWD "
    "vector-averaged) as a record of the conditions. The older <b>/public/mobile/...</b> addresses still work "
    "and redirect to the responsive page, so links already shared with competitors keep working.", styles["Body"]))

story.append(Paragraph("Cloudflare R2 (public video and live image)", styles["H2"]))
story.append(Paragraph(
    "A separate, optional feature — configured alongside video recording (Chapter 16), not part of the "
    "Tunnel setup — that offloads competitor video/image traffic from the hut's own connection onto "
    "Cloudflare R2 storage instead.", styles["Body"]))
story += (field_table([
    ("Public video publishing", "Off (serve the local evidence clip) or Cloudflare R2 public copy. The "
     "full-quality clip always stays local in data/video_clips/; R2 only ever gets a smaller, branded H.264 "
     "copy."),
    ("Public clip quality", "720p small or 1080p normal — for the R2 public copy only."),
    ("Draw the start line on public start/finish videos", "Experimental, off by default. Detects the orange "
     "ODM buoy and draws the start line (pole base → buoy) on the public copy only — never the evidence "
     "clip. Start videos: red before the start, green after; finish videos: green. Best-effort (skipped if the "
     "buoy isn't found); tune in data/startline_config.json. Needs Pillow + numpy."),
    ("Public live image", "Serve from hut app, or upload the latest branded JPEG to R2 every few seconds "
     "(Live image upload seconds)."),
    ("R2 account ID / S3 endpoint, bucket, access key ID, secret access key", "Standard S3-compatible "
     "credentials for the R2 bucket."),
    ("Public base URL", "The public/custom domain competitor browsers actually load from."),
    ("Object prefix", "Folder-style prefix inside the bucket, e.g. race-videos."),
]))
story.append(Paragraph(
    "<b>Save and test R2 upload</b> writes a small test file and checks it's publicly reachable; if the "
    "upload succeeds but the public check fails, check the <b>Public base URL</b> rather than the access "
    "keys. <b>Save and retry failed public video uploads</b> re-attempts any clips that didn't make it to "
    "R2 the first time.", styles["Body"]))

story.append(Paragraph("Public live camera stream", styles["H2"]))
story.append(Paragraph(
    "Separately from the still-image live preview and finish clips, the club can publish a continuous, "
    "branded, low-bandwidth <b>live view</b> of the hut camera to competitors and spectators. The live "
    "stream is produced by an external relay (below); the app's only part is a single setting that surfaces "
    "the public <b>Watch live</b> link.", styles["Body"]))
story += (field_table([
    ("Public live stream URL", "Settings → Video Recording → Public live stream. The public address of the "
     "live view, e.g. https://pro.pwllhelisailingclub.org/live (only http/https accepted). When set, a "
     "<b>Watch live</b> link appears on the public competitor pages — a button in the home page's live-camera "
     "panel and a link in the race-page footer. Leave blank to hide it everywhere."),
]))
story += note_box(
    "Setting the URL does not itself start any streaming — it only shows the link. The stream must actually "
    "be served by the relay for the link to work. The full-quality finish video and the still-image live "
    "preview are unaffected and independent of this.")
story.append(Paragraph(
    "From v0.182 the URL does more than show a link: the competitor home page's <b>Live camera</b> tab and the "
    "race sheet's <b>live finish camera</b> both show their refreshing snapshots while the relay's on-demand "
    "stream starts, then embed that stream once it reports it is playing. The relay's watch page posts its state "
    "to the embedding page (<b>{source: 'psc-live', state: …}</b>); an older relay build that posts nothing "
    "still works, the panels just switch on a timer instead. Snapshot refreshing stops when the video takes over, "
    "and closing the tab or panel removes the player so the relay can drop the stream.", styles["Body"]))
story += note_box(
    "The relay stream lags the water by a few seconds and uses the hut connection in both directions. Time "
    "finishes from the horn and the recorded clips, and keep the race sheet's camera panel closed on a metered "
    "connection when it is not being used.", kind="warn")

story.append(Paragraph("The camera on the club's own website", styles["H2"]))
story.append(Paragraph(
    "The club's main site can carry the same view. <b>deploy/embed/club_website_camera.html</b> is a small "
    "self-contained page to upload to that site and drop into an iframe; its README covers the two lines of "
    "HTML and the one Caddy rule the relay needs. It plays the relay's live stream, so the first visitor "
    "starts it and Cloudflare fans the same copy out to everybody after them — the hut's uplink serves one "
    "stream however many people are watching — with a still from the hut filling the gap while it starts.",
    styles["Body"]))
story += note_box(
    "The page checks where it is embedded. On a site the app does not recognise it takes a single still and "
    "stops, so a copy left on some other page cannot sit there pulling pictures off the hut all day.")

story.append(Paragraph("The live-stream relay (deploy/live_stream)", styles["H2"]))
story.append(Paragraph(
    "The stream itself runs on a separate always-on machine — <b>not</b> the hut PC — so the off-grid hut "
    "sends only one outbound stream, and only while someone is watching. The complete setup, configuration "
    "and troubleshooting guide ships with the release in <b>deploy/live_stream/README.md</b>; the essentials "
    "are:", styles["Body"]))
story.append(bullets([
    "<b>On-demand and shared:</b> the relay pulls a single stream from the hut camera only while at least "
    "one viewer is watching, and drops it about 20 seconds after the last viewer leaves. Viewer count does "
    "not change the load on the hut.",
    "<b>Cloudflare fan-out:</b> Cloudflare caches the video segments, so the relay and the home uplink stay "
    "flat no matter how many people watch.",
    "<b>Branding is automatic:</b> the relay burns the club logo (top-left) and rotating sponsor logos "
    "(top-right) into the video, reading them live from the app's public branding manifest "
    "<b>/api/branding/live</b> — change the logos in Settings → Public branding and the stream follows, with "
    "nothing to edit on the relay.",
    "<b>It is also the club's front door:</b> the same relay proxies the club's public hostname to the hut "
    "race app and shows a holding page when the hut is offline. The trade-off is that if the relay itself is "
    "down, the public hostname is unreachable.",
]))
story += note_box(
    "The relay (MediaMTX + Caddy + cloudflared, typically on a Proxmox LXC) is infrastructure a club IT "
    "volunteer sets up once. Day-to-day race operation needs none of it — only the <b>Public live stream "
    "URL</b> setting above. Follow deploy/live_stream/README.md for the build, and keep any tokens/secrets "
    "out of version control (the bundled relay env files are placeholders only).")

story.append(PageBreak())

# ================================================================================================
# PART VI — ADMINISTRATION AND REFERENCE
# ================================================================================================
story += part_heading("Part VI — Administration and reference", "Chapter 18",
                       "Rating sources, polars and branding")
story.append(Paragraph("Rating lookup sources", styles["H2"]))
story.append(Paragraph(
    "Two URLs feed both the Boats add/lookup search and the bulk import pages (Chapter 4): an IRC listing "
    "CSV URL and a YTC listing Google Sheet/CSV URL (share links are converted to CSV-export links "
    "automatically; include the correct <b>gid=</b> if the sheet has multiple tabs). Both lists are cached "
    "for an hour to avoid slow repeated downloads. Only http:// and https:// URLs are accepted.",
    styles["Body"]))
story.append(figure("s_rating.png", "Settings → Rating lookup sources.", max_h=4.5 * cm))

story.append(Paragraph("Polars and sail charts", styles["H2"]))
story.append(Paragraph(
    "A <b>polar</b> is a boat-speed-vs-wind file used by course recommendation, race leg analysis, the "
    "manual course builder and public course analysis. Upload a polar (.txt/.pol/.csv) with an optional "
    "matching <b>sail chart</b> (a TWA/TWS-to-sail-choice table) in one step; the sail-chart filename is "
    "enforced from the polar's name, e.g. polar <b>J109.txt</b> expects sail chart "
    "<b>J109-SailChart.txt</b>. Without a matching chart, analysis falls back to the bundled default sail "
    "chart.", styles["Body"]))
story.append(figure("s_polars_top.png", "Settings → Polars: upload form and the installed-polars table "
                                        "(the club's full library runs to dozens of boat classes).",
                     max_h=9 * cm))

story.append(Paragraph("Public branding", styles["H2"]))
story.append(Paragraph(
    "Club and sponsor logos shown on competitor-facing media: the public live-camera JPEG and the smaller "
    "public R2 video copy burn the logos in directly (club logo fixed top-left, one of up to <b>eight</b> "
    "sponsor logos top-right, rotating every 5 seconds, capped at about 15% of media height so the finish "
    "line stays visible); the full-quality local evidence clip is never branded. The standalone published "
    "results page instead shows the full club-plus-all-sponsors strip in its banner (Chapter 12), since "
    "it's a static page with nothing to rotate.", styles["Body"]))
story.append(figure("s_branding.png", "Settings → Public branding: club logo and sponsor logos.",
                     max_h=11 * cm))

story.append(PageBreak())

# ------------------------------------------------------------ Chapter 18
story += part_heading("Part VI — Administration and reference", "Chapter 19", "Backup and restore")
story.append(Paragraph(
    "The Backup / restore page (bottom of the side menu) creates or restores a ZIP covering selectable "
    "parts of the <b>data/</b> folder.", styles["Body"]))
story.append(figure("backup_restore.png", "Backup / restore: the selectable sections, and what each "
                                          "contains."))
story.append(Paragraph(
    "The app keeps race data in <b>three</b> SQLite databases, and all three have their own section: "
    "<b>Database</b> (races, entries, boats, settings, users), <b>GPS tracks</b> "
    "(<b>data/track_positions.db</b> — every recorded tracker fix, and so the evidence behind GPS finishes) "
    "and <b>Hut power history</b>. Those, plus <b>Marks/Courses/start-finish</b>, "
    "<b>Polars &amp; Sail charts</b> and <b>Branding Images</b>, are selected by default; <b>Videos</b> is not, "
    "because clips can make backups very large.", styles["Body"]))
story += note_box(
    "Before v0.192 only the race database was backed up, so a PC rebuilt from a backup came back with every "
    "boat's recorded track gone. If you hold older backup ZIPs, they do not contain the tracks.", kind="warn")
story.append(Paragraph(
    "Every database is copied through SQLite's <b>online backup API</b> rather than as a file, so a backup taken "
    "while the tracker poller and power monitor are writing cannot catch a half-written transaction — nothing "
    "has to be stopped first. A database that does not exist yet (a hut that has never used tracking) is "
    "reported as nothing to back up rather than an error. Every backup includes a manifest recording app "
    "version, creation time and file counts. Restoring validates every ZIP entry against path-traversal (must "
    "live under <b>data/</b>, no <b>..</b> segments, no absolute/drive paths) before writing anything to disk, "
    "and puts each database back where its module looks for it, reopening it at the current schema.",
    styles["Body"]))
story += note_box(
    "Restoring replaces the selected sections outright with the ZIP's content. Make sure no one is editing "
    "races or settings while a restore runs, and keep the pre-restore backup until you've confirmed races, "
    "boats, users and settings all loaded correctly.", kind="warn")
story += note_box(
    "Restoring is <b>admin only</b>, because it overwrites current data — race officers see the Restore panel "
    "replaced by an “administrator access required” note. Creating and downloading a backup is available to "
    "race officers too (see the user roles in Chapter 3).")

story.append(Paragraph("Off-site backup", styles["H2"]))
story.append(Paragraph(
    "A backup made on the page above is a copy in the same building as the original. From <b>v0.238</b> the app "
    "can also push an <b>encrypted copy off-site</b>, every night, to Cloudflare R2 — so a fire, a theft or a "
    "dead disk in the hut does not take the club's records with it. Settings → <b>Off-site backup</b>.",
    styles["Body"]))
story.append(Paragraph(
    "It is the <i>same ZIP</i>, with the same <b>data/...</b> member names, so an off-site copy restores on the "
    "Backup / restore page like any other backup. There is no second format to keep working.", styles["Body"]))
story.append(figure("s_offsite.png", "Settings → Off-site backup: the bucket, the passphrase, the schedule "
                                     "and the sections that go off-site.", max_h=13 * cm))
story.append(Paragraph("Setting it up", styles["H3"]))
story += (field_table([
    ("Backup bucket", "A <b>second, private</b> R2 bucket, created in the Cloudflare dashboard with no public "
                      "access. It must not be the bucket that serves public race videos."),
    ("Key prefix", "Folder the archives are written under. Default <b>race-officer-backups</b>."),
    ("Copies to keep", "Older archives beyond this count are deleted after each success, newest kept. Default 30."),
    ("Backup passphrase", "Encrypts the archive. Record it in the club password manager, beside the admin login."),
    ("Hour / minute", "When it runs. Default 03:15."),
    ("Sections", "What goes in. Everything except the saved videos, by default."),
]))
story.append(Paragraph(
    "The account ID and access keys are the ones already entered for public video publishing (Chapter 16) — "
    "there is only one set of credentials to look after. The <b>bucket</b>, however, must be a different one, "
    "and the app <b>refuses to run</b> if it is not.", styles["Body"]))
story += note_box(
    "The public video bucket is served publicly. A backup placed in it would be one guessed object key away "
    "from being anybody's download — and a backup contains every user account and password hash in the club. "
    "That is why a shared bucket is refused rather than merely warned about.", kind="warn")
story.append(Paragraph(
    "After any change, press <b>Save and back up off-site now</b> and read the result. It builds, encrypts, "
    "uploads and verifies one backup immediately and reports what actually happened, including a wrong bucket "
    "name or a key the bucket will not accept. An unattended 3 am job that has never once succeeded is not a "
    "backup.", styles["Body"]))
story += note_box(
    "If an R2 <b>API token is scoped to specific buckets</b>, a token created for the video bucket cannot write "
    "to the backup bucket. Either widen the token's scope to both buckets, or issue a token that covers the "
    "backup bucket, in the Cloudflare dashboard under R2 → Manage API tokens.", kind="warn")
story.append(Paragraph("Encryption, and getting the data back", styles["H3"]))
story.append(Paragraph(
    "The archive is <b>AES-256 encrypted before it leaves the hut</b>, in the WinZip AES format. That choice is "
    "deliberate: <b>7-Zip or WinRAR will extract the data/ folder given the passphrase</b>, with this app not "
    "installed and no script to run. A format only this app understood would have made the off-site copy depend "
    "on the very thing it exists to survive.", styles["Body"]))
story.append(Paragraph(
    "To restore one: download the <b>.zip</b> from R2, then use the Backup / restore page as normal and enter "
    "the passphrase in the <b>Backup passphrase</b> field. A wrong passphrase is refused <i>before</i> anything "
    "is deleted — the restore clears the target folders before extracting, so that check is what stops a typo "
    "costing you the branding folder. If the archive is larger than the upload limit "
    "(<b>RO_MAX_UPLOAD_MB</b>, 64 MB by default), raise it for that restore, or extract the ZIP with 7-Zip and "
    "copy the <b>data/</b> folder in by hand with the app stopped.", styles["Body"]))
story += note_box(
    "Nothing can open an off-site backup without the passphrase — not the app, not Cloudflare, not the "
    "developer. Changing it does not re-encrypt archives already uploaded, so keep the old passphrase until "
    "those have aged out of retention.", kind="warn")
story.append(Paragraph("When it runs, and when it stands aside", styles["H3"]))
story.append(Paragraph(
    "Nightly at the configured time, and <b>held back</b> while boats are still racing, while a start is within "
    "the next three hours, and while race videos are still uploading. The hut is on 4G shared with a caravan "
    "park, and start videos have already been lost to Saturday-evening contention — a backup must never be the "
    "reason one is lost. A held-back run tries again about twenty minutes later; a failed one waits about "
    "forty-five. A backup missed because the PC was switched off at 03:15 — the normal state of a hut PC — is "
    "taken when the app next starts, rather than skipped for the day.", styles["Body"]))
story.append(Paragraph("Checking it is still happening", styles["H3"]))
story.append(Paragraph(
    "The <b>dashboard</b> carries the age of the last success, and says so plainly when the answer is “never” "
    "or “40 days”. This is the point of the card: a backup job that quietly stopped months ago is worse than no "
    "backup job at all, because the club believes it has one. Settings also lists <b>what is actually in the "
    "bucket</b>, rather than what the app believes it put there.", styles["Body"]))
story.append(Paragraph(
    "Each upload is <b>verified with a HEAD request</b> against the expected byte count, because an upload that "
    "reports success without the bytes arriving is the failure nobody notices until a restore. An unencrypted "
    "sidecar manifest (<b>....manifest.json</b>) is stored beside each archive holding the sections, file "
    "counts, size and SHA-256 — so the club can see what is off-site, and check a downloaded archive, without "
    "decrypting anything first. It contains counts only, never race data.", styles["Body"]))
story += note_box(
    "<b>Videos are deliberately left out.</b> The public web copies are already on R2, so backing them up would "
    "pay twice for the same bytes. The consequence is worth stating: those R2 copies are branded, re-encoded "
    "web versions, so the <b>unbranded evidence clips on the hut PC are not off-site</b>. If a particular clip "
    "matters as evidence, copy it off by hand.")
story.append(Paragraph(
    "Encrypted backups need the <b>pyzipper</b> package from <b>requirements.txt</b>. Without it the app "
    "refuses to run an off-site backup rather than uploading a readable archive.", styles["Body"]))

story.append(Paragraph("Reading the bundled documentation in the app", styles["H2"]))
story.append(Paragraph(
    "The <b>Documentation</b> page lists the PDF guides and, beside them, every one of the bundled "
    "<b>docs/*.md</b> files — installation, tracking, video, the relay, troubleshooting and the rest — which open "
    "rendered in the app with their cross-links working. Before this they could only be read as files on the "
    "race-office PC, which is no use when <b>TROUBLESHOOTING</b> is the one you want <i>while</i> something is "
    "going wrong.", styles["Body"]))

story.append(Paragraph("Web server sizing", styles["H2"]))
story.append(Paragraph(
    "<b>Settings → Web server</b> sets how much the app takes on at once: <b>worker threads</b> (4–64, "
    "default 8), the <b>connection limit</b> (50–512, default 100) and the <b>idle timeout</b> "
    "(20–600 s, default 120). Waitress reads them once at startup, so they take effect on a <b>restart</b> "
    "— the card shows what the running process actually started with and says plainly when a restart is "
    "owed, because “I changed it and nothing happened” gets changed twice more during a race.",
    styles["Body"]))
story += note_box(
    "Raise the <b>connection limit</b> first when phones on the water get “cannot connect” while the "
    "race office is fine. It counts open sockets, not people, and a browser holds up to six per origin — so "
    "the old default of 100 is about sixteen viewers, reached whether or not anything is slow. That is what "
    "filled it during a race: every request fast, and no sockets left. Threads are cheap here because they are "
    "mostly waiting on disk and network rather than working.")

story.append(Paragraph(
    "The same section carries the <b>public address</b>, which is not about sizing: it is the address "
    "competitors type in, and it is what the app uses to build any link it hands out — the competitor "
    "share links, the QR code, and the logo addresses the live-stream relay reads. Reached from outside, "
    "the app is behind the relay and sees only the tunnel’s own internal hostname, so without this "
    "those links name a machine nobody can reach. Leave it empty on the hut network, where the address a "
    "request arrived on is already the right one. Scheme and host only, and unlike the three above it "
    "takes effect immediately rather than on a restart.",
    styles["Body"]))

story.append(Paragraph("Slow-request log", styles["H2"]))
story.append(Paragraph(
    "Any request over <b>RO_SLOW_REQUEST_MS</b> (default 250 ms) is written to "
    "<b>runtime/logs/slow.log</b> with its method, path, status and duration — one file per day, kept a "
    "fortnight, and set the threshold to 0 to switch it off. It exists because when the server started refusing "
    "connections there were four plausible causes in the code and nothing recorded how long a request took. "
    "Guessing there means fixing three things that were fine and leaving the one that was not; if it happens "
    "again, this file names the endpoint.", styles["Body"]))

story.append(Paragraph("Activity log", styles["H2"]))
story.append(Paragraph(
    "The app keeps a plain-text history of <b>everything that changes state</b>: logins, failed logins, logouts "
    "and password changes; races created, updated and deleted; boats added to and removed from races; finishes "
    "and pursuit positions recorded; an entry edited by hand, with the old and new finish time and status; "
    "course changes, shortenings and mark edits; rating imports; series edits; backups downloaded and restored; "
    "and user-account changes. It is written to <b>runtime/logs/activity.log</b> on the race-office PC, one file "
    "per day (yesterday rolls over to <b>activity.log.YYYY-MM-DD</b>). It lives under <b>runtime/</b>, so it is "
    "not part of backups or release packages.", styles["Body"]))
story.append(Paragraph(
    "A <b>settings save lists the keys that changed, and what they changed from</b> — <b>horn_active: 1 -&gt; 0</b> "
    "is the kind of line that explains a horn fault a fortnight later. Only the differences are listed, and a save "
    "that altered nothing is still recorded so “who was in Settings” stays answerable. Passwords, secrets, "
    "tokens and passphrases are recorded as <i>changed</i> or <i>set</i> <b>without their values</b>: this is a "
    "plain-text file, and it must not become somewhere to read a credential out of.", styles["Body"]))
story.append(Paragraph(
    "It can be read in the browser: <b>Settings → Recent hardware events</b> carries a link to it, newest entry "
    "first with a button per day. Administrators only, since it names who did what, and <b>read-only</b> — there is "
    "no route that can edit or clear it, which is what makes an audit trail worth keeping.", styles["Body"]))
story.append(figure("activity_log.png", "The activity log: two mark corrections, and a settings save with "
                                        "what each key changed from.", max_h=9 * cm))
story += note_box(
    "One line is worth knowing on sight: <b>finish direction skipped an earlier crossing</b>. It means a boat "
    "crossed the line with every mark rounded and that crossing was refused by the finishing-direction test, so "
    "the finish recorded is a later one. Either the boat did cross the wrong way first — the finish video "
    "settles it — or the line geometry needs looking at.", kind="note")

story.append(PageBreak())

# ------------------------------------------------------------ Chapter 19
story += part_heading("Part VI — Administration and reference", "Chapter 20", "Environment variables reference")
story += (field_table([
    ("RO_HOST", "Waitress bind address. Default 0.0.0.0."),
    ("RO_PORT", "Waitress port. Default 5050."),
    ("RO_THREADS, RO_CONNECTION_LIMIT, RO_CHANNEL_TIMEOUT", "Waitress worker threads (8), connections allowed "
     "at once (100) and idle-connection timeout in seconds (120). Defaults for the Settings → Web server values, "
     "which override them once saved; raise the connection limit first when phones cannot connect."),
    ("RO_DB_TIMEOUT_S", "How long a database write waits for another writer before giving up (default 30 s). "
     "Waiting beats failing here: a finish that is not recorded is the one thing this app cannot afford to lose."),
    ("RO_SECRET_KEY", "Flask session signing key; auto-generated and persisted if unset. Set explicitly for "
     "internet-facing/Cloudflare deployments."),
    ("RO_INITIAL_ADMIN_PASSWORD", "First-run admin password (only effective before the DB/first admin user "
     "exists)."),
    ("RO_ALLOW_DEFAULT_ADMIN", "Set to 1 to keep an existing insecure admin/admin credential instead of "
     "having it auto-replaced."),
    ("RO_WEATHER_ALLOWED_HOSTS", "Comma-separated hostname allow-list for the weather-station URL, in "
     "addition to the built-in localhost/link-local/multicast rejection."),
    ("RO_SERIAL_PORT, RO_HORN_LINE, RO_HORN_ACTIVE, RO_HORN_DURATION_MS", "Optional startup defaults for the "
     "horn output configuration normally set from Settings → Horn settings & Test."),
    ("RO_HORN_INPUT_ENABLED, RO_HORN_INPUT_LINE, RO_HORN_INPUT_ACTIVE, RO_HORN_INPUT_POLL_MS", "Optional "
     "startup defaults for manual horn input sensing, normally set from the same Settings section."),
    ("RO_COOKIE_SECURE", "Session cookies are marked Secure by default, so they only work over HTTPS. Set to 0 "
     "for local http testing — otherwise a login over http://localhost:5050 silently fails to stay signed in."),
    ("RO_MAX_UPLOAD_MB", "Maximum request/upload size in MB (default 64). Raise it only to restore a very large "
     "backup ZIP containing video clips."),
    ("RO_TRACK_ENABLED, RO_TRACCAR_BASE_URL, RO_TRACCAR_TOKEN", "First-run defaults for the GPS-tracking "
     "connection normally set from Settings → GPS tracking."),
    ("RO_TRACK_INGEST_SECRET", "Shared secret letting Traccar's forwarder POST fixes to /api/track/ingest. Must "
     "match forward.header on the relay; without it the endpoint stays closed and positions arrive only on the poll."),
    ("RO_TRACK_SIM, RO_TRACK_POLL_SECONDS, RO_TRACK_RETENTION_DAYS, RO_TRACK_ROUNDING_RADIUS_M, "
     "RO_TRACK_GATE_REACH_M, RO_GPS_FINISH_HORN", "Startup defaults for the simulator, poll interval (5), "
     "retention (90 days), mark-rounding radius (50 m), rounding gate reach (750 m) and the auto-finish "
     "horn (off)."),
    ("RO_VEDIRECT_SMARTSHUNT_PORT, RO_VEDIRECT_SMARTSOLAR_PORT, RO_VEDIRECT_PHOENIX_PORT", "Serial ports for the "
     "three Victron hut-power devices, normally set from Settings → Hut power."),
    ("RO_POWER_SAMPLE_SECONDS, RO_POWER_RETENTION_DAYS, RO_POWER_SIM", "Hut-power sample interval, history "
     "retention, and a simulator for previewing the dashboard card without hardware."),
], col_widths=[6.6 * cm, CONTENT_W - 6.6 * cm]))
story.append(Paragraph(
    "Values set from the Settings UI are persisted to the database and take precedence after first save; "
    "the environment variables mainly matter for the very first run or for scripted/headless deployments.",
    styles["Body"]))

story.append(PageBreak())

# ------------------------------------------------------------ Chapter 20
story += part_heading("Part VI — Administration and reference", "Chapter 21", "Status codes and glossary")
status_rows = [
    ["PRESTART", "Entered and waiting: a display label shown until the race's warning-signal time "
     "(stored as RACING throughout, so timing and results are unaffected)"],
    ["RACING", "Started and still racing"],
    ["FINISHED", "Crossed the finish line — has a finish time"],
    ["DNC", "Did Not Compete — never entered/came to the start"],
    ["DNS", "Did Not Start"],
    ["OCS", "On Course Side — over the line early at the start"],
    ["RET", "Retired from the race"],
    ["DNF", "Did Not Finish"],
    ["DSQ", "Disqualified"],
    ["DNE", "Disqualification that is not excludable as a discard (RRS 90.3(b))"],
    ["DGM", "Discretionary penalty for gross misconduct"],
]
status_intro = [Paragraph("Entry status codes", styles["H2"])]
status_table = Table([["CODE", "MEANING"]] + status_rows, colWidths=[3.2 * cm, CONTENT_W - 3.2 * cm], repeatRows=1)
status_table.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9),
    ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"), ("TEXTCOLOR", (0, 1), (0, -1), NAVY_DARK),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ("GRID", (0, 0), (-1, -1), 0.5, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
]))
story.append(KeepTogether(status_intro + [status_table]))

story.append(Paragraph("Glossary", styles["H2"]))
glossary = [
    ("First warning signal vs. first start", "The warning-signal time is set on a race; the actual start is "
     "always five minutes later. Every other start in a multi-start plan is an offset from that first "
     "start, not from the warning signal."),
    ("IRC / YTC", "Two independent handicap rating systems scored side by side. A boat only appears in a "
     "system's table if it has a rating for it."),
    ("Discard profile / constitution", "See Chapter 6."),
    ("Rating-band class", "A named group of boats sharing a rating system and range, with its own numeral "
     "class flag."),
    ("Compound mark", "A named course mark that expands to real physical marks for chart/analysis purposes "
     "— see Chapter 5."),
    ("Stream copy vs re-encode", "Recording modes for the RTSP camera — see Chapter 16."),
]
for term, definition in glossary:
    story.append(Paragraph(term, styles["GlossaryTerm"]))
    story.append(Paragraph(definition, styles["Body"]))

story.append(PageBreak())

# ------------------------------------------------------------ Chapter 21
story += part_heading("Part VI — Administration and reference", "Chapter 22", "Good practice checklist")
story.append(bullets([
    "Keep the race-office PC's clock accurate (NTP/GPS-disciplined) and visible; every signal and finish "
    "time depends on it.",
    "Don't let the PC sleep during racing — the start scheduler and horn/audio need to keep running.",
    "Before race day: check horn wiring and isolation, test manual horn input sensing if fitted, test "
    "central audio to the VHF/audio path, and confirm horn/audio/weather/video status in Settings.",
    "During the start sequence: supervise automatic horn/audio, confirm displayed flags match the physical "
    "flags hoisted, and use manual controls for recall/postpone/abandon as needed.",
    "Prefer the blue <b>Finish</b> button so horn, log, finish time and video clip stay linked; if using a "
    "manual horn event for a finish, assign it from the horn/race log afterwards.",
    "Handle protests and redress outside the app, then apply the outcome by editing the affected entry "
    "directly.",
    "Back up <b>data/</b> before using “Remove Race” to delete a race sheet — it cannot be undone.",
    "Re-publish (Download HTML / Download CSV) after any post-race correction so the club website and your "
    "records stay in step.",
    "For a public/Cloudflare deployment, set an explicit <b>RO_SECRET_KEY</b> and treat every public URL as "
    "world-readable — there is no access-key gate on them.",
]))
story.append(Paragraph(
    "For narrative, step-by-step walkthroughs rather than reference lookup, see the "
    "<b>Race Officer's Guide</b> (running a series day-to-day) and the <b>Competitor's Guide</b> "
    "(what competitors see on the public pages).", styles["Body"]))

story.append(PageBreak())

# ================================================================================================
# PART VII — PURSUIT RACES
# ================================================================================================
story += part_heading("Part VII — Pursuit races", "Chapter 23", "Running a pursuit race")
story.append(Paragraph(
    "A <b>pursuit race</b> is a different kind of race, chosen with the <b>Race type</b> option when you "
    "create a new race. It runs for a fixed period; the slower boats start first and the faster boats later, "
    "so on handicap they would all finish together. You pick <b>IRC</b> or <b>YTC</b> as the single rating "
    "system that sets the start times. There are no classes, and there is one finish signal rather than "
    "individual finish times.", styles["Body"]))
story.append(figure("pursuit_new_race.png", "New race: choosing the Pursuit type, its rating system and the "
                                            "fixed period.", max_h=9 * cm))
story.append(Paragraph("Setting up", styles["H2"]))
story.append(numbered([
    "On <b>New race</b>, set <b>Race type → Pursuit</b>, choose the rating system (IRC or YTC) and the fixed "
    "period in minutes, and optionally add all active boats.",
    "On the race sheet's <b>Course &amp; start</b> tab, set the first warning-signal time (the first, slowest "
    "boat starts five minutes later), confirm the period and rating system, and save.",
    "Choose a course the same way as a standard race — a fixed numbered course, <b>Recommend / change "
    "course</b>, or <b>Build manual course</b>.",
    "Start times are computed automatically and recomputed whenever you add or remove a boat. Until the first "
    "warning time and period are set, boats show “set start time” rather than a time — that is different from a "
    "genuinely missing rating, which is flagged separately.",
]))
story += note_box(
    "The slowest-rated boat starts first; each faster boat is delayed by "
    "<b>period × (1 − slowest speed ÷ boat speed)</b>, derived from the chosen rating (IRC TCC, or 1000 ÷ YTC). "
    "A boat with no rating in the chosen system is still entered, but gets no start time and is flagged.")
story.append(Paragraph("Running the start", styles["H2"]))
story.append(Paragraph(
    "The <b>Start console</b> tab shows the start ladder: every boat and its start time, with the next boat "
    "to start highlighted and a live countdown. Horns fire automatically — the five-minute warning sequence "
    "before the first start, one start signal at each boat's start time, then the single finish signal at the "
    "end of the period. <b>Fire horn now</b> is available for a manual signal. There is a start video for each "
    "start time and no finish video. The first start flies the normal flag sequence, defaulting to class 1 "
    "(the numeral-1 pennant).", styles["Body"]))
story.append(figure("pursuit_start_console.png", "The pursuit start console: the start ladder with the next "
                                                 "boat to start highlighted.", max_h=12 * cm))
story.append(Paragraph("Finishing and results", styles["H2"]))
story.append(Paragraph(
    "At the finish signal, judge the boats by their order on the water and record it on the "
    "<b>Finish &amp; positions</b> tab — a position number and status for each boat. There is no time "
    "correction: the finishing order is the result, shown on the <b>Results</b> tab. A pursuit race that "
    "belongs to a series scores into the standings by finishing position, exactly like a normal race's places. "
    "Competitors see each boat's start time (next to start highlighted) on the public race page, and the "
    "finishing order once you have recorded it.", styles["Body"]))

story.append(PageBreak())

# ------------------------------------------------------------ Chapter 24
story += part_heading("Part VIII — GPS tracking", "Chapter 24",
                      "GPS yacht tracking and automated finishes")
story.append(Paragraph(
    "Boats carrying low-cost <b>Queclink GL521MG</b> GPS trackers can be shown live on the race course chart, "
    "with a live position-on-the-water list, and the app can detect finish-line crossings to help record "
    "finishes. Positions are shown to the race officer on the race sheet and, since v0.177, on the "
    "<b>public competitor race page</b> (course chart plus tracking columns in the entries list). The trackers report to a "
    "<b>Traccar</b> server on the live-stream relay, and no port is opened on the hut PC. Relay/Traccar setup is in "
    "<b>deploy/live_stream/README.md</b> §7.", styles["Body"]))
story += note_box(
    "GPS tracking is cellular, so coverage thins offshore, and the GL521MG is a battery asset tracker built for "
    "very long standby — its default reporting is far too coarse for finish order. Set a <b>frequent reporting "
    "interval on race days</b>, expect to <b>charge the trackers between race days</b> because a fast rate costs "
    "a large multiple of the standby drain, and treat GPS finishes as proposals to confirm — the finish video "
    "remains the arbiter.")

story.append(Paragraph("Push or poll", styles["H2"]))
story.append(Paragraph(
    "Positions reach the app two ways, and both are wanted. <b>Push</b> (v0.189) is the fast path: Traccar's "
    "position forwarder POSTs each fix to the app the moment it decodes it, and finish detection runs on "
    "receipt — measured at 0.02–0.32 s, against up to a full poll interval of waiting. This is what keeps the "
    "automatic horn and the competitors' view prompt. <b>Polling</b> stays on as the safety net: it covers a "
    "relay that is not forwarding and a stalled forwarder, and it <b>back-fills gaps</b>, asking Traccar for the "
    "range missed while the app was down rather than leaving a permanent hole in the track. With push working, "
    "a poll interval of about 30 seconds is plenty.", styles["Body"]))
story += note_box(
    "A recorded finish <b>time</b> does not depend on how quickly the app hears about a fix — crossings are "
    "interpolated between fix timestamps, so those were always right. What push removes is the delay in the "
    "things you can see: the automatic horn, and the positions competitors are shown.")

story.append(Paragraph("Connection (Settings → GPS tracking)", styles["H2"]))
story.append(figure("s_gps_tracking.png", "Settings → GPS tracking: the Traccar connection, poll interval, "
                                          "retention, the mark-rounding radius, the rounding gate reach and the simulator toggle.", max_h=8 * cm))
story += (field_table([
    ("Enable GPS tracking", "Master on/off for the position poller."),
    ("Traccar base URL / API token", "The Traccar server and an API token. The token needs device-management "
     "rights so the app can add and remove devices for you."),
    ("Poll interval (seconds)", "How often positions are pulled from Traccar (default 5). With push working "
     "this can go to about 30 — polling is then only the safety net and the back-fill."),
    ("Push ingest token", "Shared secret that lets Traccar POST each fix straight to the app. Set the same value "
     "here and in the relay's forward.header. Leave blank to poll only."),
    ("Position history retention (days)", "How long stored positions are kept (default 90); a separate database "
     "from the race data."),
    ("Mark-rounding radius (metres)", "Close enough to a mark that it counts as rounded whatever else the "
     "geometry says (default 50) — one of the three tests below, not the only one. Inside this distance no "
     "side is asked about, on either hand: a boat nearer the mark than we know where the mark is has no "
     "determinable side."),
    ("Rounding gate reach (metres)", "How far past a mark, on the hand a boat is supposed to pass it, still "
     "counts as rounding it (default 750). Wide on purpose: it is what catches a boat that gives a mark a "
     "berth. On the wrong hand only the rounding radius counts, and that asymmetry is the only thing in the "
     "app that knows a mark has a required side."),
    ("Simulate boats", "Runs a built-in simulator so the map, positions list and finishes can be previewed without "
     "any hardware."),
    ("Show SIM status on the Trackers page", "Off by default. Turns on the SIM column and the top-up warnings "
     "described under <i>What the SIM is doing</i> below. Needs a Hologram API key."),
    ("Hologram API key", "From a Hologram user&#8217;s Settings &#8594; My account &#8594; API card. Hologram keys "
     "cannot be scoped, so limit the key by limiting its owner: make a separate user with the <b>Editor</b> role "
     "rather than using an owner or admin login. Editor reads SIM state, usage, plan pricing and the balance, and "
     "cannot activate SIMs or change billing."),
    ("Organisation ID", "Only needed when the key&#8217;s owner belongs to more than one Hologram organisation."),
]))
story += note_box(
    "The status box below the fields reports whether push is actually arriving, because a token that does not "
    "match the relay's fails <b>silently</b> — the app carries on polling, every tracker reports, and the only "
    "symptom is a late horn. It reads <b>Push: working — N fixes received, last just now</b>, "
    "<b>configured, but no fix has arrived this way yet</b> (go and check forward.header), or "
    "<b>not configured</b>. Counts are since the app last started.")

story.append(Paragraph("The Trackers page", styles["H2"]))
story.append(Paragraph(
    "Tracker management lives on the <b>Trackers</b> page (bottom group of the side menu, with Documentation and "
    "Settings). Unlike Settings it is available to <b>race officers</b>, since assigning trackers is a race-day "
    "task. Each "
    "tracker shows how long ago it last reported with a RAG dot — <b>green</b> within 5 minutes, <b>amber</b> "
    "within the hour, <b>red</b> after — and the whole page updates live without a refresh.", styles["Body"]))
story.append(figure("trackers_page.png", "The Trackers page: each tracker's boat assignment and a live "
                                         "last-reported indicator.", max_h=9 * cm))
story.append(Paragraph(
    "There are two ways to add one. <b>Trackers seen on the network</b> (v0.190) lists the devices Traccar has "
    "heard from that are not set up here yet, most recently heard from first — switch a tracker on, pick its "
    "boat from the dropdown and press <b>Add</b>, with no 15-digit IMEI to read off the device. The page notices "
    "new arrivals between refreshes and says so. Adopting claims the device in Traccar for the app's token, and "
    "removing a tracker deletes it there too. This is the only way in: a form for typing an IMEI by hand was "
    "removed in v0.270, because a tracker that has never reached Traccar cannot be tracked whatever the app "
    "records, and a 15-digit number read off a label is a number waiting to be mistyped.", styles["Body"]))
story += note_box(
    "For a tracker to appear by itself, the relay needs <b>database.registerUnknown</b> enabled (see "
    "traccar-forward.xml). A device Traccar registers by itself belongs to no Traccar user, which is why "
    "Traccar's own web UI hides it until you switch on <i>All Devices</i> — and why its positions are not "
    "returned to the app's API token at all until adopting it <b>claims</b> it. Without that the poller would "
    "never see that boat even after it was assigned to one.")
story += note_box(
    "A boat's track always stays with the <b>boat</b>: if you move a tracker to a different boat, the earlier "
    "fixes remain with the first boat. The Trackers page is the <b>only</b> place a pairing is set — the race "
    "sheet used to carry a second, per-race dropdown for a loaner tracker and it was removed in v0.207, because "
    "two places to set the same thing is two places to get it wrong on a race morning. To lend a tracker for one "
    "race, re-assign it here and move it back afterwards; nothing recorded is lost either way.")

story.append(Paragraph("What kind of tracker it is (v0.269)", styles["H3"]))
story.append(Paragraph(
    "A <b>Type</b> column names each tracker's model — Queclink GL521MG, Teltonika ATC700, and so on. It is "
    "worked out from the first eight digits of the IMEI, the <i>type allocation code</i> that every unit of a "
    "model shares, checked against the protocol Traccar decoded. Both are needed: the code belongs to whoever "
    "certified the radio, and a tracker built around a bought-in cellular module often carries the module "
    "maker's codes, while the protocol alone is far too coarse — an asset tracker that flattens in a day and a "
    "mains-powered router are both simply <b>teltonika</b>. A model the app does not recognise shows as "
    "<i>unknown</i> with a <b>name it</b> button, which adds it to the <b>Tracker types</b> catalogue on the "
    "same page. Naming a model is open to race officers; it records a fact about the kit rather than touching "
    "the hardware.", styles["Body"]))

story.append(Paragraph("Sending a command to a tracker (v0.269)", styles["H3"]))
story.append(Paragraph(
    "<b>Commands</b> beside each tracker opens a console that sends a command straight to the device through "
    "Traccar and shows what comes back, with a list of prebuilt commands chosen to suit that tracker's "
    "protocol. This one is <b>administrators only</b> — the rest of the page decides which boat carries which "
    "tracker, while this reconfigures hardware at sea. A tracker that is awake answers in seconds; one that "
    "sleeps between reports answers on its next connection, up to five minutes on the ATC700.", styles["Body"]))
story.append(Paragraph(
    "<b>What you can read back depends on the tracker.</b> Teltonika units send their answer as text and it "
    "appears in the console, so a reporting interval can be read and changed there and then. Queclink units "
    "acknowledge the command and send their answer as a separate message that Traccar does not keep, so for "
    "those the acknowledgement is all the app can show — those commands are labelled <i>answer not visible "
    "here</i> rather than left looking broken. The exception is a position request, whose answer is a fix and "
    "arrives on the map about a second later.", styles["Body"]))
story += note_box(
    "<b>Factory resets are refused outright.</b> In Queclink's command set a factory reset and a reboot differ "
    "by a single digit, and a tracker that forgets its server address stops being findable at all — it has to "
    "come off the boat and onto a USB cable to be set up again. A reboot is allowed but asks first, and says "
    "what it costs: about <b>5 minutes 45 seconds with no fixes</b>, measured, then a few minutes at reduced "
    "accuracy. Never do that to a boat approaching the finish, where being blind is worse than being slightly "
    "out of position. Every command is written to the activity log, including the refused ones.")

story.append(Paragraph("Asking racing boats where they are (v0.270)", styles["H3"]))
story.append(Paragraph(
    "A Queclink reports once a minute and holds each fix until the top of the minute, so its position reaches the "
    "app about <b>45 seconds old</b> — 185 m behind the boat at 6 knots, which is the gap a finish is interpolated "
    "across. Asked directly it answers in about a second. So while a race is on the app asks: from the <b>warning "
    "signal</b> until <b>a minute after each boat stops racing</b>, every tracked boat carrying a GL521MG is sent a "
    "position request every 20 seconds. The extra minute is not padding — a finish is interpolated between the "
    "fixes either side of the line, so the one after the crossing is what pins it down.", styles["Body"]))
story.append(Paragraph(
    "Twenty seconds is a floor the tracker sets rather than a choice: asked faster it returns the same cached fix "
    "over again. The result is fixes about 62 m apart arriving 4 seconds old, against 185 m arriving 45 seconds "
    "old. It costs roughly <b>+1.1%/h</b>, about double a Queclink's idle draw, so even a nine-hour race polled "
    "throughout is under a fifth of its battery. Only the GL521MG is asked, matched on its IMEI type code rather "
    "than its protocol: the request carries that model's own password, so another Queclink would simply reject "
    "it. Teltonika trackers are left out for a different reason — an ATC700 already reports every 10 seconds "
    "while moving and answers in a second, and at about 10%/h it is the one unit that cannot spare the asking.", styles["Body"]))
story += note_box(
    "On by default, and switched off in <b>Settings &rarr; GPS tracking</b>. It does nothing unless Traccar is "
    "configured, and a race left open by mistake stops being polled after 30 hours, so a forgotten one cannot "
    "quietly flatten a tracker.")

story.append(Paragraph("Live map and position on the water", styles["H2"]))
story.append(Paragraph(
    "On the race sheet's <b>Course &amp; start</b> tab, tracked boats appear on the course chart as a hull seen "
    "from above, turned the way the boat is heading, with its sail number beside it, and in the "
    "<b>Position on the water</b> list, ordered by how far round the course "
    "each boat is — "
    "marks rounded (e.g. 6/10), the next mark, and the distance still to sail, plus speed and last-fix age. It "
    "refreshes every few seconds; a boat whose last fix is over a minute old is dimmed.", styles["Body"]))
story.append(Paragraph(
    "<b>The boats are compared as at the same instant.</b> Trackers do not report together - a fleet can be "
    "running at 2, 10 and 61 seconds at once - so putting each boat's last known position against the others "
    "would compare them at different moments. At 6 knots, two minutes of that is 400 m, which is enough to "
    "put an order the wrong way round, and it showed as the list swapping boats about while nothing on the "
    "water was changing. Each boat is carried forward from its last fix on its last known course and speed, "
    "to the moment being shown. One unheard from for three minutes is left where it last actually was, and "
    "marked stale.", styles["Body"]))
story.append(Paragraph(
    "<b>Every boat entered is listed, and one with no position says why.</b> Its row is dashed across and the "
    "last column reads <b>No tracker</b> when there is no tracker on the boat, or <b>Not reporting</b> when "
    "there is one and nothing has come from it in the last hour — the same hour that turns a tracker red on "
    "the Trackers page. Those boats sort after the tracked fleet. The chart and the list will show a boat that "
    "reported shortly before the warning signal, so it does not vanish in the minutes around the gun, but not "
    "one whose last fix is older than that hour: a boat is not at where it was three weeks ago, and the "
    "distance-to-go worked out from such a fix was the length of a passage race.", styles["Body"]))
story.append(Paragraph(
    "<b>Until you set the course there is no progress to report.</b> A new race stores the first fixed course "
    "as a fallback so the chart and the leg analysis have something to draw, and <b>Marks</b>, <b>Next</b> and "
    "<b>To go</b> were being measured against it on a race sheet whose own header said <i>Course: not set "
    "yet</i> — as was GPS finish detection. Those three columns stay blank until you choose a course. Where "
    "each boat is, how fast it is going and when it last reported are the tracker's own report and are "
    "unaffected.", styles["Body"]))
story.append(figure("race_track_map.png", "The live fleet on the course chart — a heading arrow and sail number "
                                          "for each tracked boat.", max_h=9 * cm))
story.append(figure("race_track_leaderboard.png", "Position on the water: every entered boat, ordered by "
                                                  "progress round the course.", max_h=6 * cm))

story.append(Paragraph("How a rounding is decided", styles["H2"]))
story.append(Paragraph(
    "Three tests decide whether a boat has rounded a mark, and <b>any one of them is enough</b>:", styles["Body"]))
story.append(bullets([
    "<b>The rounding radius.</b> A fix inside it, or the path between two fixes passing inside it. Default "
    "50 m, and any mark can carry its own. Inside this distance no side is asked about, on either hand: a "
    "boat nearer the mark than we know the mark's own position has no determinable side.",
    "<b>The gate.</b> A line through the mark at right angles to the leg arriving at it, reaching the "
    "<b>rounding gate reach</b> (default 750 m) on the hand the course requires and only the rounding radius "
    "on the hand it does not. Crossing it is rounding. That asymmetry is the only thing in the app that "
    "knows a mark has a required side.",
    "<b>Closest approach and departure.</b> The boat came within the mark's neighbourhood and has since "
    "opened up again, with the next mark nearer than it was. The catch-all, and it earns its place on "
    "<i>tight</i> roundings rather than wide ones: at a coarse reporting rate a close rounding can happen "
    "entirely between two fixes, leaving no fix inside the radius and no crossing of the gate to find.",
]))
story.append(Paragraph(
    "The walk through the course is <b>sequential</b>, which is why a missed rounding is expensive: the boat "
    "shows several marks behind where it actually is for the rest of the race, and because the finish line is "
    "only watched once every earlier mark has been rounded, <b>its GPS finish never arrives either</b>.",
    styles["Body"]))
story += note_box(
    "A suspected wrong-side rounding is <b>reported, never refused</b>. Refusing it would stall that boat for "
    "the rest of the race and take its automatic finish with it, to enforce a rule this app does not "
    "adjudicate - the race officer does, on a protest, with the video and the track. So the gate can only "
    "ever add a rounding the other tests missed; it cannot take one away.", kind="note")

story.append(Paragraph("Correcting the mark a boat is sailing to", styles["H2"]))
story.append(Paragraph(
    "A wide rounding usually counts on its own, so the arrows are for what the geometry still cannot see - a "
    "mark that has dragged a long way, a tracker silent through the rounding, or a boat sent round something "
    "the course does not describe.", styles["Body"]))
story.append(Paragraph(
    "The <b>Sailing to</b> column at the end of each <i>Position on the water</i> row carries a left and a right "
    "arrow. Forward treats the mark the boat is heading for as rounded; back puts it on the previous one. The "
    "row then shows <b>set</b>, so a boat corrected by hand is not mistaken for one the GPS worked out.",
    styles["Body"]))
story.append(bullets([
    "A correction applies <b>from the moment you press it</b>, never retrospectively. The club's finish-line "
    "mark O is also a mid-course rounding mark on most courses, so a boat nudged on to the line could "
    "otherwise be finished by a crossing it made on an earlier lap, at the wrong time.",
    "It reaches the finish detection, not just the display — which is most of the point.",
    "The arrows stop at the ends: back from the first mark and forward past the line are both refused, because "
    "finishing a boat is the finish button's job. A boat that has finished shows no arrows.",
    "Every press is recorded in the activity log with the marks either side of it, and appears in the race log.",
]))

story.append(Paragraph("Tracker batteries", styles["H2"]))
story.append(Paragraph(
    "These are battery <i>asset</i> trackers run at a reporting rate far above the standby they were designed "
    "for, so they need charging between race days. The <b>Trackers</b> page shows each tracker's battery beside "
    "<i>Last reported</i>, with the same red/amber/green dots: <b>green above 25%</b>, <b>amber at or below "
    "25%</b> (charge it before Saturday), <b>red at or below 10%</b> (may not last a race). A dash means that "
    "tracker does not report a level, which is not the same as a flat one — it is deliberately not shown as 0%.",
    styles["Body"]))
story.append(Paragraph(
    "The <b>Dashboard</b> warns when a tracker <i>assigned to a boat</i> is at or below 25%, flattest first and "
    "naming the boat. It appears only when there is something to say, and only when GPS tracking is on. A spare "
    "in the drawer being flat is not warned about.", styles["Body"]))

story.append(Paragraph("What the SIM is doing", styles["H2"]))
story.append(Paragraph(
    "A tracker that goes quiet tells you nothing about why. A flat battery, a boat behind a headland and a SIM "
    "the network has stopped all look identical from the app &mdash; but only the last will still be silent "
    "tomorrow, and it is the only one no other part of the app can see. With a Hologram key configured, the "
    "Trackers page carries a <b>SIM</b> column: a dot and one word, <b>OK</b>, <b>Check</b> or <b>Error</b>, and "
    "a link to the detail.", styles["Body"]))
story.append(bullets([
    "Read a healthy answer carefully. It says the <b>SIM</b> is fine &mdash; that nothing at the network end is "
    "stopping the tracker &mdash; and <b>not</b> that the tracker is alive. Only the bad answers mean anything.",
    "The popout names the state the way Hologram&#8217;s own dashboard does: <b>Connected</b> when a data "
    "session is open at that moment, <b>Ready</b> when the SIM is live but idle. Both are healthy. The "
    "club&#8217;s Queclinks hold a long-lived connection and usually read <i>Connected</i>; the ATC700s sleep "
    "between reports and flip between the two.",
    "<b>Pausing</b> means somebody is switching the SIM off and the change has not landed yet. It is reported "
    "as paused, because it is about to stop carrying data &mdash; better a minute early than a page that says "
    "the SIM is fine while it is being switched off.",
    "<b>Error</b> is a SIM that cannot carry data. <i>Paused by system</i> means it has hit a usage limit or a "
    "low balance and will not recover on its own; it needs resuming in the Hologram dashboard.",
    "<b>Check</b> is a state this app has not been taught. Not a claimed fault, and not a clean bill either.",
    "The detail popout also shows which carrier the tracker is actually on. That is not trivia: the club&#8217;s "
    "SIM is multi-network, and on a Teltonika half of every setting is live or dead depending on whether the "
    "device counts as roaming.",
]))
story += note_box(
    "Hologram is asked at most every two minutes, and the line above the table says how old the answer you are "
    "reading is. If you have just changed something in the Hologram dashboard, press <b>check again</b> rather "
    "than waiting.")

story.append(Paragraph("Data, cost and topping up", styles["H2"]))
story.append(Paragraph(
    "<b>costs and balance</b>, beside <i>check again</i>, totals the fleet: each tracker&#8217;s data and cost, "
    "the account balance, and the date it runs out. The account is prepay, and when it empties <b>every tracker "
    "stops at once</b>, with nothing on the water to see it coming.", styles["Body"]))
story.append(bullets([
    "The top-up date is not the balance divided by a monthly cost. Hologram bills each SIM on its own 30-day "
    "cycle from the day it was activated, so the fees arrive in <b>clusters</b> that an average would hide. The "
    "app walks forward a day at a time instead, taking the recent daily data spend off each day and each "
    "SIM&#8217;s fee on its own renewal date.",
    "The dashboard says so too, once a month or less is left and more loudly inside a fortnight.",
    "Every money figure is worked out here, not read from an invoice: Hologram returns <b>no cost</b> on any "
    "usage endpoint, only a per-MB rate and a recurring fee. Figures are US dollars.",
    "There is no single time period on that page. Each SIM&#8217;s <i>this period</i> is a different window, so "
    "the table shows how far into its own cycle each one is and carries one figure &mdash; the last 28 days "
    "&mdash; on a window they all share.",
]))

story.append(Paragraph("Automated finishes", styles["H2"]))
story.append(Paragraph(
    "<b>Arm GPS auto-finish</b> and <b>Auto-confirm</b> are both ticked on a new race, on the <b>Entries &amp; "
    "finish times</b> tab, so a finish is caught whenever it can be; untick either there if you would rather not. "
    "The app follows each boat through the course marks in order and only records a finish once every "
    "mark has been rounded and the boat then crosses the line — so a course that passes the ODM mid-course (e.g. "
    "O-F-O-1-O), or a shortened course, finishes correctly on the final crossing, not an early one.", styles["Body"]))
story.append(Paragraph(
    "Both boxes sit on a single <b>GPS finish detection</b> strip above the list of entries, with "
    "<b>Apply</b> and the current state — <i>Armed. No finishes detected yet.</i>, or <i>Off for "
    "this race.</i> With <b>Auto-confirm</b> unticked, each detection waits on that strip instead, as "
    "a row offering <b>Confirm</b> or <b>Dismiss</b> beside the time it caught and the finish video.",
    styles["Body"]))
story.append(bullets([
    "Each detected finish is <b>proposed</b> — boat and interpolated time, with a link to the finish video to "
    "check it against. Confirm or dismiss it; a confirmed finish is tagged <b>gps-confirmed</b>.",
    "With <b>Auto-confirm</b> left on, each detected finish is recorded straight away (tagged <b>gps-auto</b>) "
    "and remains reviewable and editable afterwards. Untick it and detections wait as proposals instead.",
    "The horn only sounds on an auto-confirmed GPS finish if that option is enabled in Settings (off by default, "
    "because GPS latency makes an automatic horn late).",
]))

story.append(Paragraph("When the line’s mark has moved", styles["H2"]))
story.append(Paragraph(
    "A finish line has two ends and they are not the same kind of thing. The <b>shore</b> end is a surveyed "
    "transit — a bridge window — and does not move. The <b>seaward</b> end is a laid buoy on a mooring: "
    "it swings, it drags, and it gets re-laid. A boat that passes <i>inside</i> the physical buoy but "
    "<i>outside</i> the position the app holds for it falls off the end of the line segment, and its finish is "
    "never seen. The finishing-direction test does not save you — that measures against the infinite line "
    "and is perfectly content; only the segment bound refuses the crossing. An ISORA night race lost the third "
    "boat’s automatic finish exactly that way.", styles["Body"]))
story.append(Paragraph(
    "So each line projects its seaward end outward, by <b>seaward_extension_m</b> in "
    "<b>data/start_finish.json</b>: 150 m on the club line (347 m, tested as 497 m) and 250 m on the ISORA line "
    "(1510 m, tested as 1760 m). Only the seaward end — extending the shore end would project the line "
    "inland over the harbour wall and cover nothing that can move.", styles["Body"]))
story += note_box(
    "Safe because the extension is <b>collinear</b>: the infinite line is unchanged, so the finishing-direction "
    "test cannot be affected by it, and a finish still requires every mark rounded first. The extended line is "
    "used <b>only</b> for the crossing test — the chart still draws the line where it really is, and "
    "distance-to-go still measures to the real mark. It is a tolerance, not an open end: a boat well beyond it "
    "still does not finish. Set it to the widest the buoy plausibly swings.")
story.append(Paragraph(
    "A tolerance can hide a wrong position rather than fix one, so this is worth knowing: the stored position for "
    "<b>F</b>, the Pwllheli Fairway Buoy, was <b>112 m inshore</b> of the position ISORA’s own sailing "
    "instruction gives — which had been sitting in <b>start_finish.json</b> all along as "
    "<b>si_seaward_position</b>, unread. On a 1510 m line that is 7% of its length, before the buoy had moved at "
    "all. It is corrected, with the old position kept in the mark’s history so races already scored still "
    "replay against the line they were sailed to. <b>A drifting line mark is worth re-measuring, not just "
    "tolerating</b>: the extension is for the drift between measurements, not a substitute for making one.",
    styles["Body"]))

# ================================================================================================
# PART IX — RUNNING RACING FROM THE WATER
# ================================================================================================
story.append(PageBreak())
story += part_heading("Part IX — The Virtual Race Officer", "Chapter 25",
                      "Running racing from the water")
story.append(Paragraph(
    "Everything in this manual so far is done on the race sheet, in the hut. It can also be done from a "
    "boat. The <b>Virtual Race Officer</b> at <b>/vro</b> is one page — a box to type in, a "
    "thread of conversation and a <b>Yes</b> "
    "button — for a race officer with a phone in one hand and a tiller in the other. It creates races, "
    "sets starts and courses, enters boats, shortens courses and answers questions about the racing, and it "
    "does all of it by calling the same code the race sheet calls. There is no second way to run a race.",
    styles["Body"]))
story.append(figure("onwater_page.png",
                    "The on-the-water page: the race and the countdown to the gun above, the conversation "
                    "below, and a read-back waiting for Yes.", max_h=13 * cm))

story.append(Paragraph("Who may use it", styles["H2"]))
story.append(Paragraph(
    "Being an administrator is not enough. Each account has a <b>VRO</b> tick box in "
    "<b>Settings → Users</b>, and without it the page answers 403 and names the permission that is "
    "missing. Both command endpoints check it as well as the page, because a POST is a POST.", styles["Body"]))
story += note_box(
    "The separation is deliberate. The club has settled that a race may start with <b>nobody watching the "
    "line</b> — a boat later judged over by video takes an alternative penalty — but <b>who</b> may "
    "drive the racing from a phone is a different decision, and it is made one account at a time. "
    "<b>Arming the start sequence is not offered on this page at all</b>, because that decision has not been "
    "made.")

story.append(Paragraph("How a command works", styles["H2"]))
story.append(numbered([
    "You type a sentence — <i>“create a race called Sunday Points at 11am, in the summer "
    "series”</i>.",
    "The app says back exactly what it would do, in full: <i>“Create ‘Sunday Points’ as a "
    "standard race, first warning signal 10:55, first gun 11:00, in series Summer Series 2026. Course 29 "
    "(4.0 nm), chosen for the wind now (131°T, 12.0 kn).”</i>",
    "Nothing happens until you press <b>Yes</b>, or type one — <i>yes</i>, <i>ok</i>, "
    "<i>go ahead</i>, <i>do it</i>.",
]))
story.append(Paragraph(
    "The read-back is the whole safety model. It is the only check that catches the class of mistake where "
    "the app understood something perfectly and it was not what was meant: a race at 11 tomorrow rather than "
    "today, the wrong fleet, the right command aimed at yesterday’s race. Questions are answered "
    "straight away, because there is nothing to undo. A sentence that begins with yes but carries a change "
    "— <i>“yes, but make it half past”</i> — is an instruction, and is read as one.",
    styles["Body"]))
story += note_box(
    "A proposal lapses after <b>five minutes</b>, only one is live at a time, and sending the same command "
    "twice does it once — a phone that retries after a dropped reply is the same command, not a second "
    "race. Every command, read-back and result goes to the activity log with the account that gave it.")

story.append(Paragraph("What it will do", styles["H2"]))
story += field_table([
    ("create a race", "A race sheet with its first gun, its type and its series. A course is suggested for "
                      "the wind at that moment and set if you agree."),
    ("set the start / the course", "<i>“use course 4”</i>, <i>“put the start back ten "
                                   "minutes”</i>, <i>“bring it forward five”</i>."),
    ("add entries", "Every active boat, one fleet, or one named boat — <i>“add Mojito”</i>."),
    ("shorten the course", "At a mark the fleet is already sailing to. Two horn blasts and the spoken "
                           "announcement, after you confirm."),
    ("status / results", "How the race is going, or the finishing order of a race already sailed."),
    ("make up a course", "A course built from the club’s marks instead of a numbered one, read "
                         "back mark by mark. Say <i>“change the second mark to 2, "
                         "starboard”</i> and it reads the whole course back again."),
    ("look something up", "The club’s own data: where a mark is, a course’s legs and their "
                          "distances, how many courses there are, the boat database, the series, "
                          "recent races, where the fleet has got to, the polar and its sail chart."),
    ("a boat’s rating", "IRC and YTC, by name or sail number — from the boat database, and from "
                        "the RORC IRC listing and the YTC sheet when the boat is not in it. It "
                        "offers to add a boat the club does not have, and <b>never overwrites an "
                        "existing record</b>."),
])
story += note_box(
    "Three places hold a rating and they are not the same thing. The <b>boat database</b> is what a race "
    "actually scores on, because entering a boat snapshots its rating at that moment; the <b>IRC "
    "listing</b> and the <b>YTC sheet</b> are where that figure came from and either may have moved "
    "since. All three are reported, because somebody asking what a boat is rated is usually asking "
    "because something does not look right. The import screens on the Boats page do update an existing "
    "record from a listing — correctly, with the two side by side in front of you. From a boat they "
    "are not, so there the existing record wins: taking a listing figure over the club’s own record "
    "is how a season gets scored on the wrong number.")
story.append(Paragraph(
    "It will also answer questions in words, from what the app itself knows: the wind now and how it has "
    "shifted over the last hour, which boats are entered, how long a course should take round, which course "
    "would give more reaching, and whether the trackers are reporting.", styles["Body"]))

story.append(Paragraph("What it will not do", styles["H2"]))
story.append(bullets([
    "<b>Sound a horn on command.</b> There is no <i>arm</i> step and no <i>fire now</i>. With "
    "start automation switched on in Settings the app fires the warning, preparatory and start "
    "signals itself, off the race’s stored warning signal — so <b>setting or moving a start time "
    "from the water moves the horn with it</b>, and the read-back says so. With start automation "
    "off, nothing sounds by itself whatever time is set.",
    "<b>Signal a recall.</b> The club starts races with nobody watching the line and reviews the "
    "video afterwards, so there is nothing for it to signal.",
    "<b>Abandon a race, or record a finish.</b>",
]))
story.append(Paragraph(
    "Asked for any of these it says so plainly and offers the nearest thing it can actually do. It never "
    "picks a nearby command to be helpful: an unasked-for command is far worse than an unanswered question.",
    styles["Body"]))

story.append(Paragraph("Which interpreter is answering", styles["H2"]))
story.append(Paragraph(
    "A <b>model</b> reads what you type — one configured in <b>Settings → Virtual Race Officer</b>. "
    "There is nothing behind it: <b>without one the page is unavailable and says so</b>, offering no box "
    "to type in, and both command endpoints refuse in the same words.", styles["Body"]))
story += note_box(
    "The app does have a small built-in grammar — about six sentence shapes — and until v0.264 it "
    "answered when no model was configured or one could not be reached. On a page whose whole premise is "
    "<i>type what you want to do</i>, that reads as an app that understands nothing, and somebody on the "
    "water cannot tell a sentence it will not understand from one it has misunderstood. The club’s "
    "decision is that the feature does not exist until an interpreter is configured and answering. What it "
    "costs: the hut’s outbound internet can fail while the page itself is perfectly reachable, and the "
    "racing then goes back to the race sheet. The page and the Settings card both say which state it is "
    "in, and if the last request failed they give the provider’s own reason — a 402 for an empty "
    "account, a 400 for a model id that does not exist.")

story.append(Paragraph("The conversation", styles["H2"]))
story.append(bullets([
    "It remembers the <b>last half hour</b> with the same person, so <i>“make it a pursuit”</i> or "
    "<i>“add the fleet to that one”</i> has something to refer to.",
    "A question it asks can be answered in a word. Asked <i>“standard race or a pursuit?”</i>, "
    "answering <b>standard</b> re-issues the whole command with the time, the name and the length it already "
    "had.",
    "While a proposal is waiting it is what the conversation is <i>about</i>, so asking for a longer course "
    "changes that proposal rather than something else.",
    "<b>A refresh does not lose it.</b> On a boat that is a dropped signal or a locked phone, not a decision "
    "to start again — the page reloads the conversation, and a proposal still waiting comes back with "
    "its Yes button live.",
]))

story.append(Paragraph("The strip at the top", styles["H2"]))
story.append(Paragraph(
    "Two lines and a rolled-up chart, because every line there is a line of conversation pushed off a phone "
    "screen: the race the conversation is about and what it is doing; the <b>countdown to the first gun</b>, "
    "which turns red inside the last five minutes; the course with its length and about how long it should "
    "take round in this wind, and the wind itself; and the <b>course board</b> \u2014 the same marks and hands "
    "the race sheet and the clubhouse display show, and the thing you read out over the VHF, sized for a "
    "phone. <b>Chart</b> stays closed until you open it and then draws "
    "the marks and legs from the page itself — no map tiles and no requests, over the same 4G the hut is "
    "using. It moves with every command, so a start put back twenty minutes moves the countdown that was the "
    "reason for moving it \u2014 and so does the board, the chart and the expected time when the course "
    "changes.", styles["Body"]))
story += note_box(
    "A command names one or two things, and everything else about the race is left exactly as it was. "
    "That is worth stating because it was not always true: the Course &amp; start <i>form</i> saves every "
    "field on it, which is right for a form, and a command built from only what it named was clearing the "
    "rest \u2014 \u201cuse course 29\u201d wiped the race\u2019s start time, its series (so it scored in no "
    "standings), its class and its finish line, while the read-back said \u201ccourse 29\u201d.")

story.append(Paragraph("Finishes record themselves", styles["H2"]))
story.append(Paragraph(
    "A race created from this page has <b>Arm GPS auto-finish</b> and <b>Auto-confirm "
    "(unmanned)</b> both switched on, and the reply says so \u2014 it is a real difference in how "
    "the race will be run that the sentence did not ask for. The premise of the page is that nobody "
    "is in the hut to press <b>Finish</b>, so the app follows each boat through the course marks and "
    "records its finish as it crosses the line. Both are the ordinary tick boxes on the "
    "<b>Entries &amp; finish times</b> tab and can be turned off there. GPS gives the approximate "
    "time and order; the finish video remains the arbiter, and every recorded finish stays editable "
    "(Chapter 24).", styles["Body"]))

story.append(Paragraph("A course nobody has chosen", styles["H2"]))
story.append(Paragraph(
    "A new race stores the first fixed course as a fallback so the chart, the leg analysis and the "
    "shortening options have geometry to work with. That is not a decision, and the app does not treat it "
    "as one. Until somebody chooses a course the race sheet and the competitor page both read <b>Course not "
    "set</b>, and <b>the spoken VHF course announcement stays silent</b> — a fleet sent round a course "
    "the race officer never picked is a general recall at best.", styles["Body"]))
story.append(Paragraph(
    "From v0.276 that is the whole page and not just the header. The course picker itself reads <b>Course "
    "not set</b> with nothing selected; there is no course board, no leg analysis and no predicted time; "
    "and the chart draws the marks and the start/finish line but no course. The list of races and a "
    "series' own race table say <i>not set</i> in their Course column. Before this the header said one "
    "thing and the board directly beneath it drew the fallback's seven marks, which is a contradiction a "
    "race officer has to resolve on race morning.", styles["Body"]))
story.append(Paragraph(
    "Choosing a course from the picker and saving is what marks it chosen; so are the recommender, the "
    "manual builder, and agreeing to the course the on-the-water page suggests when it creates a race. "
    "Saving <b>Course &amp; start</b> with the picker left on <b>Course not set</b> saves everything else "
    "and leaves the race without a course, so a first warning signal can be set before the course is "
    "known. It only ever goes one way: a course already chosen stays chosen.", styles["Body"]))

doc.multiBuild(story)
print("PDF written to", OUT_PATH)
