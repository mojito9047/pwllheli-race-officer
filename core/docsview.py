"""Reading the bundled markdown documentation inside the app.

There are twenty-odd ``docs/*.md`` files that describe how everything works, and
until now the only way to read one was to find the file on the PC. The PDFs on the
Documentation page are the polished guides; these are the detail behind them, and
several of them — TROUBLESHOOTING especially — are what you want *while* something
is going wrong, which is exactly when you are not going to go hunting in a folder.

Three things here are deliberate:

* **The list discovers itself.** Files are found by globbing ``docs/`` and titled
  from their own first ``#`` heading, so a new document appears on the page
  without anyone remembering to register it, and a renamed heading follows.
* **A slug never becomes a path.** The slug is looked up in the globbed list and
  the *stored* filename is used. Joining a user-supplied slug onto a directory is
  how you end up serving ``../../runtime/secret_key.txt``.
* **Markdown rendering is optional.** If the ``markdown`` package is not
  installed the text is shown as-is rather than the page failing — the same
  approach core/backup.py takes to pyzipper. Being able to read the words matters
  more than the formatting.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import appstate

try:                                    # optional: rendering is nicer with it
    import markdown as _markdown
except Exception:                       # pragma: no cover - exercised by the fallback test
    _markdown = None                    # type: ignore[assignment]

# Rendered with tables and fenced code because the documentation uses both, and
# sane_lists so the numbered install steps do not renumber themselves.
MARKDOWN_EXTENSIONS = ("tables", "fenced_code", "sane_lists", "toc")

# Documents worth reading that do not live in docs/. Named explicitly rather than
# by widening the glob, so "every file comes from a known list" stays true and no
# slug can reach a parent directory.
#
# The test for whether one belongs here is whether it is **present on a real
# install**: scripts/README.md is good material but scripts/ is excluded from the
# release ZIP, so listing it would give every hut PC a link that 404s.
#
# README.md carries an explicit title because its own heading contains the version
# number, which would rename and re-sort the entry at every release.
EXTRA_DOCUMENTS = (
    {"path": "README.md", "slug": "overview", "title": "Overview and quick start"},
    {"path": "deploy/live_stream/README.md", "slug": "relay-setup"},
    {"path": "deploy/windows/README.md", "slug": "windows-deployment-scripts"},
)


def docs_dir() -> Path:
    """Return the documentation folder, read at call time for tests."""
    return appstate.BASE_DIR / "docs"


def slug_for_filename(filename: str) -> str:
    """Return the URL slug for a documentation file name."""
    return Path(filename).stem.lower().replace("_", "-")


def document_title(path: Path) -> str:
    """Return a document's own first heading, or a tidied file name.

    Taken from the file rather than kept in a list here, so the page cannot drift
    from what the document actually calls itself.
    """
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("# "):
                    return line[2:].strip()
                if line.strip() and not line.startswith(("<!--", "{#")):
                    break
    except OSError:
        pass
    return path.stem.replace("_", " ").capitalize()


def markdown_documents() -> List[Dict[str, Any]]:
    """Return every bundled markdown document, sorted by title.

    docs/*.md is discovered by globbing; the handful outside it are listed in
    EXTRA_DOCUMENTS. Either way each entry stores the relative path it was found
    at, and that stored path is what is ever opened.
    """
    documents: List[Dict[str, Any]] = []
    try:
        paths = sorted(docs_dir().glob("*.md"))
    except OSError:
        paths = []
    for path in paths:
        if path.is_file():
            documents.append({
                "slug": slug_for_filename(path.name),
                "relpath": f"docs/{path.name}",
                "title": document_title(path),
            })
    for extra in EXTRA_DOCUMENTS:
        path = appstate.BASE_DIR / extra["path"]
        if not path.is_file():
            continue        # not in this install, so do not offer a link to it
        documents.append({
            "slug": extra["slug"],
            "relpath": extra["path"],
            "title": extra.get("title") or document_title(path),
        })
    return sorted(documents, key=lambda item: item["title"].lower())


def document_for_slug(slug: str) -> Optional[Dict[str, Any]]:
    """Look a slug up in the discovered list.

    The whole point of the lookup: the filename comes from the glob, never from
    the request, so no slug can address a file outside docs/ however it is spelt.
    """
    wanted = str(slug or "").strip().lower()
    for document in markdown_documents():
        if document["slug"] == wanted:
            return document
    return None


# A link to another document, e.g. [`DATA_AND_BACKUP.md`](DATA_AND_BACKUP.md) or
# docs/TRACKING.md. The documentation cross-references itself heavily, and a
# rendered page whose every internal link 404s is worse than no rendering.
_DOC_LINK = re.compile(r'href="(?:\./)?(?:docs/)?([A-Za-z0-9_\-]+)\.md(#[^"]*)?"')
# And the ones outside docs/, which are written with a path because that is where
# they are: REMOTE_ACCESS_CLOUDFLARE.md links to ../deploy/live_stream/README.md
# twice, and those were the links dead-ending in the viewer.
_PATH_LINK = re.compile(r'href="((?:\.\./|\./)*(?:[A-Za-z0-9_\-]+/)*[A-Za-z0-9_\-]+\.md)(#[^"]*)?"')
_PDF_LINK = re.compile(r'href="(?:\./)?(?:docs/)?([A-Za-z0-9_\-]+)\.pdf"')


def rewrite_internal_links(rendered: str, md_url: str, pdf_urls: Optional[Dict[str, str]] = None) -> str:
    """Point links between documents at the in-app viewer.

    ``md_url`` carries a ``{slug}`` placeholder and ``pdf_urls`` maps a PDF file
    name to its URL, both supplied by the route so this module needs no Flask.
    The PDF guides are addressed by catalogue slug (``relay-guide``) rather than by
    file name, so they cannot be derived here and have to be passed in.
    """
    pdf_urls = pdf_urls or {}
    # Slug by the path an EXTRA_DOCUMENT was found at, so a link written as
    # ../deploy/live_stream/README.md resolves however many ../ it carries.
    by_path = {doc["relpath"]: doc["slug"] for doc in markdown_documents()}

    def path_link(match: "re.Match[str]") -> str:
        target = match.group(1).lstrip("./")
        while target.startswith("../"):
            target = target[3:]
        slug = by_path.get(target)
        if not slug:
            return match.group(0)
        return f'href="{md_url.format(slug=slug)}{match.group(2) or ""}"'

    def md(match: "re.Match[str]") -> str:
        slug = slug_for_filename(match.group(1))
        anchor = match.group(2) or ""
        if not document_for_slug(slug):
            return match.group(0)       # not one of ours; leave it alone
        return f'href="{md_url.format(slug=slug)}{anchor}"'

    def pdf(match: "re.Match[str]") -> str:
        url = pdf_urls.get(f"{match.group(1)}.pdf")
        if not url:
            return match.group(0)
        return f'href="{url}" target="_blank" rel="noopener"'

    # Paths first: the plain-name pattern would match the tail of a path.
    return _PDF_LINK.sub(pdf, _DOC_LINK.sub(md, _PATH_LINK.sub(path_link, rendered)))


_LEADING_H1 = re.compile(r"^\s*<h1[^>]*>.*?</h1>\s*", re.IGNORECASE | re.DOTALL)


def strip_leading_heading(rendered: str) -> str:
    """Drop a document's own opening H1.

    The page already shows the title in its header, and every one of these files
    opens with the same words as a top-level heading, so leaving it in printed the
    title twice.
    """
    return _LEADING_H1.sub("", rendered, count=1)


def render_markdown(text: str) -> str:
    """Render markdown to HTML, or fall back to readable plain text.

    Raw HTML in the source is passed through, which the documentation relies on
    for entities such as ``&mdash;``. That is safe here for one specific reason:
    these files ship with the app and are not user input. It would not be safe for
    anything uploaded.
    """
    if _markdown is None:
        return ('<p class="muted small">The <code>markdown</code> package is not installed, '
                'so this is the raw file. Run <code>pip install -r requirements.txt</code> '
                'for formatted documentation.</p>'
                f"<pre class=\"doc-raw\">{html.escape(text)}</pre>")
    return _markdown.markdown(text, extensions=list(MARKDOWN_EXTENSIONS), output_format="html")


def rendering_available() -> bool:
    """Return whether formatted rendering is available in this install."""
    return _markdown is not None


def read_document(slug: str) -> Optional[Dict[str, Any]]:
    """Return one document's title and rendered HTML, or None if unknown."""
    document = document_for_slug(slug)
    if not document:
        return None
    path = appstate.BASE_DIR / document["relpath"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return {**document, "html": strip_leading_heading(render_markdown(text)), "text": text}
