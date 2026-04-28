"""Deep-inspect basic-information + icon blocks on saved detail pages."""
import sys, pathlib, re, json
sys.stdout.reconfigure(encoding="utf-8")
from bs4 import BeautifulSoup

for prop_id in (1, 2):
    path = pathlib.Path(f"tests/fazwaz_detail_debug/prop_{prop_id}.html")
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")

    print(f"\n{'='*70}")
    print(f" PROPERTY {prop_id}")
    print(f"{'='*70}")

    # H1
    h1 = soup.select_one("h1")
    print(f"  H1: {h1.get_text(strip=True) if h1 else 'N/A'!r}")

    # Price
    price = soup.select_one(".header-detail-page__right .price-tag") \
             or soup.select_one(".price-tag")
    print(f"  Price: {price.get_text(' ', strip=True) if price else 'N/A'!r}")

    # Bed / bath / size icons at top
    for cls in ("icon-bed-resale-rental", "icon-bath-resale-rental", "icon-size-resale-rental"):
        el = soup.select_one(f".{cls}")
        if el:
            parent = el.find_parent()
            print(f"  .{cls} parent: {parent.get_text(' ', strip=True)[:80]!r}")

    # basic-information items
    print("\n  --- .basic-information-info items ---")
    for item in soup.select(".basic-information-info"):
        topic = item.select_one(".basic-information-topic")
        line  = item.select_one(".basic-information-line")
        if topic or line:
            k = topic.get_text(strip=True) if topic else "?"
            v = line.get_text(strip=True) if line else "?"
            print(f"    {k:<35} {v}")

    # more-basic-information
    print("\n  --- .more-basic-information items ---")
    for item in soup.select(".more-basic-information .basic-information-info"):
        topic = item.select_one(".basic-information-topic")
        line  = item.select_one(".basic-information-line")
        k = topic.get_text(strip=True) if topic else "?"
        v = line.get_text(strip=True) if line else "?"
        print(f"    {k:<35} {v}")

    # enriched-feature-list / body-feature
    print("\n  --- .body-feature items ---")
    for feat in soup.select(".body-feature"):
        print(f"    {feat.get_text(' ', strip=True)[:80]!r}")

    # available-units table
    print("\n  --- .available-units-table-list__info (first 3) ---")
    for row in soup.select(".available-units-table-list__info")[:3]:
        print(f"    {row.get_text(' ', strip=True)[:100]!r}")

    # JSON-LD or script data
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
            if data.get("@type") in ("Product", "RealEstateListing", "Apartment"):
                print(f"\n  JSON-LD ({data['@type']}): {json.dumps(data)[:300]}")
        except Exception:
            pass
