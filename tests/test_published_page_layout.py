"""The published results document on a desktop, a tablet and a phone.

Reported from a phone: the results table cut off mid-column, the banner filling
a third of the screen before anything worth reading, and a footer telling the
reader to save the file and upload it to the club website — which they are, in
all likelihood, already looking at.

Measured at 1440, 820 and 390, before:

    desktop  overflow 0    banner 119px (13%)
    tablet   overflow 62   banner 119px (10%)
    phone    overflow 438  banner 249px (30%)   first table below the fold

The phone number is the reported fault. ``.racetable`` is 804px wide — as wide
as its columns need, and none of them can go without dropping something a
competitor came to read — and it had no scroll container, so it took the whole
document sideways with it: headings, banner and all. A table that scrolls is
normal; a page that scrolls is broken.

The 62px on the tablet was a different thing wearing the same clothes. Not a
table at all: the sponsor strip only got ``flex-wrap`` below 700px, so between
700 and about 1100 the last logo simply hung over the edge.

After: **zero horizontal overflow at all three widths.**
"""
from __future__ import annotations

import pathlib

TPL = (pathlib.Path(__file__).resolve().parent.parent
       / "templates" / "series_publish.html").read_text(encoding="utf-8")


class TestTablesScrollNotThePage:
    def test_there_is_a_scroll_wrapper(self):
        assert ".tablewrap" in TPL
        assert "overflow-x: auto" in TPL.split(".tablewrap {")[1].split("}")[0]

    def test_the_series_table_is_wrapped(self):
        head = TPL.split('<table class="summarytable">')[0]
        assert head.rstrip().endswith('<div class="tablewrap">'), head[-120:]

    def test_the_race_table_is_wrapped(self):
        head = TPL.split('<table class="racetable">')[0]
        assert head.rstrip().endswith('<div class="tablewrap">'), head[-120:]

    def test_both_wrappers_are_closed(self):
        """An unclosed one would put the rest of the document inside a scroll
        box, which is a worse fault than the one being fixed."""
        assert TPL.count('<div class="tablewrap">') == 2
        assert TPL.count("</table>\n        </div>") + TPL.count("</table>\n      </div>") == 2

    def test_a_cut_off_table_says_it_can_be_pushed(self):
        """Without a cue, a table clipped mid-column reads as a broken page --
        which is how this was reported. Shadows on whichever edge has more
        table beyond it, in CSS, with nothing to keep in sync."""
        block = TPL.split(".tablewrap, .toolbar {")[1].split("}")[0]
        assert "radial-gradient" in block
        assert "local" in block and "scroll" in block


class TestTheBannerLeavesRoomForTheResults:
    """Wrapping everywhere cured the overflow and caused a second complaint.

    Eight sponsor logos at 160px need 1228px with their gaps, and the banner
    was held to the same 1200px as the tables -- so the last one sat on a line
    of its own and **widening the browser changed nothing**, because the cap
    was the limit rather than the screen.

    Measured again after, at six widths, with eight logos:

        1800  one line, 160px logos      0 overflow
        1440  one line, 160px logos      0 overflow
        1100  one line, 118px logos      0 overflow
         820  two lines, 160px logos     0 overflow
         390  two lines, phone sizes     0 overflow

    One line where there is room for one, and the tablet overflow stays fixed
    -- by shrinking rather than by wrapping, which is what keeps the logos at
    full size on the monitor the results are actually read on.
    """

    def test_the_strip_is_one_line_where_there_is_room(self):
        base = TPL.split(".publish-sponsor-strip {")[1].split("}")[0]
        assert "flex-wrap: nowrap" in base

    def test_the_banner_is_not_held_to_the_width_of_the_tables(self):
        """The actual fault: a wider window has to buy something."""
        branding = TPL.split(".publish-branding {")[1].split("}")[0]
        assert "max-width: 1200px" not in branding,             "the banner is capped at the tables' width again, so widening does nothing"

    def test_a_logo_shrinks_instead_of_hanging_over_the_edge(self):
        """What stops the 62px tablet overflow coming back. An image is a flex
        item with an automatic minimum of its own width, so min-width: 0 is the
        whole trick."""
        logo = TPL.split(".publish-sponsor-logo {")[1].split("}")[0]
        assert "min-width: 0" in logo
        assert "flex: 0 1 auto" in logo

    def test_it_wraps_where_one_line_would_be_too_small_to_read(self):
        """Eight logos on one line at 820px come out 23px tall."""
        mid = TPL.split("@media (max-width: 1000px) {")[1].split("@media")[0]
        assert "flex-wrap: wrap" in mid

    def test_a_phone_gets_a_smaller_banner(self):
        phone = TPL.split("@media (max-width: 700px) {")[1].split("@media")[0]
        assert "max-height: 32px" in phone       # sponsor logos
        assert "max-height: 52px" in phone       # club logo

    def test_and_less_body_padding(self):
        phone = TPL.split("@media (max-width: 700px) {")[1].split("@media")[0]
        assert "body { padding: 12px; }" in phone


class TestTheFooter:
    @staticmethod
    def _note():
        """The footer as it renders. Asserting against the whole template would
        match the Jinja comment that explains why the old wording went."""
        return TPL.split('<p class="print-note">')[1].split("</p>")[0]

    def test_it_no_longer_tells_the_reader_to_upload_the_file(self):
        """The app publishes it now, and most people reading it are on the club
        website, where being told to upload it there is a puzzle."""
        note = self._note()
        assert "Save this HTML file" not in note
        assert "upload it to the club website" not in note

    def test_it_still_says_where_it_came_from_and_that_it_is_provisional(self):
        note = self._note()
        assert "Generated by Pwllheli Race Officer" in note
        assert "provisional" in note


class TestTheRaceLinksAreButtonsOnOneRow:
    """Reported: the two things most people open the page for looked like small
    print. The film and the start video were separate stacked rows of ordinary
    blue text, each behind a bold label -- so a reader scanning for the replay
    found a caption first and a link second, twice over.

    One row now, and buttons, so they read as the two things you can press.
    """

    def test_the_film_and_the_start_videos_share_one_block(self):
        assert '<div class="race-links">' in TPL
        assert TPL.count('<div class="race-links">') == 1, \
            "two blocks means two rows again"

    def test_that_block_lays_them_out_in_a_row(self):
        row = TPL.split(".race-links {")[1].split("}")[0]
        assert "display: flex" in row
        assert "flex-wrap: wrap" in row, "they must still stack rather than overflow a phone"

    def test_they_are_buttons_rather_than_text_links(self):
        btn = TPL.split(".racebtn {")[1].split("}")[0]
        assert "background:" in btn and "padding:" in btn
        assert "text-decoration: none" in btn

    def test_the_buttons_keep_the_club_s_square_corners(self):
        """No radius anywhere else in this document, and none here."""
        btn = TPL.split(".racebtn {")[1].split("}")[0]
        assert "border-radius" not in btn

    def test_the_film_button_says_what_it_opens(self):
        assert ">Watch the 3D replay<" in TPL

    def test_a_missing_start_video_still_says_so(self):
        """It said "No Video" before and the sheet should not go quiet."""
        assert "no video" in TPL

    def test_printing_does_not_put_a_slab_of_ink_on_every_race(self):
        block = TPL.split("@media print {")[1]
        assert ".racebtn {" in block
        assert "background: #fff" in block.split(".racebtn {")[1].split("}")[0]
