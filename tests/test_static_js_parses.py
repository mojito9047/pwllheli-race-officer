"""Every file in static/ has to be something a browser will actually parse.

This exists because of one character. In marks_ping.js an error message read

    'it at the club's https address instead.'

and the apostrophe in "club's" closed the string. A browser reports that as
`SyntaxError: missing ) after argument list`, discards the *entire file*, and
then does nothing else — no handlers bound, no messages, no clue. The phone page
sat on "waiting for GPS…" forever and looked like a broken GPS rather than a
broken script.

The whole suite passed while that file was live. Nothing else here loads the
JavaScript, so nothing else can notice: the templates are rendered and asserted
on as text, and a `<script src=...>` that 404s or fails to compile renders the
same either way.

So: hand each file to a real JavaScript engine and see whether it compiles.
`new Function(src)` compiles without running, which is what is wanted — these
files touch `document` on load and there is nothing to touch here.

It only checks that the file parses. A file can parse and still be wrong; that is
what the browser tests of individual pages are for.
"""
from __future__ import annotations

import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
JS_FILES = sorted((_ROOT / "static").glob("*.js"))


@pytest.fixture(scope="module")
def compile_js():
    """Yields a callable that compiles a source string, or skips the module.

    One browser for all the files rather than one each — launching is the slow
    part, compiling is not.
    """
    sync_playwright = pytest.importorskip(
        "playwright.sync_api",
        reason="playwright is not installed here",
    ).sync_playwright

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:                      # no browser downloaded
                pytest.skip(f"no chromium available: {exc}")
            page = browser.new_page()

            def compile_source(source: str) -> str | None:
                """Returns the error message, or None if it compiled."""
                return page.evaluate(
                    "src => { try { new Function(src); return null; }"
                    "        catch (e) { return String(e); } }",
                    source,
                )

            try:
                yield compile_source
            finally:
                browser.close()
    except pytest.skip.Exception:
        raise


def test_there_are_files_to_check():
    """Guards against this whole module quietly checking nothing."""
    assert len(JS_FILES) >= 8, [f.name for f in JS_FILES]


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.name)
def test_it_compiles(path, compile_js):
    error = compile_js(path.read_text(encoding="utf-8"))
    assert error is None, f"{path.name} is not valid JavaScript: {error}"
