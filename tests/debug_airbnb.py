"""
Debug script: navigate to Airbnb, save HTML + all JSON responses.
Run: python tests/debug_airbnb.py
"""
import sys, asyncio, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright
from config import SCRAPER_HEADLESS

URL = (
    "https://www.airbnb.com/s/Phuket--Thailand/homes"
    "?checkin=2027-02-10&checkout=2027-02-17&adults=2&currency=THB"
)

async def main():
    out = pathlib.Path("tests/airbnb_debug")
    out.mkdir(exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=SCRAPER_HEADLESS, args=[
            "--no-sandbox", "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
        ])
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            ignore_https_errors=True,
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await ctx.new_page()

        json_responses = []

        async def capture(response):
            ct = response.headers.get("content-type", "")
            if "json" in ct and response.status == 200:
                try:
                    body = await response.json()
                    json_responses.append({"url": response.url, "body": body})
                    print(f"  [JSON] {response.url[:80]}  status={response.status}")
                except Exception:
                    pass

        page.on("response", capture)

        print(f"Navigating to: {URL}")
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        # Wait for listing cards OR map — whichever signals data loaded
        try:
            await page.wait_for_selector(
                '[data-testid="listing-card-title"], [itemprop="itemListElement"], '
                '.t1jojoys, [class*="listingCard"]',
                timeout=15000,
            )
            print("  Listing cards found in DOM")
        except Exception:
            print("  Listing card selector timed out — waiting extra 8s for JS")
        await page.wait_for_timeout(8000)

        html = await page.content()
        html_path = out / "page.html"
        html_path.write_text(html, encoding="utf-8")
        print(f"\nHTML saved: {html_path} ({len(html):,} chars)")

        # Show page title and first 300 chars of body text
        title = await page.title()
        print(f"Page title: {title!r}")

        # Check for CAPTCHA / block signals
        lower_html = html.lower()
        for keyword in ["captcha", "robot", "blocked", "unusual traffic", "verify"]:
            if keyword in lower_html:
                print(f"  [WARN] Possible block: '{keyword}' found in HTML")

        # Check for listing data
        for keyword in ['"listing"', '"listingId"', '"roomTypeCategory"', "StaysSearch"]:
            count = html.count(keyword)
            if count:
                print(f"  [HTML] '{keyword}' appears {count}x")

        # Save all captured JSON responses
        print(f"\nCaptured {len(json_responses)} JSON responses:")
        for i, resp in enumerate(json_responses):
            path = out / f"resp_{i:02d}.json"
            path.write_text(json.dumps(resp["body"], indent=2, ensure_ascii=False),
                            encoding="utf-8")
            # Quick listing check
            body_str = json.dumps(resp["body"])
            has_listings = '"listing"' in body_str or '"listingId"' in body_str
            print(f"  [{i}] {resp['url'][:80]}")
            print(f"       saved={path.name}  has_listings={has_listings}  size={len(body_str):,}")

        await browser.close()
    print("\nDone. Check tests/airbnb_debug/")

asyncio.run(main())
