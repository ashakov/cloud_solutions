"""Unit tests for rewritten Airbnb SSR parser — no network or Playwright needed."""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import base64
from parsers.airbnb import (
    _decode_listing_id,
    _parse_price_label,
    _parse_rating,
    _parse_bedrooms,
    _find_card_nodes,
    _parse_card,
    _location_to_district,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_id(numeric: str) -> str:
    """Encode numeric ID the way Airbnb does: base64('DemandStayListing:{id}')."""
    raw = f"DemandStayListing:{numeric}"
    return base64.b64encode(raw.encode()).decode().rstrip("=")


def _make_card(
    listing_id: str,
    title: str,
    price_label: str,
    rating_str: str | None = None,
    bedrooms_body: str | None = None,
    subtitle: str | None = None,
) -> dict:
    card: dict = {
        "demandStayListing": {"id": _make_id(listing_id)},
        "title": title,
        "structuredDisplayPrice": {
            "primaryLine": {"accessibilityLabel": price_label}
        },
    }
    if rating_str:
        card["avgRatingLocalized"] = rating_str
    if subtitle:
        card["subtitle"] = subtitle
    if bedrooms_body:
        card["structuredContent"] = {
            "primaryLine": [{"type": "BEDINFO", "body": bedrooms_body}]
        }
    return card


# ── Sample cards ──────────────────────────────────────────────────────────────

CARD_VILLA = _make_card(
    "1234567890",
    "Villa in Bang Tao",
    "THB 8,500 per night",
    rating_str="4.87 (143)",
    bedrooms_body="3 bedrooms",
    subtitle="Sunset Pool Villa Bang Tao",
)

CARD_CONDO = _make_card(
    "9988776655",
    "Apartment in Laguna",
    "THB14,742 for 7 nights",
    rating_str="4.50 (38)",
    bedrooms_body="1 bedroom",
)

CARD_TINY = _make_card(
    "1111222233",
    "Tiny home in Choeng Thale",
    "THB7,000 for 7 nights",
    rating_str="New",
)

CARD_NO_PRICE = {
    "demandStayListing": {"id": _make_id("000")},
    "title": "Home in Patong",
    "structuredDisplayPrice": {"primaryLine": {}},
}


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_decode_listing_id():
    encoded = _make_id("1525252042518288418")
    result = _decode_listing_id(encoded)
    assert result == "1525252042518288418", f"Got {result!r}"
    print(f"  [PASS] decode listing id -> {result}")


def test_decode_listing_id_fallback():
    result = _decode_listing_id("plain_string")
    assert result == "plain_string"
    print("  [PASS] decode fallback returns raw value")


def test_parse_price_per_night():
    assert _parse_price_label("THB 8,500 per night") == 8500.0
    assert _parse_price_label("฿3,500 per night") == 3500.0
    print("  [PASS] price per-night pattern")


def test_parse_price_for_nights():
    result = _parse_price_label("THB14,742 for 7 nights")
    assert result == round(14742 / 7, 2), f"Got {result}"
    result2 = _parse_price_label("฿14,742 for 7 nights")
    assert result2 == round(14742 / 7, 2)
    print(f"  [PASS] price for-nights -> {result}")


def test_parse_price_with_original():
    result = _parse_price_label("฿27,424 for 7 nights, originally ฿29,983")
    assert result == round(27424 / 7, 2), f"Got {result}"
    print(f"  [PASS] price with 'originally' discount -> {result}")


def test_parse_price_none():
    assert _parse_price_label("") is None
    assert _parse_price_label(None) is None
    print("  [PASS] empty price -> None")


def test_parse_rating_full():
    rating, count = _parse_rating("4.92 (12)")
    assert rating == 4.92
    assert count == 12
    print(f"  [PASS] rating 4.92 (12) -> ({rating}, {count})")


def test_parse_rating_new():
    rating, count = _parse_rating("New")
    assert rating is None
    assert count is None
    print("  [PASS] rating 'New' -> (None, None)")


def test_parse_rating_no_count():
    rating, count = _parse_rating("4.5")
    assert rating == 4.5
    assert count is None
    print(f"  [PASS] rating without count -> ({rating}, {count})")


def test_parse_bedrooms():
    sc = {"primaryLine": [{"type": "BEDINFO", "body": "3 bedrooms · 6 guests"}]}
    assert _parse_bedrooms(sc) == 3
    print("  [PASS] parse_bedrooms -> 3")


def test_parse_bedrooms_studio():
    sc = {"primaryLine": [{"type": "BEDINFO", "body": "Studio · 2 guests"}]}
    assert _parse_bedrooms(sc) == 0
    print("  [PASS] parse_bedrooms studio -> 0")


def test_parse_bedrooms_none():
    assert _parse_bedrooms(None) is None
    assert _parse_bedrooms({}) is None
    print("  [PASS] parse_bedrooms None -> None")


def test_location_mapping():
    cases = [
        ("Bang Tao, Thalang",       "bang-tao"),
        ("Laguna Phuket",           "bang-tao"),
        ("Choeng Thale, Thalang",   "cherng-talay"),
        ("Rawai, Mueang Phuket",    "rawai"),
        ("Si Sunthon, Thalang",     "layan"),
        ("Patong, Kathu District",  "patong"),
        ("Unknown Area, Phuket",    "phuket-other"),
        ("Surin, Thalang",          "surin"),
    ]
    for loc, expected in cases:
        got = _location_to_district(loc)
        ok = got == expected
        print(f"  [{'PASS' if ok else 'FAIL'}] {loc!r} -> {got!r}")
        assert ok, f"Expected {expected!r}, got {got!r}"


def test_find_card_nodes():
    nested = {
        "sections": [
            {"results": [CARD_VILLA, CARD_CONDO]},
            {"other": "stuff"},
        ]
    }
    cards = _find_card_nodes(nested)
    assert len(cards) == 2, f"Expected 2 cards, got {len(cards)}"
    print(f"  [PASS] find_card_nodes -> {len(cards)} cards")


def test_parse_card_villa():
    r = _parse_card(CARD_VILLA, "peak")
    assert r is not None
    assert r.source_id == "1234567890"
    assert r.district == "bang-tao"
    assert r.property_type == "villa"
    assert r.bedrooms == 3
    assert r.daily_rate_thb == 8500.0
    assert r.platform_rating == 4.87
    assert r.platform_reviews_count == 143
    assert r.season_tag == "peak"
    assert r.rental_type == "short_term"
    print(f"  [PASS] parse_card villa bang-tao peak THB8500 beds=3 rating=4.87")


def test_parse_card_condo_for_nights():
    r = _parse_card(CARD_CONDO, "high")
    assert r is not None
    assert r.district == "bang-tao"     # laguna -> bang-tao
    assert r.property_type == "condo"
    assert r.bedrooms == 1
    assert abs(r.daily_rate_thb - round(14742 / 7, 2)) < 0.01
    assert r.season_tag == "high"
    print(f"  [PASS] parse_card condo laguna->bang-tao high THB{r.daily_rate_thb:.0f}")


def test_parse_card_tiny_home():
    r = _parse_card(CARD_TINY, "low")
    assert r is not None
    assert r.district == "cherng-talay"
    assert r.property_type == "condo"   # tiny home -> condo
    assert r.platform_rating is None    # "New"
    assert r.platform_reviews_count is None
    assert abs(r.daily_rate_thb - round(7000 / 7, 2)) < 0.01
    print(f"  [PASS] parse_card tiny-home cherng-talay low THB{r.daily_rate_thb:.0f}")


def test_parse_card_no_price_returns_none():
    r = _parse_card(CARD_NO_PRICE, "shoulder")
    assert r is None
    print("  [PASS] parse_card no price -> None")


def test_parse_card_amenity_detection():
    card = _make_card(
        "5556667788",
        "Villa in Surin",
        "THB12,000 per night",
        subtitle="Ocean View Pool Villa Surin Beach",
    )
    r = _parse_card(card, "high")
    assert r is not None
    assert r.has_pool is True
    assert r.has_sea_view is True
    assert r.has_gym is False
    print(f"  [PASS] amenity detection pool=True sea_view=True gym=False")


if __name__ == "__main__":
    tests = [
        test_decode_listing_id,
        test_decode_listing_id_fallback,
        test_parse_price_per_night,
        test_parse_price_for_nights,
        test_parse_price_with_original,
        test_parse_price_none,
        test_parse_rating_full,
        test_parse_rating_new,
        test_parse_rating_no_count,
        test_parse_bedrooms,
        test_parse_bedrooms_studio,
        test_parse_bedrooms_none,
        test_location_mapping,
        test_find_card_nodes,
        test_parse_card_villa,
        test_parse_card_condo_for_nights,
        test_parse_card_tiny_home,
        test_parse_card_no_price_returns_none,
        test_parse_card_amenity_detection,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  [FAIL] {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            import traceback
            print(f"  [ERROR] {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print()
    if failed:
        print(f"FAILED: {failed}/{len(tests)}")
        sys.exit(1)
    else:
        print(f"ALL {len(tests)} TESTS PASSED")
