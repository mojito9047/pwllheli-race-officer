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

SCREEN_DIR = os.path.join(_HERE, "screenshots")
LOGO_PATH = os.path.join(_REPO, "static", "img", "pwllheli_sailing_club_logo.png")
OUT_PATH = os.path.join(_REPO, "docs", "Pwllheli_Race_Officer_Series_Guide.pdf")

# Read the version from the file the release process bumps, so the cover cannot
# drift from the app the way it did between v0.197 and v0.219.
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
styles.add(ParagraphStyle(name="Body", fontName="Helvetica", fontSize=10, leading=14.5,
                           textColor=colors.HexColor("#1f2937"), spaceAfter=7, alignment=TA_LEFT))
styles.add(ParagraphStyle(name="Caption", fontName="Helvetica-Oblique", fontSize=8.7,
                           leading=11.5, textColor=MUTED, alignment=TA_CENTER, spaceBefore=4, spaceAfter=14))
styles.add(ParagraphStyle(name="BulletItem", fontName="Helvetica", fontSize=10, leading=14.5,
                           textColor=colors.HexColor("#1f2937"), spaceAfter=4))
styles.add(ParagraphStyle(name="GlossaryTerm", fontName="Helvetica-Bold", fontSize=10.5,
                           leading=14, textColor=NAVY_DARK, spaceBefore=10, spaceAfter=2))
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
styles.add(ParagraphStyle(name="TocChapter", fontName="Helvetica-Bold", fontSize=12, leading=24,
                           textColor=NAVY_DARK, spaceBefore=8))


def fit_image(filename, max_w=CONTENT_W, max_h=13.0 * cm):
    path = os.path.join(SCREEN_DIR, filename)
    with PILImage.open(path) as im:
        iw, ih = im.size
    scale = min(max_w / iw, max_h / ih)
    img = Image(path, width=iw * scale, height=ih * scale)
    img.hAlign = "CENTER"
    return img


def figure(filename, caption_text, max_h=13.0 * cm):
    img = fit_image(filename, max_h=max_h)
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


def on_page(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, MARGIN - 10, PAGE_W - MARGIN, MARGIN - 10)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, MARGIN - 22, "Pwllheli Race Officer — Race Officer's Guide")
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
                       title="Pwllheli Race Officer - Race Officer's Guide",
                       author="Pwllheli Sailing Club")

cover_frame = Frame(0, 0, PAGE_W, PAGE_H, id="cover", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
body_frame = Frame(MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN, id="body")
doc.addPageTemplates([
    PageTemplate(id="Cover", frames=[cover_frame], onPage=on_cover),
    PageTemplate(id="Body", frames=[body_frame], onPage=on_page),
])

story = []

# ============================================================== COVER PAGE
cover_logo = Image(LOGO_PATH, width=3.1 * cm, height=3.1 * cm * 477 / 600)
cover_logo.hAlign = "CENTER"
cover_table = Table(
    [
        [Spacer(1, 4.6 * cm)],
        [cover_logo],
        [Spacer(1, 0.6 * cm)],
        [Paragraph("PWLLHELI SAILING CLUB", styles["CoverClub"])],
        [Spacer(1, 0.9 * cm)],
        [Paragraph("Pwllheli Race Officer", styles["CoverTitle"])],
        [Paragraph("Race Officer's Guide", ParagraphStyle("t2", parent=styles["CoverTitle"], fontSize=22, leading=26))],
        [Spacer(1, 0.5 * cm)],
        [Paragraph("Running a series of races and publishing results", styles["CoverSubtitle"])],
        [Spacer(1, 5.7 * cm)],
        [Paragraph(f"Covers app version {APP_VERSION}", styles["CoverMeta"])],
        [Paragraph("Internal race-office reference · human-supervised prototype", styles["CoverMeta"])],
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
story += chapter_heading("Before you begin", "About this guide")
story.append(Paragraph(
    "This guide is for the race officer or race-office volunteer running club racing on the Pwllheli Race "
    "Officer application. It follows one continuous journey: setting up a series, creating and running each "
    "race in that series from the warning signal to the finish, and publishing results so competitors and the "
    "club website can see them.", styles["Body"]))
story.append(Paragraph(
    "It deliberately does not cover first-time installation, or configuring hardware such as the start-hut horn, "
    "finish camera or weather station — those are covered in the Settings &amp; Admin and Hardware documentation "
    "that ships with the app, in the <b>docs/</b> folder. This guide assumes the race-office PC is already set up "
    "and you have a login.", styles["Body"]))
story += note_box(
    "Pwllheli Race Officer is a <b>human-supervised</b> tool. It times starts, records finishes, calculates "
    "corrected results and publishes them — but the race officer and race committee remain responsible for "
    "every official decision: physically hoisting and lowering the correct flags, judging recalls and "
    "abandonments, and handling protests or redress. Always verify what the app shows against what is actually "
    "happening on the water before you rely on it.", kind="warn")
story.append(Paragraph("What this guide covers, in order:", styles["Body"]))
story.append(bullets([
    "Logging in and reading the dashboard",
    "Key concepts: series, races, warning signals, rating systems and scoring, in plain language",
    "Setting up a series: discards, rating-band classes and a default start plan",
    "Creating a race, setting its course and warning-signal time, and adding entries",
    "Running the start sequence, shortening the course if the wind drops, and recording finishes",
    "Viewing race and series results",
    "Publishing results to the club website",
    "A good-practice checklist for race day",
]))

story.append(PageBreak())

# ============================================================== CHAPTER 1
story += chapter_heading("Chapter 1", "Getting started")

story.append(Paragraph("1.1 &nbsp; Logging in", styles["H2"]))
story.append(Paragraph(
    "Open the app in a browser on the race-office PC (normally <b>http://localhost:5050/admin</b>, or the PC's "
    "address from another device on the club network) and sign in with your username and password.", styles["Body"]))
story.append(figure("crop_login.png", "The sign-in page. The public competitor page does not require a login — "
                                       "only the race-office side does.", max_h=8 * cm))
story += note_box(
    "The very first admin account's password is generated automatically on the race-office PC and saved to "
    "<b>runtime/initial_admin_password.txt</b> — it is not shown on this page. Your club may have since "
    "created named accounts for each race officer under Settings → Users.")
story += note_box(
    "Accounts come in two roles. A <b>race officer</b> account can do everything in this guide, but "
    "<b>Settings is read-only</b> and restoring a backup is reserved for an <b>admin</b> account. If Settings "
    "looks greyed-out with a “read-only” note, you're signed in as a race officer — ask an admin to make "
    "configuration changes.")
story += note_box(
    "To change your <b>own password</b>, click your username in the top bar to open the <b>My account</b> page. "
    "This works whatever your role — you don't need admin access to change your own password. Enter your current "
    "password to confirm, then the new one. Administrators can also reset any user's password from Settings → Users.")

story.append(Paragraph("1.2 &nbsp; The dashboard", styles["H2"]))
story.append(Paragraph(
    "After signing in you land on the <b>Dashboard</b>. It is a single at-a-glance view of race-office status: "
    "the currently active race and its countdown clock, the live wind reading, the marks and any trackers "
    "reporting, and whether the horn and video systems are configured. Everything on it is something that "
    "changes during a day; the club's standing start-line, finish-line and radio notes belong to the Sailing "
    "Instructions and are no longer repeated here.",
    styles["Body"]))
story.append(figure("02_dashboard.png", "The Dashboard. “Current race” always tracks the most relevant "
                                         "race — this is also what the public competitor page shows by default."))
story.append(Paragraph(
    "If the hut runs off-grid, a <b>Hut power</b> card also shows the battery charge, solar input and hut "
    "consumption from the Victron devices, with a fuller history graph under <b>Hut power</b> in the sidebar "
    "(grouped with Documentation and Backup). Setting it up is covered in the Reference Manual.", styles["Body"]))

story.append(Paragraph("1.3 &nbsp; Check your boats before the series starts", styles["H2"]))
story.append(Paragraph(
    "The <b>Boats</b> database stores every boat once, with both its IRC and YTC ratings, so you do not have to "
    "re-type them for every race. Before starting a new series it is worth a quick check that ratings are "
    "current — add any new boats and update any changed ratings here first.", styles["Body"]))
story.append(figure("14_boats_list.png", "The Boat database. Boats can be looked up from the IRC/YTC rating "
                                          "lists or entered manually."))
story += note_box(
    "Ratings are copied into a race as a <b>snapshot</b> the moment a boat is added to that race — they are "
    "not looked up live afterwards. If you correct a boat's rating in the database, it will apply to races you "
    "add that boat to <i>from now on</i>, not to entries already created. You can still edit an individual "
    "entry's rating on the race's “Entries &amp; finish times” tab if a correction is needed after the fact.")

story.append(PageBreak())

# ============================================================== CHAPTER 2 - GLOSSARY
story += chapter_heading("Chapter 2", "Key concepts, in plain language")
story.append(Paragraph(
    "A handful of ideas recur throughout the app. It is worth understanding them before setting up your first "
    "series — they explain why some settings live on the series and others on the individual race.", styles["Body"]))

glossary = [
    ("Series and race", "A <b>series</b> (e.g. “Autumn Series 2025”) is a named group of races that are "
     "scored together over a season, with discards. A <b>race</b> is one race sheet: one start, one course, one "
     "set of entries and finish times. A race can belong to a series, or stand alone."),
    ("First warning signal vs. first start", "You set the <b>first warning signal</b> time on a race. The actual "
     "<b>first start</b> is always five minutes later. Every other start in a multi-start race plan is defined "
     "as an offset in minutes from that first start, not from the warning signal."),
    ("IRC and YTC", "Two independent handicap rating systems, scored side by side in every race and series. "
     "<b>IRC</b> corrected time = elapsed time × IRC TCC (shown to three decimal places). <b>YTC</b> corrected "
     "time = elapsed time × 1000 ÷ YTC number. A boat only appears in the table for a system it has a "
     "rating for."),
    ("Discard profile", "A comma-separated list such as <b>0,0,1,1,1,1,2,2,2</b> that says how many of a "
     "competitor's worst scores are discarded once 1, 2, 3… races have been completed. If more races are "
     "sailed than there are numbers in the list, the last number is reused. Discarded scores are shown "
     "struck-through in results tables."),
    ("Series constitution", "A series class is marked “constituted” once at least the configured "
     "minimum number of races (commonly 3) have been scored for it. Below that threshold, results are still "
     "shown, but labelled “Not yet constituted”."),
    ("Rating-band classes", "A series can define up to three IRC and three YTC classes, each with a name, a "
     "numeral class flag, and an optional rating band (e.g. IRC Class 1 = 1.000 and above). Boats are grouped "
     "into these classes automatically by their rating."),
    ("Start plan", "Up to six starts can be defined for a series, each with a time offset from the first start "
     "and a choice of which classes start on it. Classes that share both a rating system and a start are also "
     "shown combined as an “Overall” table."),
]
for term, definition in glossary:
    story.append(Paragraph(term, styles["GlossaryTerm"]))
    story.append(Paragraph(definition, styles["Body"]))

status_intro = [
    Paragraph("Entry status codes", styles["H2"]),
    Paragraph(
        "Every entry carries a status, set on the “Entries &amp; finish times” tab. These follow standard "
        "Racing Rules of Sailing abbreviations:", styles["Body"]),
]

status_rows = [
    ["PRESTART", "Entered and waiting — shown until the race's warning signal time"],
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
status_table = Table([["CODE", "MEANING"]] + status_rows, colWidths=[3.2 * cm, CONTENT_W - 3.2 * cm], repeatRows=1)
status_table.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), NAVY),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE", (0, 0), (-1, -1), 9),
    ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
    ("TEXTCOLOR", (0, 1), (0, -1), NAVY_DARK),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ("GRID", (0, 0), (-1, -1), 0.5, LINE),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
]))
story.append(KeepTogether(status_intro + [status_table]))
story += note_box(
    "There is no built-in protest or redress workflow. If the race committee awards redress or upholds a "
    "protest, apply the outcome by editing the affected entry's status or rating directly, then re-check the "
    "results.")

story.append(PageBreak())

# ============================================================== CHAPTER 3 - SET UP SERIES
story += chapter_heading("Chapter 3", "Setting up a series")
story.append(Paragraph(
    "Start on the <b>Series</b> page in the side menu. A series holds the settings that every race added to it "
    "will share by default: its discard profile, its rating-band classes, and its default start plan.", styles["Body"]))

story.append(Paragraph("3.1 &nbsp; Create the series", styles["H2"]))
story.append(Paragraph(
    "On the Series page choose <b>Add series</b>. Give it a name (e.g. “Spring Series” or "
    "“Wednesday Evening Series”) and, on the same page, set the discard profile, the rating-band "
    "classes and the default start plan — it is the same form as <b>Edit series details</b>, so a "
    "series can be set up in one pass rather than created blank and then immediately edited. Everything "
    "on it can be changed later.", styles["Body"]))
story.append(figure("03_series_list.png", "The Series page. Winter Series 2027 has no races in it yet and can be deleted; every series that has been raced shows In use instead."))

story.append(Paragraph("3.2 &nbsp; Set the discard profile and constitution rule", styles["H2"]))
story.append(Paragraph(
    "This and the two sections that follow are all on the same form — the one you are already on when "
    "adding the series, and the one behind <b>Edit series details</b> on the series afterwards. The discard "
    "profile controls how many of each competitor's worst scores are dropped as the series progresses, and "
    "“Minimum races to constitute series” sets when a class's results stop being provisional.",
    styles["Body"]))
story.append(figure("crop_classes.png", "Series discard profile, constitution threshold, and the publish/export "
                                         "buttons this guide returns to in Chapter 11."))

story.append(Paragraph("3.3 &nbsp; Define the rating-band classes", styles["H2"]))
story.append(Paragraph(
    "Tick up to three IRC classes and three YTC classes, name each one, choose its numeral class flag, and "
    "optionally bound it by rating (e.g. IRC Class 1 from 1.000 upwards). Leave a bound blank for an "
    "open-ended band.", styles["Body"]))
story.append(Paragraph(
    "On a new series <b>nothing is ticked</b>: a series created without touching this section has no bands "
    "at all and scores as one fleet per rating rule, which is what a single-fleet series wants. Tick a row "
    "only for a series that really is split by rating.", styles["Body"]))
story.append(figure("crop_classes2.png", "IRC rating-band classes, each with a name, numeral flag and rating band."))

story.append(Paragraph("3.4 &nbsp; Set the default start plan", styles["H2"]))
story.append(Paragraph(
    "Tick up to six starts, give each an offset in minutes from the first actual start, and choose which classes "
    "start on it. This becomes the default for every new race added to the series — it can still be reviewed "
    "per race on the race's own “Course &amp; start” tab.", styles["Body"]))
story.append(Paragraph(
    "The classes here follow the bands you ticked in the section above, as you tick them: a band appears on "
    "every start the moment it exists, and renaming it renames it here. Untick it from a start to give that "
    "band a start of its own — a second start five minutes later for the slower band is the usual shape.",
    styles["Body"]))
story.append(figure("crop_startplan.png", "The default start plan: offsets are minutes after the first start, "
                                           "which is five minutes after the first warning signal."))
story.append(Paragraph(
    "Select <b>Create series</b> if you are adding one, or <b>Save series</b> if you came back to it through "
    "<b>Edit series details</b>. Either way the whole form is saved together.", styles["Body"]))

story.append(Paragraph("3.5 &nbsp; Deleting a series", styles["H2"]))
story.append(Paragraph(
    "A series can be removed — from the <b>Series</b> list, or from the bottom of <b>Edit series "
    "details</b> — but only once <b>no races are in it</b>. One that still holds racing shows "
    "<i>In use</i> where the button would be, and the series page says how many races are in the way.",
    styles["Body"]))
story.append(Paragraph(
    "To delete one that is in use, empty it first: open each race's “Course &amp; start” tab and set its "
    "series to another one or to <i>(none)</i>, or delete the races. The button then appears.",
    styles["Body"]))
story += note_box(
    "The rule is deliberate. A series is not just a label on a group of races — it carries the rating "
    "bands, the start plan and the discard profile those races were scored under. Deleting it with races "
    "still attached would drop them out of the standings while they went on claiming to belong to it.")

story.append(PageBreak())

# ============================================================== CHAPTER 4 - CREATE RACE
story += chapter_heading("Chapter 4", "Creating and setting up a race")

story.append(Paragraph("4.1 &nbsp; Create the race sheet", styles["H2"]))
story.append(Paragraph(
    "From the Series page, use <b>New race in this series</b> (or <b>New race</b> in the side menu, then choose "
    "the series from the dropdown). Race creation is deliberately minimal: give it a name and decide whether "
    "to bulk-add every active boat from the database straight away. A race takes its classes from its series' "
    "rating bands, so there is nothing to type here about class or fleet. The course and warning-signal time "
    "are set next, on the race sheet itself.", styles["Body"]))
story.append(figure("07_new_race_form.png", "Creating a race: name, series and an optional bulk entries "
                                             "checkbox. Select “Create race sheet” to continue."))

story.append(Paragraph("4.2 &nbsp; Set the course and warning-signal time", styles["H2"]))
story.append(Paragraph(
    "On the new race sheet's <b>1. Course &amp; start</b> tab, set the <b>First warning signal time</b> — "
    "the actual start follows five minutes later automatically. Then choose a course: pick a fixed course number "
    "directly, use <b>Recommend course</b> to have the app suggest one of the club's standard courses "
    "from the live start-hut wind and a chosen boat polar, or use <b>Build manual course</b> to lay out a "
    "one-off course from individual marks. Once a manual course is active it replaces the course-number field "
    "until you clear it.", styles["Body"]))
story.append(Paragraph(
    "<b>A “?” beside a heading opens the explanation.</b> The race page keeps the controls at the top and the reasoning one click away, so what stays on screen is what changes what you do next — <i>AP is up since 14:02</i>, <i>this race has started</i>, <i>whole minutes only</i>. The <i>why</i> and the worked examples are behind the <b>?</b>, and everything in those popups is also in this manual.", styles["Body"]))
story.append(figure("crop_course_top.png", "The Course &amp; start tab: warning-signal time and course selection.",
                     max_h=12 * cm))
story.append(figure("crop_course_bottom.png", "Below the form: the selected course's chart, the fleet's position "
                                               "on the water, and the predicted leg-by-leg timing calculated from "
                                               "the chosen polar and the latest start-hut wind.",
                     max_h=16 * cm))

story.append(Paragraph("Courses that go round a headland", styles["H2"]))
story.append(Paragraph(
    "A long course west — to the Gwylan Islands, say — does not sail in a straight line, because the "
    "Llŷn peninsula is in the way. Left to itself the app draws the leg straight across the land, and it "
    "does more than look odd: it measures the leg <b>10.9 nm</b> against the <b>13.1 nm</b> actually sailed, so "
    "distance-to-go and the predicted leaderboard are a fifth short for the whole leg, and it treats one bearing "
    "of 243° as the leg when the boats really sail 211° and then 278° — a reach and a broad "
    "reach modelled as neither.", styles["Body"]))
story.append(Paragraph(
    "A <b>waypoint</b> fixes that. It is a turning point, not a mark: add it to the course wherever the course "
    "would otherwise cut across the land, and the course bends there. The club has two, <b>TC</b> off Trwyn "
    "Cilan and <b>TP</b> off Porth Ceiriad. In the manual course builder they have a single <b>Add</b> button "
    "instead of Port and Starboard, and show as a grey <b>via TC</b> chip.", styles["Body"]))
story += note_box(
    "Nothing else changes. A waypoint is <b>not</b> on the course board, not in the announcement, not offered "
    "as a place to shorten the course, not counted in a boat's marks-rounded tally, and not drawn on the chart "
    "— the leg simply changes direction there. Competitors are being told the same course as before; the "
    "app is just measuring it the way they sail it. Put one on both legs of an out-and-back course.")
story.append(Paragraph(
    "Add a waypoint on the <b>Marks</b> page like any other mark, with the <b>Waypoint</b> box ticked, and put "
    "it in the water on the line boats actually sail — a little outside the headland. It needs no rounding "
    "radius, because nothing rounds it: a boat counts as past it once it draws level with it, however far "
    "offshore that happens. That matters on a beat, where the fleet can pass a mile or more off.", styles["Body"]))

story.append(PageBreak())

# ============================================================== CHAPTER 5 - ENTRIES
story += chapter_heading("Chapter 5", "Adding entries")
story.append(Paragraph(
    "Move to the <b>2. Add entries</b> tab. Boats are added to the race from the boat database, either one at "
    "a time or in bulk:", styles["Body"]))
story.append(bullets([
    "<b>Bulk add</b> — add every active boat in the database, or every boat already known to be in a "
    "particular class, in one click.",
    "<b>Add from boat database</b> — look up one existing boat and add it, optionally overriding its class "
    "for this race.",
]))
story.append(figure("09_race_tab2_entries.png", "Add entries: current entries for the race, plus the bulk-add "
                                                 "and add-from-database controls."))
story += note_box(
    "Boats without a rating for a particular system are still entered — they will simply be excluded from "
    "that system's result table until a rating is added.")

story.append(PageBreak())

# ============================================================== CHAPTER 6 - START SEQUENCE
story += chapter_heading("Chapter 6", "Running the start sequence")
story.append(Paragraph(
    "The <b>3. Start console &amp; log</b> tab is the working screen for race day. The <b>Scheduled signal "
    "plan</b> table is generated automatically from the warning-signal time and the start plan: it shows exactly "
    "when each flag goes up or down, when the horn sounds, and any scripted audio announcements.", styles["Body"]))
story.append(figure("10_race_tab3_start.png", "Start console &amp; log: manual controls at the top, the full "
                                               "scheduled signal plan below, and the horn/race event log."))
story.append(Paragraph(
    "The manual controls above the signal plan cover the situations the schedule cannot predict:", styles["Body"]))
story.append(bullets([
    "<b>Fire horn now</b> — sound the horn immediately, outside the scheduled plan.",
    "<b>Individual recall</b> — log that one or more boats were called back over the line.",
    "<b>General Recall</b> — log a full recall of the start.",
    "<b>Postpone (AP)</b> — the real thing, not a note: two horn blasts, the start sequence held, and AP on every page that shows flags. See below.",
    "<b>Abandon</b> — log an abandoned race.",
]))
story += note_box(
    "The signal panel is guidance, not a substitute for judgement. The race officer remains responsible for "
    "physically hoisting and lowering the correct flags at the right moment, and for the on-the-water decision "
    "to recall, postpone or abandon — always check the displayed flags match what is actually flying.",
    kind="warn")

story.append(Paragraph("Postponing a start (AP)", styles["H2"]))
story.append(Paragraph(
    "If the wind dies, the line is not ready or the fleet is not there, <b>postpone — do not move the "
    "start time</b>. Moving it signals nothing to the fleet, and if the sequence is already running it "
    "drops the rest of it: boats hear a warning, then no preparatory signal and no gun. The controls are "
    "on this tab because this is the tab you are on before a start.", styles["Body"]))
story.append(bullets([
    "<b>AP</b> — races not started are postponed. The warning signal will be made one minute after "
    "AP is removed.",
    "<b>AP over H</b> — postponed, further signals ashore.",
    "<b>AP over A</b> — postponed, no more racing today.",
]))
story.append(Paragraph(
    "Choosing one sounds <b>two horn blasts</b>, holds the start sequence — nothing sounds while the "
    "flag is up — and shows AP on the race sheet, the competitor pages and the clubhouse display. The "
    "race keeps its scheduled time; the flag is what changed.", styles["Body"]))
story.append(Paragraph(
    "When the wind is back you say <b>when AP comes down</b>, in whole minutes, at least a minute and a "
    "half ahead. <b>The warning signal follows one minute later and the gun five minutes after that</b>, "
    "so 14:20 gives a warning at 14:21 and a first gun at 14:26. You are choosing when you are ready, "
    "not guessing when the wind will return — that is what the flag is for. The fleet hears the time "
    "when you set it, again if you change it, a minute before the flag moves, and once more as it comes "
    "down with its single sound.", styles["Body"]))
story += note_box(
    "AP postpones races that have <i>not started</i>. After the first gun it is not offered: stopping a "
    "race under way is abandonment (flag N), which this app does not signal — if the wind has died "
    "mid-race, <b>shorten the course</b> instead.", kind="warn")
story.append(figure("race_tab_start_postponed.png", "AP flying, with the minute it comes down set: the clock "
                                                    "reads Postponed, and the signal plan shows what is held "
                                                    "and what happens instead."))
story += note_box(
    "If the hut PC is set up to make spoken VHF announcements and the radio is keyed by <b>VOX</b>, an admin can "
    "enable a short <b>wake-up tone</b> (Settings → Horn settings &amp; Test) that plays a couple of seconds "
    "before each announcement, so the radio is already transmitting and the first words are not clipped.")

story.append(PageBreak())

# ============================================================== CHAPTER 7 - SHORTENING THE COURSE
story += chapter_heading("Chapter 7", "Shortening the course")
story.append(Paragraph(
    "If the wind drops or time runs short, you can shorten the course so boats finish at a rounding mark instead "
    "of sailing the whole course. Use the <b>4. Shorten course</b> tab on the race sheet (between <b>3. Start "
    "console &amp; log</b> and <b>5. Entries &amp; finish times</b>).", styles["Body"]))
story.append(Paragraph(
    "Choose the mark you want to finish at from the course sequence and select <b>Call shortened course</b>. "
    "The app then:", styles["Body"]))
story.append(bullets([
    "sounds <b>two horn blasts</b>, and — once the horns have finished, so it is not drowned out — makes the "
    "central-audio announcement <i>“Shortened course called on mark &lt;mark&gt; — after this mark proceed to "
    "finish”</i>, repeated a few seconds later;",
    "flies International Code flag <b>S</b> (a blue square on white) in the flag panel on both the race sheet and "
    "the public competitor page, and keeps it up until all boats are no longer racing;",
    "shows a <b>Shortened course</b> banner and records the call in the race log;",
    "shortens the shown course (and the predicted-time / leg analysis) so it ends at that mark, with "
    "<b>&#8594; Finish</b> after it, and draws the final leg on the course chart as a dashed line from that mark "
    "straight to the finish line.",
]))
story.append(Paragraph(
    "Boats round the chosen mark and then sail directly to the finish line as normal. Finish times are recorded on "
    "the <b>5. Entries &amp; finish times</b> tab exactly as for a full course (Chapter 8), and results are "
    "calculated the same way. If you call it in error, select <b>Clear shortened course</b> to undo.", styles["Body"]))
story += note_box(
    "The spoken announcement is only heard over the VHF/PA if central audio is enabled on the hut PC. The race "
    "officer is still responsible for physically displaying Code flag S and for the on-the-water decision to "
    "shorten — the app signals and records the call, it does not make it for you.",
    kind="warn")
story.append(figure("race_tab_shorten.png", "The Shorten course tab: pick a mark from the course and press "
                                            "Call shortened course."))

story.append(PageBreak())

# ============================================================== CHAPTER 8 - FINISHES
story += chapter_heading("Chapter 8", "Recording finishes")
story.append(Paragraph(
    "As boats cross the line, use the <b>5. Entries &amp; finish times</b> tab. Selecting the blue <b>Finish</b> "
    "button next to a boat records its finish time, sounds the horn and adds a log entry, all in one action. "
    "For boats that do not finish normally, set the appropriate status instead (see the status-code table in "
    "Chapter 2) — finish time is then left blank.", styles["Body"]))
story.append(figure("11_race_tab4_finish.png", "Entries &amp; finish times: one row per boat, with its ratings, "
                                                "status, finish time and elapsed time. The horn/race log is below."))
story.append(Paragraph(
    "Each entry's IRC and YTC rating is editable here — useful if a correction is needed after the boat was "
    "originally entered. Remember to select <b>Save</b> on a row after making changes. A live finish-camera view "
    "can also be shown from this tab if one is configured: it starts as near-live stills and, where the club "
    "runs the public live stream, changes to smooth video from the relay.", styles["Body"]))
story += note_box(
    "The relay video is a few seconds behind the water — it travels hut → relay → Cloudflare "
    "→ back to you. Use it to watch, not to time: take finish times from the horn and check them against "
    "the recorded finish clips, which are cut from the hut's own recording. It also uses the hut's connection "
    "twice over, so on 4G keep the panel closed when you are not watching it.", kind="warn")

story.append(PageBreak())

# ============================================================== CHAPTER 8 - RACE RESULTS
story += chapter_heading("Chapter 9", "Race results")
story.append(Paragraph(
    "The <b>6. Results</b> tab calculates corrected results as soon as finish times and statuses are recorded "
    "— there is no separate “compute results” step. Separate tables are produced for IRC and YTC, "
    "further split by the series' rating-band classes, with an overall table where classes share a start.",
    styles["Body"]))
story.append(figure("crop_results_top.png", "Race results (1 of 2): the Results tab and IRC overall results, "
                                             "continuing into IRC Class 1.", max_h=16 * cm))
story.append(PageBreak())
story.append(figure("crop_results_bottom.png", "Race results (2 of 2): IRC Class 2 and YTC results, with boats "
                                                "excluded from the YTC table for missing a rating listed below it.",
                     max_h=16 * cm))
story.append(Paragraph(
    "Use <b>Sailwave CSV (IRC)</b> / <b>(YTC)</b> to export this race for import into Sailwave, where the club "
    "keeps its published history. It is an import file rather than a report to read &mdash; one row per "
    "competitor, one file per rating system, and no finishing places, because Sailwave scores from elapsed "
    "time and rating itself. For results a person reads &mdash; for your own records, or for a protest "
    "committee &mdash; use the HTML publish in Chapter 11.", styles["Body"]))
story.append(Paragraph(
    "Competitors see the same classed tables on the public race page as soon as every boat has stopped racing: "
    "a <b>Leader board</b> tab appears there and opens first, while the entries, chart and course analysis stay "
    "available. Those last two switch to the <b>average wind over the race</b> (warning signal to last finish) "
    "as a record of the conditions, so nothing you do afterwards changes what they show.", styles["Body"]))

story.append(PageBreak())

# ============================================================== CHAPTER 9 - SERIES RESULTS
story += chapter_heading("Chapter 10", "Series results")
story.append(Paragraph(
    "Back on the series page, scroll to <b>Series results</b>. Every scored race in the series feeds into these "
    "tables automatically — nothing needs to be recalculated by hand. Discarded scores appear struck "
    "through, ties are broken using RRS Appendix A8 (race ties use Appendix A7 equal-points scoring), and each "
    "table shows whether the class is yet constituted.", styles["Body"]))
story.append(figure("06_series_detail_results.png", "Series results: one table per rating-band class (and an "
                                                      "overall table where classes share a start), with running "
                                                      "totals and discards across every race sailed so far."))

story.append(PageBreak())

# ============================================================== CHAPTER 10 - PUBLISH
story += chapter_heading("Chapter 11", "Publishing results to the club website")
story.append(Paragraph(
    "Publishing happens from the <b>Series</b> page, not the individual race page — it always produces a "
    "complete standalone results document covering every race in the series. Four buttons sit next to "
    "<b>Add race to series</b>:", styles["Body"]))
story.append(bullets([
    "<b>Sailwave CSV (IRC)</b> / <b>(YTC)</b> — every race in the series as one Sailwave import file, numbered by its position in the series so importing week by week does not overwrite race 1.",
    "<b>Preview HTML</b> — opens the results document in your browser to look at before sharing. It publishes nothing; the download beside it is the file you send.",
    "<b>Download HTML</b> — saves that same document as a single self-contained <b>.html</b> file.",
    "<b>Publish to website</b> — puts that same document straight onto the club's public web "
    "storage and gives you a link. This is the one to use at the end of a day's racing.",
]))
story.append(figure("04_series_detail_top.png", "The series page toolbar: Add race to series, the Sailwave CSV exports, "
                                                 "Preview HTML and Download HTML, above the list of races in the series."))
story.append(Paragraph(
    "<b>Publish to website</b> (v0.281) does the uploading for you. It sends the results as they stand to "
    "the club's public storage — the same place the race videos go — and each series gets "
    "its own folder there. The button appears only once an administrator has set that storage up in "
    "<b>Settings \u2192 Video recording</b>.", styles["Body"]))
story.append(Paragraph(
    "Every publish saves <b>two</b> copies. One is dated and never changes, so there is always a record "
    "of exactly what went out on Saturday evening. The other is always called <b>latest</b> and is "
    "overwritten each time you publish. <b>Put the latest link on the club website.</b> Paste it once and "
    "it keeps itself up to date for the rest of the season — you never have to touch the website "
    "again.", styles["Body"]))
story.append(Paragraph(
    "<b>Published\u2026</b> beside the button lists everything that has gone out for that series, with "
    "the club-website link in a box to copy and each dated publish openable. Competitors see it too: the "
    "competitor home page shows a <b>Published results</b> link on the series, pointing at the same "
    "document the club website shows.", styles["Body"]))
story.append(Paragraph(
    "<b>Download HTML</b> is still there for a club that would rather upload the file itself, through "
    "the website's own file manager or admin area.", styles["Body"]))
story.append(figure("13_publish_html.png", "The published results document: club/sponsor branding, a jump-to-race "
                                            "toolbar, and colour-coded 1st/2nd/3rd places in each results table."))
story += note_box(
    "The published file is a <b>snapshot</b>, provisional as of the moment you published it. If you later "
    "correct a finish time, add a late entry, or a race is rescored, the club website keeps showing the "
    "old numbers until you publish again. Press <b>Publish to website</b> after every meaningful change "
    "— the link on the club website never needs touching, only the publishing does.",
    kind="warn")

story.append(PageBreak())

# ============================================================== CHAPTER 11 - PURSUIT RACES
story += chapter_heading("Chapter 12", "Pursuit races")
story.append(Paragraph(
    "A <b>pursuit race</b> is an alternative to the class-based races covered so far. It runs for a fixed "
    "period; the slower boats start first and the faster boats later, so on handicap they would all finish "
    "together. There are no classes, and there is a single finish signal rather than individual finish times — "
    "the boats' order across the line <i>is</i> the result.", styles["Body"]))
story.append(figure("pursuit_new_race.png", "Creating a pursuit race: choose the Pursuit type, its rating "
                                            "system and the fixed period.", max_h=9 * cm))

story.append(Paragraph("12.1 &nbsp; Setting it up", styles["H2"]))
story.append(numbered([
    "On the <b>New race</b> page choose <b>Race type → Pursuit</b>, pick <b>IRC</b> or <b>YTC</b> as the "
    "single rating system that sets the start times, and set the fixed period in minutes. You can add all "
    "active boats at the same time, just like a standard race.",
    "On the race sheet's <b>Course &amp; start</b> tab, set the <b>first warning-signal time</b> and save. The "
    "slowest-rated boat starts first, five minutes after the warning; every other boat's start time is worked "
    "out from its rating and recomputed automatically whenever you add or remove a boat.",
    "Choose a course exactly as for a standard race — a fixed numbered course, <b>Recommend course</b>, "
    "or <b>Build manual course</b>.",
]))
story += note_box(
    "The slowest-rated boat starts first; each faster boat is delayed by "
    "<b>period × (1 − slowest speed ÷ boat speed)</b>, derived from the chosen rating. Until you have set the "
    "warning time and period, boats show <b>“set start time”</b> rather than a time — that is different from a "
    "boat with <i>no rating</i> in the chosen system, which is still entered but flagged separately so you can "
    "add a rating or leave it out. There are no classes in a pursuit race.")

story.append(Paragraph("12.2 &nbsp; Running the start", styles["H2"]))
story.append(Paragraph(
    "The <b>Start console</b> works like the standard one: it shows the start ladder — every boat and its start "
    "time — with the next boat to start highlighted and a live countdown. Horns fire automatically: the "
    "five-minute warning sequence before the first start (flying the normal flags, defaulting to class 1), one "
    "start signal at each boat's start time, then the single finish signal at the end of the period. "
    "<b>Fire horn now</b> is there for a manual signal. There is a start video for each start time and no finish "
    "video.", styles["Body"]))
story.append(figure("pursuit_start_console.png", "The pursuit start console: the start ladder with the next "
                                                 "boat to start highlighted.", max_h=11 * cm))

story.append(Paragraph("12.3 &nbsp; Finishing and results", styles["H2"]))
story.append(Paragraph(
    "At the finish signal, judge the boats by their order on the water and record it on the "
    "<b>Finish &amp; positions</b> tab — a position number and status for each boat. There is no time "
    "correction: the finishing order is the result, shown on the <b>Results</b> tab. If the pursuit race belongs "
    "to a series it scores into the standings by finishing position, exactly like the places in a normal race. "
    "Competitors see each boat's start time on the public race page (with the next to start highlighted), and "
    "the finishing order once you have recorded it.", styles["Body"]))

story.append(PageBreak())

# ============================================================== CHAPTER 13 - GPS TRACKING
story += chapter_heading("Chapter 13", "GPS tracking and automated finishes")
story.append(Paragraph(
    "If the club runs GPS trackers on the boats, you can watch the fleet live and let the app help record "
    "finishes. Positions are shown on your race sheet and, since v0.177, on the public competitor race page "
    "while the race is on. The trackers report to a tracking server on the relay, and the app pulls their "
    "positions.", styles["Body"]))
story.append(Paragraph("13.1 &nbsp; Setting up trackers", styles["H2"]))
story.append(Paragraph(
    "Open the <b>Trackers</b> page from the bottom of the side menu (race officers can use it, not just "
    "administrators). The easiest way to add one is to switch the tracker on and look under <b>Trackers seen on "
    "the network</b> — devices the tracking server has heard from but that are not set up here yet appear in "
    "that list, most recently heard from first. Pick the boat it is on and press <b>Add</b>. There is nothing to "
    "type. That list is the only way in — a form for typing a 15-digit IMEI by hand was removed in v0.270.", styles["Body"]))
story.append(Paragraph(
    "Each tracker shows how long ago it last reported with a coloured dot — green within 5 minutes, amber within "
    "the hour, red after that — and the page updates on its own. A boat's track always stays with the boat, so "
    "you can safely move a tracker to another boat between races.", styles["Body"]))
story += note_box(
    "The trackers are battery units built for months of standby, so their normal reporting is far too slow to "
    "time a finish. Set a fast reporting rate for race days — and charge them between race days, because that "
    "fast rate costs a great deal more battery than the standby schedule does.")
story.append(Paragraph(
    "The <b>Type</b> column names each tracker's model, worked out from its IMEI. It is worth a glance, because "
    "the club's trackers are not alike: the small Teltonika asset tracker is the most accurate of them and will "
    "flatten in about nine hours of racing, while the Queclink units are less precise and will sail a long "
    "night race on around a tenth of their charge. If the app does not recognise a model it shows "
    "<i>unknown</i> and offers to let you name it. Administrators also get a <b>Commands</b> button for talking "
    "to a tracker directly; that is covered in the reference manual.", styles["Body"]))
story.append(Paragraph(
    "Which is why the tracker tables carry a <b>Battery</b> column, reported by the tracker itself with each "
    "position: <b>green</b> above 25%, <b>amber</b> at 25% or below, <b>red</b> at 10% or below. A tracker that "
    "has not reported one shows a dash rather than 0% — an unknown battery and a flat one are not the same "
    "thing, and the older units do not send it at all. The <b>Dashboard</b> warns when any tracker "
    "<b>assigned to a boat</b> is at or below 25%, flattest first, naming the boat, so a tracker due a charge is "
    "noticed on the Friday rather than at the outer mark. Spares on the shelf are left out of the warning on "
    "purpose: they are not going racing tomorrow.", styles["Body"]))
story.append(figure("dash_battery_warning.png",
                    "The dashboard's reminder to put two trackers on charge.", max_h=5 * cm))
story.append(Paragraph(
    "<b>A phone can be a tracker too.</b> The free <b>Traccar Client</b> app reports in the same way: point it at "
    "the club's tracking server, give it a device name, then add that name on the Trackers page and assign it to "
    "a boat like any other. Useful for the rescue RIB or committee boat, for a volunteer's phone when there are "
    "more boats than trackers, and for trying the whole thing out before the hardware arrives. It does flatten a "
    "phone battery at race-day reporting rates. Your relay administrator has the server address.", styles["Body"]))
story.append(figure("trackers_page.png", "The Trackers page: assign each tracker to a boat; the dot shows how "
                                         "recently it reported.", max_h=9 * cm))
story.append(Paragraph("13.2 &nbsp; The live map and position on the water", styles["H2"]))
story.append(Paragraph(
    "On the race sheet's <b>Course &amp; start</b> tab, tracked boats appear on the course chart and in a live "
    "<b>Position on the water</b> list, ordered by how far round the course each boat is — the marks it has "
    "rounded, the next mark, and "
    "the distance still to sail.", styles["Body"]))
story.append(figure("race_track_map.png", "The live fleet on the course chart.", max_h=9 * cm))
story.append(figure("race_track_leaderboard.png", "Position on the water: every entered boat, ordered by "
                                                 "progress round the course.", max_h=6 * cm))
story.append(Paragraph(
    "The app works through the course in order, and counts a mark as rounded on any of three tests: the boat "
    "passed within its <b>rounding radius</b> (50&#160;m by default); or it crossed a <b>gate</b> through the "
    "mark reaching 750&#160;m on the hand the course requires and only the radius on the hand it does not; or "
    "it came close and has since left again with the next mark nearer. It does not judge how tidy the rounding "
    "was, which is the fleet's business and not the race office's — but the gate does mean a boat giving a mark "
    "a wide berth on the correct side still counts, and it is the only part of the app that knows a mark has a "
    "required side at all.", styles["Body"]))
story.append(Paragraph(
    "A suspected wrong side is reported, never refused. Refusing it would stall that boat for the rest of the "
    "race and take its automatic finish with it, to enforce a rule the app does not adjudicate — you do, on a "
    "protest, with the video and the track.", styles["Body"]))
story.append(Paragraph(
    "Occasionally it still gets it wrong: a mark has dragged a long way, or the tracker slept through the "
    "rounding. The <b>arrows at the end of each row</b> put it right — <b>›</b> to count the next mark as "
    "rounded, <b>‹</b> to take one back. They stop at the ends, so you cannot go past the finish or before "
    "the start.", styles["Body"]))
story += note_box(
    "A correction applies <b>from now on</b>, not backwards through the race, so it does not rewrite a chart "
    "replay the fleet may already have watched. It does reach the automatic finish detection: nudge a boat on "
    "to its last mark and its next crossing of the line can be taken as a finish. Every press is recorded in "
    "the activity log with who made it.")
story.append(Paragraph("13.3 &nbsp; Automated finishes", styles["H2"]))
story.append(Paragraph(
    "<b>Arm GPS auto-finish</b> is already ticked on a new race, on the <b>Entries &amp; finish times</b> "
    "tab; <b>Auto-confirm</b> is not. Once a boat has rounded every mark and crossed the line the app detects "
    "the finish, and that detection waits as a <b>proposal</b> — the boat and time, with a link to the finish "
    "video so you can check it — for you to confirm or dismiss. On a course that passes the start/finish mark "
    "part-way round, only the final crossing counts.", styles["Body"]))
story.append(Paragraph(
    "Tick <b>Auto-confirm</b> and the finish is recorded the moment it is detected, with nobody asked. That is "
    "what a race with an empty hut needs, and the on-the-water page ticks it by itself for a race started from "
    "a boat. For a race you are watching, leave it off: accepting a detection costs one click, and a wrong "
    "finish written straight into the results of a race still being sailed has to be spotted first.",
    styles["Body"]))
story += note_box(
    "GPS gives the approximate time and order; the finish video is still the arbiter. On race days set the "
    "trackers to report often, or the timing between close boats will be too coarse.")
story.append(Paragraph(
    "<b>If a boat plainly finished and got no automatic finish</b>, the usual reason is the seaward end of the "
    "line. It is a laid buoy: it swings and it drags, and a boat that passes inside the real buoy but outside "
    "where the app thinks it is falls off the end of the line and is never seen crossing. The app now allows for "
    "that — a set distance past the mark still counts, 150 m on the club line and 250 m on the ISORA one "
    "— but that is for drift between measurements. <b>If a line mark has moved, re-measure it</b> from the "
    "Marks page, or from a phone alongside it. The shore end is a surveyed transit and never needs this.",
    styles["Body"]))

story.append(PageBreak())
story.append(Paragraph("13.4 &nbsp; What competitors see", styles["H2"]))
story.append(Paragraph(
    "The public race page gains a <b>Chart</b> tab showing the same fleet. It opens at the latest positions and "
    "can be wound back through the race so far, or played at up to 60x. Under it a <b>Leaderboard</b> panel rolls "
    "up over the chart offering the order on the water or, where the fleet is rated for it, an "
    "<b>IRC or YTC corrected</b> order.", styles["Body"]))
story.append(Paragraph(
    "For a boat still racing that corrected order has to <b>project</b> a finish time, and the competitor "
    "chooses how: <b>average pace since the start</b> (what it opens on — how much of the course the boat has "
    "covered and how long that took), <b>recent pace</b> over the last twenty minutes, or a <b>polar pace "
    "factor</b> comparing its time so far with the target time for the legs it has sailed. Whichever is picked "
    "drives the order <i>and</i> the times printed beside it, so the board cannot contradict itself. Nothing is "
    "shown for the first ten minutes of a race, counted from the gun, and the page labels the whole thing an "
    "estimate.", styles["Body"]))
story.append(Paragraph(
    "The simplest is the default, and not because it is the most accurate — it is not. Its error is much the "
    "same for every boat at any moment, because the whole fleet has just sailed the same slow beat; a "
    "leaderboard ranks boats against each other, so an error they all share cancels out.", styles["Body"]))
story += note_box(
    "Expect to be asked about it. It is a competitor's toy, not a result: the Results tab and the scores you "
    "publish afterwards remain the authoritative record, and a boat's position on that live board carries no "
    "weight in a protest.")

story.append(Paragraph("13.5 &nbsp; The clubhouse display", styles["H2"]))
story.append(Paragraph(
    "For a television in the club bar there is a page at <b>/bar</b> built for the purpose. It needs nothing from "
    "the race office: open it once on the TV, put the browser in full screen, and leave it.", styles["Body"]))
story.append(bullets([
    "It follows the <b>current race</b> and moves on by itself when the club does — create a race or delete "
    "the one it was showing and the TV catches up within a few seconds, with nobody going to the bar to press "
    "anything.",
    "The chart fills the screen and <b>zooms to the boats still racing</b>, so the room sees the leg they are on "
    "rather than a whole course of empty sea.",
    "The leaderboards <b>cycle</b> beside it — order on the water, then IRC, then YTC — each boat with "
    "its <b>speed over the ground</b>, which is the number the bar argues about.",
    "The course is listed in the same <b>red and green</b> as the race sheet, so the room can see which way each "
    "mark is to be left.",
    "Before the start it shows the <b>countdown</b> with the <b>flags currently hoisted</b> beside it, newest "
    "first, right to left — the same flags, drawn the same way, as the flag panel in the race office.",
    "It cuts to the <b>start-hut camera</b> for the start, for each boat rounding the outer mark, and for each "
    "boat finishing, then goes back to the chart.",
]))
story += note_box(
    "There are no controls on the page at all — nothing for a stray remote or a curious member to press. It does "
    "need GPS tracking running to be worth putting up; without it there is nothing to draw.")

story.append(Paragraph("13.6 &nbsp; Replaying a race in the bar", styles["H2"]))

story.append(Paragraph("13.7 &nbsp; A 3D film of the race", styles["H2"]))
story.append(Paragraph(
    "Beside that button on the same <b>Results</b> tab is <b>Render a 3D film</b>. It makes a film of the "
    "race in three dimensions - the fleet sailing the course they actually sailed, over the real coastline, "
    "trimmed to the wind of the moment, with the hut camera cut in at the start and at the finishes and the "
    "club's own branding on it. It is built from the tracks, the course, the wind log and the videos you "
    "already recorded, so there is nothing extra to do on the water.", styles["Body"]))
story.append(bullets([
    "<b>The race-office PC does not make it.</b> It cannot - it is a fanless box that is also running the "
    "race. Pressing the button queues the job, and a separate render machine picks it up and does the work. "
    "Nothing happens at all until that machine is switched on, which is what the dashboard's <b>3D replay</b> "
    "card is there to tell you.",
    "<b>Think this evening, not this minute.</b> An eight-minute film takes between twenty minutes and a "
    "couple of hours depending on the machine. The page shows how far it has got.",
    "<b>Publish the race videos first</b> if you want the hut camera in it. The film is made either way; it "
    "simply has no camera inset for clips that have not been published, and the button says so before you "
    "press it.",
    "<b>Competitors get it by themselves.</b> Once the film exists a link appears on the public races list, "
    "on that race's public page and in the published results - nothing to send round.",
]))
story.append(figure("replay3d_film.png",
                    "A finish, from a film of a club race.", max_h=8 * cm))

story.append(Paragraph(
    "The same television will play a race back for people coming in from sailing. On the race sheet's "
    "<b>Results</b> tab, <b>Replay this race in the bar</b> puts it up: anybody signed in can start one, it "
    "touches no race data, and the TV switches itself over within a few seconds. Nobody goes near the screen.",
    styles["Body"]))
story.append(bullets([
    "It runs from the <b>warning signal to the last finish</b> at six times life, so an afternoon's race fits "
    "into fifteen to twenty-five minutes. Beside the button it says roughly how long, before you start one.",
    "It drops to <b>normal speed whenever a video plays</b> — the start, the finishes, anything else recorded "
    "that day — and the clock follows the picture, so the boats on the chart stay with the boats in the video.",
    "The order on the water, the wind gauge and the flags all show <b>that moment of that race</b>, not now: "
    "the wind is what the station recorded at the time, and the flags are the ones that were flying.",
    "At the last finish it holds the <b>finishing order</b> for a minute and a half, then goes back to the "
    "current race by itself. <b>Stop the replay</b> on the same tab ends it early.",
    "Only one race replays at a time, and choosing another replaces it. A race needs a start time and at least "
    "one boat finished, or there is nothing to replay and the tab says so.",
]))
story += note_box(
    "Clips that would replay footage the one before already showed are skipped, so a fleet finishing close "
    "together does not put the same water up three times. Every clip is still on the race sheet. Clips recorded "
    "before v0.268 can sit a few seconds out against the camera's own burnt-in clock: they are cut from whole "
    "twenty-second segments of the buffer, and where that segment began was not recorded at the time.")

story.append(PageBreak())

# ============================================================== CHAPTER 14 - CHECKLIST
story += chapter_heading("Chapter 14", "Good practice checklist")
story.append(Paragraph("A few habits that make race days run smoothly with the app:", styles["Body"]))
story.append(bullets([
    "Keep the race-office PC's clock accurate (NTP or GPS-disciplined where possible) and keep it visible — "
    "every signal time and finish time depends on it.",
    "Don't let the PC go to sleep during racing; the start sequence and horn scheduler need to keep running.",
    "Treat the flag panel and scheduled signal plan as a prompt, not a record — always confirm the physical "
    "flags on the mast match what the app expects.",
    "Supervise automatic horn and audio output rather than assuming it fired; use “Fire horn now” if in doubt.",
    "Handle protests and redress outside the app, then apply the outcome by editing the affected entry directly.",
    "Back up the <b>data/</b> folder (Backup / restore in the side menu) before using “7. Remove Race” "
    "to delete a race sheet — it cannot be undone.",
    "Glance at the <b>Off-site backup</b> card on the dashboard now and then. It shows how long ago the "
    "nightly encrypted copy last reached Cloudflare R2, and flags it when there has not been one — a backup "
    "that quietly stopped is worse than none, because everyone assumes there is one.",
    "Re-publish (Download HTML, and the Sailwave CSV if you score in Sailwave) after any post-race correction, so the club website and your "
    "records stay in step with the app.",
]))
story.append(Paragraph(
    "For deeper detail on any of these topics — hardware setup, video recording, the weather station, "
    "backups, or troubleshooting — see the full documentation set in the app's <b>docs/</b> folder, "
    "including <b>SERIES_SCORING.md</b>, <b>WEBSITE_PUBLISHING.md</b>, <b>FLAGS_AND_START_SEQUENCE.md</b> and "
    "<b>OPERATIONAL_CHECKLIST.md</b>.", styles["Body"]))

story.append(PageBreak())
story += chapter_heading("Chapter 15", "The Virtual Race Officer")
story.append(Paragraph(
    "Everything above is done on the race sheet, in the hut. It can also be done from a boat. "
    "The <b>Virtual Race Officer</b> at <b>/vro</b> is one page — a box to type in, the conversation, "
    "and a <b>Yes</b> button — "
    "for a race officer with a phone in one hand and a tiller in the other. Type a sentence, read back what "
    "it would do, agree to it. Every command goes through the same code the race sheet uses.", styles["Body"]))
story.append(bullets([
    "<i>“create a race called Sunday Points at 11am, in the summer series”</i> — a race sheet "
    "with its first gun and its series, and a course chosen for the wind at that moment if you agree to it.",
    "<i>“put the start back ten minutes”</i>, <i>“use course 4”</i>.",
    "<i>“add all the boats”</i>, <i>“enter the IRC 1 fleet”</i>, <i>“add "
    "Mojito and Sgrech Bach”</i>, <i>“the same boats as last time”</i>.",
    "<i>“make me a windward-leeward twice round, O to 4”</i> — a made-up course, read back "
    "mark by mark before it is set, and changed the same way.",
    "<i>“how many courses are there?”</i>, <i>“where is mark 4?”</i>, "
    "<i>“what sail at 12 knots?”</i> — a look-up in the club’s own data.",
    "<i>“shorten at mark 4”</i> — two horn blasts and the announcement, after you confirm.",
    "<i>“how are they getting on?”</i>, <i>“what course would you use?”</i>, "
    "<i>“are the trackers working?”</i>",
]))
story += note_box(
    "<b>Nothing happens until you agree to it.</b> The read-back is the only check that catches the mistake "
    "where the app understood you perfectly and it was not what you meant — a race at 11 tomorrow rather "
    "than today, the wrong fleet, the right command aimed at yesterday’s race. A proposal lapses after "
    "five minutes, and saying the same thing twice does it once.")
story.append(Paragraph(
    "It is a permission of its own — <b>VRO</b>, ticked per account in <b>Settings → "
    "Users</b> — and no role grants it, administrators included. It cannot fly AP, signal a recall or "
    "abandon a race; asked, it says so and offers to move the start instead. There is no <i>arm</i> step "
    "to withhold: with start automation on the horn sequence follows the race’s stored warning signal, "
    "so moving that time from the water moves the horn.", styles["Body"]))
story.append(Paragraph(
    "It reads ordinary English, and a <b>model must be configured</b> for it to read anything at all: "
    "with none, the page says the feature is not available and offers no box to type in. The app’s own "
    "small grammar used to answer instead, and six sentence shapes on a page inviting plain English read "
    "as an app that understands nothing. The cost is that the racing goes back to the race sheet when the "
    "hut’s outbound internet is down. Full detail is in <b>VIRTUAL_RACE_OFFICER.md</b>.",
    styles["Body"]))
story += note_box(
    "A race created from this page has <b>Arm GPS auto-finish</b> and <b>Auto-confirm (unmanned)</b> "
    "both on, and says so when it creates it: nobody is in the hut to press <b>Finish</b>, which is the "
    "whole premise. They are the ordinary tick boxes on the <b>Entries &amp; finish times</b> tab and can "
    "be turned off there.")

story.append(Paragraph("A course nobody has chosen", styles["H2"]))
story.append(Paragraph(
    "A new race stores a fixed course number as a fallback, so that the geometry the chart and the leg "
    "analysis work from exists before anybody has chosen anything. It is not a decision, and since v0.276 "
    "nothing shows it as one: until a course is chosen the race sheet and the competitor page both read "
    "<b>Course not set</b>, the course picker reads <b>Course not set</b>, there is no course board, "
    "nothing is drawn on the chart, there is no predicted time, and <b>the spoken VHF course announcement "
    "stays silent</b> — a fleet sent round a course nobody picked is a general recall at best.",
    styles["Body"]))
story.append(Paragraph(
    "Choosing one from the picker and saving is what makes it a decision; so is recommending or building "
    "one. You can still save the tab with the picker left on <b>Course not set</b> — setting the first "
    "warning signal before the course is known is an ordinary thing to do on a race morning — and the race "
    "simply stays without a course.", styles["Body"]))

doc.build(story)
print("PDF written to", OUT_PATH)
