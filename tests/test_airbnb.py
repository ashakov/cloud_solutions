"""Unit tests for Airbnb parser — run without network or Playwright."""
import sys
sys.stdout.reconfigure(encoding="utf-8")

from parsers.airbnb import _parse_listing, _location_to_district, AirbnbParser

ITEM_VILLA = {
    "listing": {
        "id": "12345678", "name": "Private Pool Villa Bang Tao 3BR",
        "roomTypeCategory": "entire_home", "bedrooms": 3, "bathrooms": 3,
        "personCapacity": 6, "avgRating": 4.87, "reviewsCount": 143,
        "coordinate": {"latitude": 8.008, "longitude": 98.291},
        "city": "Bang Tao", "previewAmenities": ["pool", "gym"],
    },
    "pricingQuote": {"rate": {"amount": 8500.0, "currency": "THB"}},
}

ITEM_CONDO = {
    "listing": {
        "id": "99887766", "name": "Laguna Condo Studio Near Beach",
        "roomTypeCategory": "entire_home", "bedrooms": 0, "bathrooms": 1,
        "avgRating": 4.5, "reviewsCount": 38,
        "coordinate": {"latitude": 8.012, "longitude": 98.295},
        "city": "Laguna", "neighborhood": "Bang Tao",
    },
    "pricingQuote": {
        "structuredStayDisplayPrice": {"primaryLine": {"price": "2,800"}}
    },
}

ITEM_NO_PRICE = {
    "listing": {"id": "111", "name": "X", "coordinate": {}},
    "pricingQuote": {},
}

ITEM_RAWAI = {
    "listing": {
        "id": "55566677", "name": "Seaview House Rawai 2BR",
        "roomTypeCategory": "entire_home", "bedrooms": 2,
        "avgRatingLocalized": "4.92 out of 5", "reviewsCount": 67,
        "coordinate": {"latitude": 7.8, "longitude": 98.3},
        "city": "Rawai", "amenityIds": [7, 25, 77],
    },
    "pricingQuote": {"displayPrice": {"total": {"amount": 4200.0}}},
}


def test_villa_peak():
    r = _parse_listing(ITEM_VILLA, "peak")
    assert r is not None
    assert r.source_id == "12345678"
    assert r.district == "bang-tao"
    assert r.property_type == "villa"
    assert r.daily_rate_thb == 8500.0
    assert r.season_tag == "peak"
    assert r.has_pool is True
    assert r.has_gym is True
    assert r.bedrooms == 3
    print("  [PASS] villa bang-tao peak THB8500 pool=True beds=3")


def test_condo_shape2():
    r = _parse_listing(ITEM_CONDO, "high")
    assert r is not None
    assert r.district == "bang-tao"       # laguna -> bang-tao
    assert r.property_type == "condo"
    assert r.daily_rate_thb == 2800.0
    assert r.season_tag == "high"
    print("  [PASS] condo laguna->bang-tao high THB2800")


def test_no_price_returns_none():
    r = _parse_listing(ITEM_NO_PRICE, "low")
    assert r is None
    print("  [PASS] no price -> None")


def test_location_mapping():
    cases = [
        ("Choeng Thale, Thalang", "cherng-talay"),
        ("Rawai, Mueang Phuket",  "rawai"),
        ("Si Sunthon, Thalang",   "layan"),
        ("Patong, Kathu District","patong"),
        ("Unknown Area, Phuket",  "phuket-other"),
        ("Laguna Phuket",         "bang-tao"),
    ]
    for loc, expected in cases:
        got = _location_to_district(loc)
        ok = got == expected
        print(f"  [{'PASS' if ok else 'FAIL'}] {loc!r} -> {got!r}")
        assert ok, f"Expected {expected!r}, got {got!r}"


def test_extract_nested_json():
    parser = AirbnbParser.__new__(AirbnbParser)
    nested = {"data": {"presentation": {"explore": {"sections": {"sections": [
        {"sectionId": "HOMES_SEARCH_RESULTS",
         "section": {"results": [ITEM_VILLA, ITEM_CONDO]}}
    ]}}}}}
    found = parser._extract_listings_from_json(nested)
    assert len(found) == 2
    print(f"  [PASS] _extract_listings_from_json: found {len(found)} items")


def test_avgRatingLocalized_parse():
    r = _parse_listing(ITEM_RAWAI, "shoulder")
    assert r is not None
    assert r.district == "rawai"
    assert r.daily_rate_thb == 4200.0
    assert r.platform_rating == 4.92
    print(f"  [PASS] rawai shoulder THB4200 rating={r.platform_rating}")


if __name__ == "__main__":
    tests = [
        test_villa_peak,
        test_condo_shape2,
        test_no_price_returns_none,
        test_location_mapping,
        test_extract_nested_json,
        test_avgRatingLocalized_parse,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  [FAIL] {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  [ERROR] {t.__name__}: {e}")
            failed += 1

    print()
    if failed:
        print(f"FAILED: {failed}/{len(tests)}")
        sys.exit(1)
    else:
        print(f"ALL {len(tests)} TESTS PASSED")
