"""Save 2 FazWaz property detail pages and inspect their structure."""
import sys, asyncio, pathlib, re
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from config import SCRAPER_HEADLESS
import sqlite3

# Grab 2 URLs that are missing area_sqm
con = sqlite3.connect("phuket_invest.db")
urls = con.execute(
    "SELECT id, url FROM properties WHERE area_sqm IS NULL LIMIT 2"
).fetchall()
con.close()

out = pathlib.Path("tests/fazwaz_detail_debug")
out.mkdir(exist_ok=True)

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=SCRAPER_HEADLESS, args=[
            "--no-sandbox", "--disable-blink-features=AutomationControlled",
        ])
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )

        for prop_id, url in urls:
            print(f"\n{'='*60}")
            print(f"ID={prop_id}  URL={url}")
            print('='*60)

            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
            html = await page.content()
            await page.close()

            path = out / f"prop_{prop_id}.html"
            path.write_text(html, encoding="utf-8")
            print(f"Saved: {path} ({len(html):,} chars)")

            soup = BeautifulSoup(html, "lxml")

            # Try known detail selectors
            checks = [
                (".detail-summary",              "detail-summary"),
                (".listing-detail",              "listing-detail"),
                (".property-detail",             "property-detail"),
                (".unit-details",                "unit-details"),
                (".unit-detail__info",           "unit-detail__info"),
                (".unit-info__detail",           "unit-info__detail"),
                (".detail-title",                "detail-title"),
                (".detail-info",                 "detail-info"),
                ("[class*='detail']",            "*detail class"),
                (".price-tag",                   "price-tag"),
                (".property-features",           "property-features"),
                (".wrap-icon-info",              "wrap-icon-info"),
                (".unit-info__feature",          "unit-info__feature[0]"),
                ("table",                        "table"),
                (".table-property",              "table-property"),
                ("dl",                           "dl (definition list)"),
            ]
            for selector, label in checks:
                el = soup.select_one(selector)
                if el:
                    txt = el.get_text(" ", strip=True)[:150]
                    print(f"  [{label}]: {txt!r}")

            # All unique class fragments containing "detail|spec|feature|prop|info|bed|bath|sqm"
            all_classes = set()
            for tag in soup.find_all(True):
                for cls in (tag.get("class") or []):
                    all_classes.add(cls)
            interesting = sorted(c for c in all_classes if any(
                k in c.lower() for k in
                ["detail","spec","feature","prop","bed","bath","sqm","size",
                 "floor","area","owner","freehold","lease","info","summary"]
            ))
            print(f"\n  Interesting classes ({len(interesting)}):")
            for c in interesting[:40]:
                print(f"    .{c}")

            # Title / price
            title = soup.select_one("h1") or soup.select_one(".detail-title")
            if title:
                print(f"\n  H1/title: {title.get_text(strip=True)!r}")
            ptag = soup.select_one(".price-tag")
            if ptag:
                print(f"  price-tag: {ptag.get_text(' ', strip=True)!r}")

        await browser.close()

asyncio.run(main())
