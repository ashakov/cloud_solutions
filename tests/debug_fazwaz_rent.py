"""Save FazWaz rental page HTML and show bed/bath selectors."""
import sys, asyncio, re, pathlib
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from playwright.async_api import async_playwright
from config import SCRAPER_HEADLESS
from bs4 import BeautifulSoup

URL = "https://www.fazwaz.com/property-for-rent/thailand/phuket?page=1"

async def main():
    out = pathlib.Path("tests/fazwaz_debug")
    out.mkdir(exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=SCRAPER_HEADLESS)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        page = await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector(".result-search__item", timeout=15000)
        except Exception:
            print("WARNING: .result-search__item not found")
        await page.wait_for_timeout(2000)
        html = await page.content()
        await browser.close()

    path = out / "page.html"
    path.write_text(html, encoding="utf-8")
    print(f"Saved: {path} ({len(html):,} chars)")

    soup = BeautifulSoup(html, "lxml")
    cards = soup.select(".result-search__item")
    print(f"Cards found: {len(cards)}")

    # Inspect first 3 cards in detail
    for i, card in enumerate(cards[:3]):
        print(f"\n{'='*60}")
        print(f"CARD {i+1}")
        print(f"{'='*60}")

        # Title
        title_tag = card.select_one(".unit-name") or card.select_one(".unit-info_title")
        print(f"  title: {title_tag.get_text(strip=True) if title_tag else 'NOT FOUND'}")

        # Price
        price_tag = card.select_one(".price-tag")
        print(f"  price: {price_tag.get_text(' ', strip=True) if price_tag else 'NOT FOUND'}")

        # Location
        loc_tag = card.select_one(".location-unit")
        print(f"  location: {loc_tag.get_text(strip=True) if loc_tag else 'NOT FOUND'}")

        # shot-description
        desc = card.select_one(".unit-info__shot-description")
        if desc:
            print(f"  .unit-info__shot-description: {desc.get_text(' ', strip=True)!r}")
        else:
            print("  .unit-info__shot-description: NOT FOUND")

        # features
        feats = card.select(".unit-info__feature")
        for j, f in enumerate(feats):
            print(f"  .unit-info__feature[{j}]: {f.get_text(' ', strip=True)!r}")

        # All elements with bed/bath/sqm info
        card_text = card.get_text(" ", strip=True)
        print(f"  card_text excerpt: {card_text[:300]!r}")

        # Try various bed patterns
        patterns = [
            (r"(\d+)\s*(?:Bed|BR)",       "Bed/BR"),
            (r"(\d+)\s*(?:bedroom)",       "bedroom"),
            (r"Bed[a-z]*[:\s]+(\d+)",      "Bed: N"),
            (r"(\d+)\s*(?:Bath)",          "Bath"),
        ]
        for pat, label in patterns:
            m = re.search(pat, card_text, re.IGNORECASE)
            print(f"  regex [{label}]: {m.group(0)!r} -> {m.group(1)} " if m else f"  regex [{label}]: no match")

        # Show all class names present in this card
        all_classes = set()
        for tag in card.find_all(True):
            for cls in (tag.get("class") or []):
                all_classes.add(cls)
        bed_classes = [c for c in all_classes if any(k in c.lower() for k in ["bed","bath","room","feat","info","desc","detail","spec"])]
        print(f"  relevant classes: {sorted(bed_classes)}")

asyncio.run(main())
