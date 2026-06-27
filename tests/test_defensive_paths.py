"""D5: 对 shoutrrr / display / speed_test 防御分支喂畸形输入,断言安全默认。

Brief deviation (verified): The brief listed ",,,", " , \n ," as inputs expected
to yield [] from _parse_urls. However _parse_urls splits on ";" (not ","), so
these comma-only strings are non-blank after strip and are returned as a
single-element list. Replaced with ";;;" and " ; \n ;" which are genuinely
all-blank after semicolon-splitting and correctly yield []. Every assertion
below was verified against the actual source before committing.
"""

from __future__ import annotations

import pytest
from sb_xray import display, shoutrrr
from sb_xray.speed_test import rate

# ---------------------------------------------------------------------------
# shoutrrr._fmt_mbps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, "abc", True, False, [], {}, object()])
def test_fmt_mbps_returns_none_on_non_number(bad: object) -> None:
    # bool is an int subclass; implementation explicitly short-circuits it.
    assert shoutrrr._fmt_mbps(bad) is None


def test_fmt_mbps_formats_real_number() -> None:
    # Positive anchor: ensures the None results above aren't from a stub.
    assert shoutrrr._fmt_mbps(123.0) == "123"


# ---------------------------------------------------------------------------
# shoutrrr._fmt_pct
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, "x", True, [], {}])
def test_fmt_pct_returns_none_on_non_number(bad: object) -> None:
    assert shoutrrr._fmt_pct(bad) is None


# ---------------------------------------------------------------------------
# shoutrrr._rating_line
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, "x", True, [], {}])
def test_rating_line_returns_none_on_non_number(bad: object) -> None:
    assert shoutrrr._rating_line(bad) is None


# ---------------------------------------------------------------------------
# shoutrrr._parse_urls
# Brief had ",,,", " , \n ," — those are NOT blank under ";" split and return
# a one-element list. Replaced with ";;;" and " ; \n ;" which ARE all-blank
# after ";" split+strip. Verified by running _parse_urls() against each input.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", "   ", ";;;", " ; \n ;"])
def test_parse_urls_empty_or_blank_returns_empty_list(raw: str | None) -> None:
    assert shoutrrr._parse_urls(raw) == []


# ---------------------------------------------------------------------------
# display.flag_from_iso
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "U", "USA", "1S", "中国", "u s", "U8"])
def test_flag_from_iso_rejects_non_two_ascii_letters(bad: str) -> None:
    assert display.flag_from_iso(bad) == ""


def test_flag_from_iso_maps_valid_code() -> None:
    assert display.flag_from_iso("US") == "\U0001f1fa\U0001f1f8"  # 🇺🇸


# ---------------------------------------------------------------------------
# display.get_flag_emoji
# ---------------------------------------------------------------------------


def test_get_flag_emoji_returns_empty_on_no_match() -> None:
    assert display.get_flag_emoji("no-such-region-token-xyz") == ""


# ---------------------------------------------------------------------------
# speed_test.rate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mbps,expected",
    [(-1.0, "slow"), (0.0, "slow"), (float("nan"), "slow")],
)
def test_rate_safe_default_on_low_or_nan(mbps: float, expected: str) -> None:
    # NaN: all > comparisons evaluate False → falls through to "slow".
    # -1.0 / 0.0: below every threshold → "slow".
    assert rate(mbps) == expected
