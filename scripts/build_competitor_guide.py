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

SCREEN_DIR = os.path.join(_HERE, "competitor_screens")
LOGO_PATH = os.path.join(_REPO, "static", "img", "pwllheli_sailing_club_logo.png")
OUT_PATH = os.path.join(_REPO, "docs", "Pwllheli_Competitor_Guide.pdf")

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


def fit_image(filename, max_w=CONTENT_W, max_h=13.0 * cm):
    path = os.path.join(SCREEN_DIR, filename)
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


def phone_figures(pairs, max_h=17.5 * cm):
    """Lay out one or two phone-shaped screenshots side by side with captions."""
    col_w = (CONTENT_W - 1 * cm) / len(pairs)
    cells = []
    for filename, caption_text in pairs:
        img = fit_image(filename, max_w=col_w, max_h=max_h)
        cap = Paragraph(caption_text, styles["Caption"])
        cells.append([img, cap])
    row = list(zip(*cells)) if len(pairs) > 1 else [[cells[0][0]], [cells[0][1]]]
    t = Table(row, colWidths=[col_w] * len(pairs))
    t.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


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


def on_page(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, MARGIN - 10, PAGE_W - MARGIN, MARGIN - 10)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, MARGIN - 22, "Pwllheli Race Officer - Competitor's Guide")
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
                       title="Pwllheli Race Officer - Competitor's Guide",
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
        [Paragraph("Competitor's Guide", ParagraphStyle("t2", parent=styles["CoverTitle"], fontSize=22, leading=26))],
        [Spacer(1, 0.5 * cm)],
        [Paragraph("What to expect on the public race-day pages", styles["CoverSubtitle"])],
        [Spacer(1, 5.7 * cm)],
        [Paragraph("No login needed - for competitors, crews and spectators", styles["CoverMeta"])],
        [Paragraph(f"Covers app version {APP_VERSION}", styles["CoverMeta"])],
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
    "Pwllheli Race Officer publishes live, read-only race information for competitors, crews and spectators "
    "on race day - no login, account or app install needed. This guide shows what you'll see on those pages "
    "and what each part means, so you know where to look before you leave the dock and while you're waiting "
    "to start.", styles["Body"]))
story.append(Paragraph("There are two pages you'll use:", styles["Body"]))
story.append(bullets([
    "The <b>competitor home page</b> - a starting point showing the current series, its races, and the "
    "start-hut wind.",
    "The <b>race page</b> - one specific race's start sequence, course and, once finished, its results.",
]))
story += note_box(
    "These pages are provisional information for competitors. The race officer's decisions and the results "
    "published after a race remain the authoritative record - if anything here looks wrong or conflicts with "
    "an announcement on the water, always follow the race officer.")
story.append(Paragraph(
    "Both pages work the same way on a phone as on a laptop - screenshots of both are shown throughout this "
    "guide. There is one address for every device: on a phone the wide tables (entries, results, leg analysis, "
    "pursuit start times) are shown as <b>one card per boat</b> with the column heading beside each value, so "
    "nothing is cut off and the page never scrolls sideways.", styles["Body"]))
story.append(Paragraph(
    "Turn the phone sideways and it is wide enough for the ordinary tables, the same as a tablet. Where a table "
    "is still too wide for the screen - the results are nine columns - <b>the table scrolls sideways inside its "
    "own panel</b> and the rest of the page stays where it is, so you can drag the columns across without losing "
    "your place. Before v0.257 the right-hand end was simply cut off, taking the <b>Finish video</b> link with "
    "it.", styles["Body"]))

story.append(PageBreak())

# ============================================================== CHAPTER 1 - FINDING THE PAGES
story += chapter_heading("Chapter 1", "Finding the pages")
story.append(Paragraph(
    "Your club will share an address for the competitor pages - typically the club's normal website address, "
    "or one given out at registration/briefing. Opening it takes you straight to the competitor home page; "
    "no separate app or login is involved.", styles["Body"]))
story.append(Paragraph(
    "The race officer may also share a direct link to a specific race - for example when a QR code or link is "
    "put up on the club noticeboard for \"today's race\". Direct race links go straight to that race's page, "
    "skipping the home page.", styles["Body"]))
story += note_box(
    "There's nothing to configure and nothing you can accidentally change - these pages are read-only. Feel "
    "free to leave one open on your phone throughout race day.")

story.append(PageBreak())

# ============================================================== CHAPTER 2 - HOME PAGE
story += chapter_heading("Chapter 2", "The competitor home page")
story.append(Paragraph(
    "This is the page you land on first. It always shows the club's current racing at a glance:", styles["Body"]))
story.append(bullets([
    "<b>Race information</b> - the current series (if any), the current race's name and first start time, and "
    "a <b>Go to current race</b> button. That button opens a page which <i>keeps following</i> the current "
    "race: sail your race, finish, and when the race officer starts the next one the page moves on to it by "
    "itself, with no need to come back here. It says so at the top, and offers a link to stay on one race if "
    "you would rather. Opening a race from the Races tab pins you to that race instead, which is what you "
    "want for a race already sailed.",
    "<b>Wind dial and countdown</b> - at the top of the page, beside the race details: an analog dial showing "
    "the wind direction and speed at the start hut (the same instrument the race officer uses), and a countdown "
    "to the current race's first start. Both update themselves, so the page can be left open on a phone.",
    "<b>Races tab</b> - every race this year, grouped into its series. Each series is a folded-up section you "
    "can tap to open; the <b>current series is already open</b> and the <b>current race is highlighted</b>. Each "
    "row gives the first start, status, number of entries and an <b>Open</b> link to that race's page.",
    "<b>Published results</b> - where the race office has published a series' standings, the series shows a "
    "link to them. It is the same document the club website carries, and it is updated each time the race "
    "office publishes, so it is the one to check for the scores so far rather than the live race pages.",
    "<b>Wind tab</b> - the latest wind direction, speed and gust measured at the start hut, plus a tall "
    "wind-history plot covering your choice of the last 5 to 60 minutes. This is the wind at the hut, which can "
    "differ from the wind out on the racecourse.",
    "<b>Live camera tab</b> - a view from the start hut, if your club has one configured. It starts as soon "
    "as you open the tab and stops when you leave it, so it only uses data while you are watching. You will see "
    "still pictures for the first few seconds - the camera stream only runs while somebody is watching, so it "
    "takes a moment to start - and then it changes by itself to <b>live video</b>, which fills the width of the "
    "screen on a phone. On a race page a <b>Watch live</b> link in the footer opens the same view in its own "
    "tab; if you don't see it, the club isn't streaming live right now.",
]))
story.append(figure("01_landing_desktop.png",
                     "The competitor home page on the Races tab: this year's racing, with the current series "
                     "open and the current race highlighted."))
story.append(PageBreak())
story.append(figure("01b_landing_wind_desktop.png",
                     "The Wind tab: hut wind now, and the history plot behind it.", max_h=14 * cm))
story.append(PageBreak())
story.append(phone_figures([("02_landing_mobile.png", "The home page on a phone.")], max_h=21 * cm))

story.append(PageBreak())

# ============================================================== CHAPTER 3 - BEFORE THE START
story += chapter_heading("Chapter 3", "The race page: before the start")
story.append(Paragraph(
    "Opening a race - either from the home page or a direct link - shows that race's own page. Before "
    "racing starts, you'll see the start sequence in progress:", styles["Body"]))
story.append(bullets([
    "<b>Race name and summary</b> - first warning-signal time, first start time, course number and "
    "series.",
    "<b>Flag panel</b> - shows only the flags currently flying at the start line: your class's numeral pennant "
    "once the warning signal has gone up, and the blue-and-white <b>P</b> (preparatory) flag once that follows. "
    "An empty panel reading <b>No flags up</b> means the sequence hasn't started yet, or has finished.",
    "<b>Race status clock</b> - a countdown to the start (shown as a negative time, e.g. <b>-02:35</b>) once "
    "the sequence has begun.",
    "<b>Entries tab</b> - the list of boats in the race, with each boat's name, sail number, class, IRC and YTC "
    "rating and current status. It's a quick way to check you're entered and that your rating looks right.",
    "<b>Chart tab</b> - the course chart with the marks, the sailing direction and the wind arrow.",
    "<b>Course analysis tab</b> - a predicted leg-by-leg timing table (distance, bearing, suggested sail and "
    "target speed) calculated from the current wind.",
]))
story += note_box(
    "The page is split into tabs below the race header — <b>Entries</b> (or <b>Start times</b> for a "
    "pursuit race), <b>Chart</b> and <b>Course analysis</b>, joined by <b>Leader board</b> once the race has "
    "finished. Tap a tab to switch; the race name, times, flags and countdown stay visible above them. On a "
    "phone each table is shown as one card per boat, so nothing is cut off.")
story += note_box(
    "The flag panel mirrors what the race officer's system is displaying - it is a helpful reference, not a "
    "replacement for watching the actual flags at the start line. If your class isn't shown, it hasn't been "
    "signalled to start yet.")
story.append(Paragraph(
    "<b>If the course is shortened</b> (for example when the wind drops), the race page shows a <b>Shortened "
    "course</b> banner naming the mark to finish at, the flag panel flies International Code flag <b>S</b> (a "
    "blue square on a white background), and the course chart is redrawn so it ends at that mark with a dashed "
    "line straight to the finish. Round the named mark and then head directly for the finish line - listen for "
    "the two horn blasts and the spoken announcement on the water as well.", styles["Body"]))
story.append(figure("03_race_live_desktop.png",
                     "A race page during the start sequence: class flag and preparatory flag up, "
                     "countdown running, and the course chart with predicted leg timing."))
story.append(PageBreak())
story.append(phone_figures([
    ("04_race_live_mobile.png", "The same race page on a phone: the Entries tab, one card per boat."),
    ("04b_race_chart_mobile.png", "The Chart tab on the same phone."),
], max_h=19 * cm))

story.append(PageBreak())

# ============================================================== CHAPTER 4 - AFTER RACING
story += chapter_heading("Chapter 4", "The race page: results")
story.append(Paragraph(
    "Once every entered boat has finished, retired or otherwise stopped racing, a <b>Leader board</b> tab "
    "appears and opens first. The race status clock changes to <b>Finished</b>, and the leader board holds a "
    "results table for each configured class:", styles["Body"]))
story.append(bullets([
    "<b>Rank</b> - the boat's placing in that table once corrected; a dash means it didn't finish.",
    "<b>Elapsed / Corrected</b> - the boat's raw sailing time, and its time after the class's IRC or YTC "
    "handicap has been applied. Rankings are always by corrected time.",
    "<b>Status</b> - shows a code instead of a time for anything other than a normal finish (see the table "
    "below).",
    "<b>Finish video</b> - a link to a video clip of the finish, where the club records and publishes one.",
]))
story.append(Paragraph(
    "The <b>Entries</b>, <b>Chart</b> and <b>Course analysis</b> tabs stay where they are after the race, and "
    "the last two become a <b>record of the conditions</b>: instead of the wind right now, they show the "
    "<b>average wind over the race</b>, measured at the start hut between the warning signal and the last boat "
    "finishing. The wind chips change to <b>Average TWD</b>, <b>Average TWS</b> (with the range it varied over) "
    "and <b>Peak gust</b>, and the chart's wind arrow points the average way. It is a useful thing to look back "
    "at when comparing your race with a result sheet weeks later.", styles["Body"]))
story.append(Paragraph("Status codes you may see in the Status column:", styles["Body"]))

status_rows = [
    ["PRESTART", "Entered and waiting for the warning signal"],
    ["RACING", "Started and still racing"],
    ["FINISHED", "Crossed the finish line"],
    ["DNC", "Did not compete - did not come to the start"],
    ["DNS", "Did not start"],
    ["OCS", "On course side - over the line early at the start"],
    ["RET", "Retired during the race"],
    ["DNF", "Did not finish"],
    ["DSQ", "Disqualified"],
]
status_intro = [Spacer(1, 2)]
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
    "Results shown here are provisional. Final, official results for a series are published separately by the "
    "race officer, typically on the club website.")

story.append(PageBreak())
story.append(figure("crop_results_top.png", "Race results (1 of 2): race status and the first results table.",
                     max_h=16 * cm))
story.append(figure("crop_results_bottom.png", "Race results (2 of 2): remaining classes and any boats excluded "
                                                "from a table for missing a rating.", max_h=16 * cm))

story.append(PageBreak())
story.append(Paragraph("On a phone, the same results are shown in the same order:", styles["Body"]))
story.append(phone_figures([("06_race_results_mobile.png", "Results on a phone. Each boat is shown as its own "
                                                             "card with the column heading beside every value, "
                                                             "so nothing is cut off.")], max_h=20 * cm))

story.append(PageBreak())

# ============================================================== CHAPTER 5 - PURSUIT RACES
story += chapter_heading("Chapter 5", "Pursuit races")
story.append(Paragraph(
    "Some events are run as a <b>pursuit race</b>. Instead of everyone starting together and results being "
    "worked out on handicap afterwards, the slower boats start first and the faster boats later, so if "
    "everyone sails to their handicap they would all finish at about the same time. The first boat across the "
    "line at the end wins.", styles["Body"]))
story.append(Paragraph(
    "On the race page you'll see a <b>Start times</b> list instead of the usual course analysis:", styles["Body"]))
story.append(bullets([
    "Every boat is listed with its own <b>start time</b>, earliest first.",
    "The <b>next boat due to start is highlighted</b>, and each row counts down to its start — so you can see "
    "exactly when it's your turn.",
    "The single <b>finish-signal</b> time is shown below the list.",
    "After the finish, the page shows the <b>finishing order</b> (the positions the race officer recorded from "
    "the boats' order on the water) rather than a corrected-time table.",
]))
story.append(figure("pursuit_public.png", "A pursuit race on the competitor page: each boat's start time, with "
                                          "the next boat to start highlighted.", max_h=15 * cm))

story.append(PageBreak())

# ============================================================== CHAPTER 6 - TRACKING
story += chapter_heading("Chapter 6", "Following the fleet (GPS tracking)")
story.append(Paragraph(
    "When the club is running GPS trackers on the boats, the race page's <b>Chart</b> tab shows where everyone "
    "is. You don't need to do anything to switch it on — if tracking is running for that race, it simply "
    "appears.", styles["Body"]))
story.append(bullets([
    "Each tracked boat is drawn as a <b>hull seen from above</b>, turned the way it is actually heading, in its "
    "own colour, with its sail number beside it and a trail of where it has been for the last ten minutes.",
    "The chart <b>opens at the latest positions</b> — during a race that is simply the live view. Drag the "
    "timeline underneath it, or press play, to wind back through the race so far and watch it again at up to "
    "60x. <b>Live</b> returns you to the front.",
    "<b>Full screen</b> gives the chart your whole display, which is worth doing on a phone.",
    "The <b>Entries</b> table also gains columns for each boat: <b>Marks</b> (how many of the course marks it "
    "has rounded, e.g. 6/10), <b>Next</b> (the mark it is heading for), <b>To go</b> (how far it still has to "
    "sail), <b>SOG</b> (its speed over the ground) and <b>Fix</b> (how long ago its tracker last reported).",
    "A boat the app cannot place still appears in the list, with dashes across the row and the reason in the "
    "<b>Fix</b> column: <b>No tracker</b> if there is no tracker on the boat, or <b>Not reporting</b> if there "
    "is one and nothing has been heard from it in the last hour.",
    "It all keeps up by itself — just leave the page open.",
]))
story.append(figure("04b_race_chart_mobile.png",
                     "The Chart tab on a phone: the fleet on the course, the timeline to wind back through the "
                     "race, and the Leaderboard tab that rolls up over the chart.", max_h=17 * cm))
story.append(PageBreak())

story.append(Paragraph("<b>Who is actually winning</b>", styles["H2"]))
story.append(Paragraph(
    "Underneath the chart is a <b>Leaderboard</b> tab. Tap it and a panel rolls up over the chart; tap it again "
    "and it rolls away. It offers more than the order on the water:", styles["Body"]))
story.append(bullets([
    "<b>Line honours</b> — the order on the water, by how far round the course each boat is.",
    "<b>IRC corrected</b> and <b>YTC corrected</b> — the order on handicap, overall or for one class. Only the "
    "systems your fleet is actually rated in are offered.",
]))
story.append(Paragraph(
    "For a boat that has already finished, the corrected board uses its real elapsed time. For one still racing "
    "the app has to <b>estimate</b> when it will finish, and you can choose how:", styles["Body"]))
story.append(bullets([
    "<b>Average pace since the start</b> (the one it opens on) — how much of the course the boat has covered, "
    "and how long that has taken. Nothing else comes into it: no wind, no polar.",
    "<b>Recent pace</b> — how fast the boat has actually been closing on the finish over the last twenty "
    "minutes, carried over the distance it has left. This is the one that notices a boat parking, and the one "
    "that jumps about most.",
    "<b>Polar pace factor</b> — how the boat's time so far compares with the target time for the legs it has "
    "sailed, applied to the legs it has not. The only one that knows the legs ahead are a different point of "
    "sail from the legs behind. It needs to know the wind, and the only wind the club measures is at the hut.",
]))
story.append(Paragraph(
    "Whichever you pick drives the <b>order and the times printed beside it</b>, so the board cannot contradict "
    "itself as you read down it.", styles["Body"]))
story += note_box(
    "The simplest method is the default, and not because it is the most accurate - it is not. Its error is "
    "much the same for every boat at any moment, because the whole fleet has just sailed the same slow beat or "
    "the same fast reach. A leaderboard ranks boats against each other, so an error they all share cancels out, "
    "while a per-boat one shuffles the order. Over the night race of 8 August it had the finishing order right "
    "for 99% of the race against 63-83% for the others, despite being further out on each individual boat.")
story += note_box(
    "Nothing is estimated for the first ten minutes of racing — before the first mark a boat's pace is mostly "
    "which end of the line it started. After that the estimate settles quickly, but it remains an estimate and "
    "is labelled as one on the page. It is not a result.")
story.append(Paragraph(
    "<b>The wind gauge on the chart</b> shows the wind at the moment you are looking at, not the wind "
    "now: wind the timeline back to the start and the gauge winds back with it. Direction and speed are "
    "the two readings on the dial, and a gust shows as a marker further round the speed scale when it is "
    "actually gusting. It is the wind measured <b>at the hut</b>, which is not the wind where the boats "
    "are once a course goes round a headland - the gauge says so underneath itself. If nothing was "
    "recorded near the moment on screen the gauge simply disappears, rather than showing you an old "
    "reading as though it were current.", styles["Body"]))
story.append(Paragraph(
    "<b>Boats are compared as at the same moment.</b> The trackers do not all report together - one boat's may "
    "speak every two seconds and another's once a minute - so putting each boat's last known position against "
    "the others compares them at different times. At 6 knots, two minutes of that is 400 metres, which is "
    "enough to put the order the wrong way round, and it is why the board could be seen swapping boats about "
    "while nothing on the water was changing. Each boat is now carried forward from its last report on its last "
    "known course and speed to the moment on screen. A boat not heard from for three minutes is left where it "
    "last actually was, and shown as stale.", styles["Body"]))
story.append(Paragraph(
    "Two other things to keep in mind. The chart shows the order <b>on the water</b>, which is not the result: "
    "on handicap a boat behind on the water may still beat one ahead of it — which is exactly what the corrected "
    "boards are for. And trackers report over the mobile network, so a boat can drop out for a while if it loses "
    "signal; the <b>Fix</b> column tells you how fresh each position is.", styles["Body"]))

story.append(PageBreak())

# ============================================================== CHAPTER 7 - GOOD TO KNOW
story += chapter_heading("Chapter 7", "Good to know")
story.append(bullets([
    "<b>Everything updates automatically.</b> Leave the page open and it will refresh itself - there's no "
    "reload button you need to press.",
    "<b>Hut wind, not course wind.</b> The wind reading and history come from a sensor at the start hut, and "
    "can differ from conditions out on the course.",
    "<b>Nothing here needs a login.</b> If a page asks you to sign in, you've reached the race-office side of "
    "the app by mistake - go back and use the competitor link instead.",
    "<b>Results are provisional until published.</b> The figures on the race page are live working numbers. "
    "Your club's official series results are published separately, usually on the club website, after the "
    "race officer has checked them.",
    "<b>Live camera only runs while you watch it.</b> The start-hut view starts when you open its tab and "
    "stops when you leave, so the page stays light on data the rest of the time.",
    "<b>There may be a screen in the club.</b> Some clubs put this same race up on a television in the "
    "bar - the chart following the boats still racing, the leaderboards in turn, and the start-hut camera "
    "at the start, at each rounding of the outer mark and as each boat finishes.",
    "<b>“Course not set yet” means exactly that.</b> A race sheet carries a course number from the "
    "moment it is created, purely so the app has some geometry to work from - which is not the same as the "
    "race officer having chosen one. Until they do, the page says so, and shows no course board and no "
    "course on the chart, rather than naming a course you might go and sail.",
]))
story.append(Paragraph(
    "If something looks wrong - a missing entry, a rating that looks off, or a result you want to query - "
    "raise it with the race officer rather than the page itself; these pages simply display what has been "
    "recorded in the race-office system.", styles["Body"]))

doc.build(story)
print("PDF written to", OUT_PATH)
