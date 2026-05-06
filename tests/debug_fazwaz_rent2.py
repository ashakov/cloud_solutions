"""Inspect i-bed / i-bath elements and unit-info structure in FazWaz rental cards."""
import sys, pathlib, re
sys.stdout.reconfigure(encoding="utf-8")
from bs4 import BeautifulSoup

html = pathlib.Path("tests/fazwaz_debug/page.html").read_text(encoding="utf-8")
soup = BeautifulSoup(html, "lxml")
cards = soup.select(".result-search__item")
print(f"Cards: {len(cards)}")

for i, card in enumerate(cards[:5]):
    print(f"\n--- Card {i+1} ---")

    # i-bed and i-bath elements
    for cls in ("i-bed", "i-bath"):
        el = card.select_one(f".{cls}")
        if el:
            parent = el.parent
            print(f"  .{cls} parent html: {str(parent)[:200]!r}")
        else:
            print(f"  .{cls}: not found")

    # wrap-icon-info
    wrap = card.select_one(".wrap-icon-info")
    if wrap:
        print(f"  .wrap-icon-info text: {wrap.get_text(' ', strip=True)!r}")
        print(f"  .wrap-icon-info html: {str(wrap)[:400]}")

    # unit-info__basic-info
    basic = card.select_one(".unit-info__basic-info")
    if basic:
        print(f"  .unit-info__basic-info text: {basic.get_text(' ', strip=True)!r}")

    # description title (has "3 Bedroom Villa for rent")
    desc_title = card.select_one(".unit-info__description-title")
    if desc_title:
        print(f"  .unit-info__description-title: {desc_title.get_text(strip=True)!r}")
