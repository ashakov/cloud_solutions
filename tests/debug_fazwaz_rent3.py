"""Test _parse_card on the saved HTML to see why bedrooms = None."""
import sys, pathlib, re
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from bs4 import BeautifulSoup
from parsers.fazwaz_rental import FazWazRentalParser

html = pathlib.Path("tests/fazwaz_debug/page.html").read_text(encoding="utf-8")
soup = BeautifulSoup(html, "lxml")
cards = soup.select(".result-search__item")
parser = FazWazRentalParser.__new__(FazWazRentalParser)

print(f"Cards: {len(cards)}\n")
for i, card in enumerate(cards[:8]):
    result = parser._parse_card(card)
    if result:
        print(f"Card {i+1}: beds={result.bedrooms!r}  baths={result.bathrooms!r}  "
              f"sqm={result.area_sqm!r}  type={result.property_type!r}  "
              f"district={result.district!r}  price={result.price_thb!r}  "
              f"name={result.project_name!r}")
    else:
        print(f"Card {i+1}: None")

# Also manually check desc regex on card 1
print("\n--- Manual regex check on card 1 ---")
desc = cards[0].select_one(".unit-info__shot-description")
if desc:
    t = desc.get_text(" ", strip=True)
    print(f"desc text: {t[:150]!r}")
    m1 = re.search(r"(\d+)\s*(?:Bed|BR)", t, re.IGNORECASE)
    print(f"bed regex match: {m1.group(0)!r} -> {m1.group(1)}" if m1 else "bed regex: NO MATCH")
    # Try wrap-icon-info
    wrap = cards[0].select_one(".wrap-icon-info")
    if wrap:
        wt = wrap.get_text(" ", strip=True)
        print(f"wrap text: {wt!r}")
        m2 = re.search(r"(\d+)\s*Bedroom", wt, re.IGNORECASE)
        print(f"wrap bed match: {m2.group(0)!r} -> {m2.group(1)}" if m2 else "wrap bed: NO MATCH")
    # Try description-title
    dt = cards[0].select_one(".unit-info__description-title")
    if dt:
        dtt = dt.get_text(strip=True)
        print(f"desc-title: {dtt!r}")
        m3 = re.search(r"(\d+)\s*Bedroom", dtt, re.IGNORECASE)
        print(f"desc-title bed match: {m3.group(0)!r} -> {m3.group(1)}" if m3 else "desc-title: NO MATCH")
