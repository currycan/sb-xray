"""Tests for sb_xray.routing.service_spec."""

from __future__ import annotations

import re

from sb_xray.routing.service_spec import SPECS_BY_ENV, ContentSignature


def test_compiled_url_patterns_are_compiled() -> None:
    sig = ContentSignature(blocked_url_patterns=(r"/sorry/", r"consent\.youtube\.com"))
    compiled = sig.compiled_url_patterns
    assert all(isinstance(p, re.Pattern) for p in compiled)
    assert [p.pattern for p in compiled] == [r"/sorry/", r"consent\.youtube\.com"]


def test_compiled_url_patterns_empty_when_none() -> None:
    sig = ContentSignature(real_substrings=("x",))
    assert sig.compiled_url_patterns == ()


def test_compiled_url_patterns_match_youtube_sorry() -> None:
    sig = SPECS_BY_ENV["YOUTUBE_OUT"].signature
    assert sig is not None
    assert any(p.search("https://www.youtube.com/sorry/abc") for p in sig.compiled_url_patterns)
