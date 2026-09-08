"""The PDF guides must not quietly fall behind the app.

Three of the four covers sat at "Covers app version 0.197" through twenty-two
releases, describing a race page that no longer existed. Two of them had the
version typed into the builder as a literal; the third had it in a constant that
looked dynamic because the cover used an f-string on it.

They all read `VERSION` now, which is the file the release process bumps.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILDERS = sorted(_ROOT.joinpath("scripts").glob("build_*.py"))
PDF_BUILDERS = [p for p in BUILDERS
                if "Covers app version" in p.read_text(encoding="utf-8")]


def test_the_builders_were_found():
    """A rename should fail here rather than silently stop checking anything."""
    assert len(PDF_BUILDERS) >= 4


@pytest.mark.parametrize("builder", PDF_BUILDERS, ids=lambda p: p.name)
def test_no_builder_hard_codes_a_version(builder):
    src = builder.read_text(encoding="utf-8")
    stray = re.findall(r'Covers app version (\d+\.\d+)', src)
    assert not stray, f"{builder.name} has {stray} typed into it rather than reading VERSION"


@pytest.mark.parametrize("builder", PDF_BUILDERS, ids=lambda p: p.name)
def test_every_builder_reads_the_version_file(builder):
    src = builder.read_text(encoding="utf-8")
    assert '"VERSION"' in src and "APP_VERSION" in src


@pytest.mark.parametrize("builder", PDF_BUILDERS, ids=lambda p: p.name)
def test_no_version_constant_is_left_assigned_a_literal(builder):
    """The relay guide's `APP_VERSION = "0.197"` printed through an f-string, so
    the cover looked as though it were generated."""
    src = builder.read_text(encoding="utf-8")
    assert not re.search(r'^APP_VERSION\s*=\s*[\'"]\d', src, re.M)
