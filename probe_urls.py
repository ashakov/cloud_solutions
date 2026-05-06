"""
HTML inspector — находит реальные CSS-классы карточек листингов.
Запусти: python probe_urls.py
"""
import asyncio
import re
from collections import Counter
from playwright.async_api import async_playwright

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


async def inspect(browser, url: str, label: str, wait_sel: str | None = None):
    print(f"\n{'─'*60}")
    print(f"🔍  {label}")
    print(f"    {url}")
    ctx = await browser.new_context(user_agent=UA, ignore_https_errors=True)
    page = await ctx.new_page()
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        print(f"    HTTP {resp.status}  |  HTML {len(await page.content())//1024}KB")

        # scroll to trigger lazy load
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(3000)

        if wait_sel:
            try:
                await page.wait_for_selector(wait_sel, timeout=8000)
                print(f"    ✅ selector found: {wait_sel}")
            except Exception:
                print(f"    ❌ selector NOT found: {wait_sel}")

        html = await page.content()

        # ── Find all elements that look like listing cards ──────────────────
        # Collect class names from <article>, <li>, <div> that appear 5+ times
        tag_classes = re.findall(
            r'<(?:article|li|div|a)\s[^>]*class="([^"]+)"', html
        )
        counter = Counter()
        for cls_str in tag_classes:
            for cls in cls_str.split():
                counter[cls] += 1

        print("\n    Top repeated class names (potential card selectors):")
        for cls, n in counter.most_common(30):
            if n >= 4:
                print(f"      .{cls}  ×{n}")

        # ── Find price patterns ─────────────────────────────────────────────
        prices = re.findall(r'[฿$]\s*[\d,]+(?:\.\d+)?(?:\s*[MK])?', html)
        if prices:
            print(f"\n    Price patterns found (first 5): {prices[:5]}")
        else:
            print("\n    ⚠️  No price patterns found (฿/$ + digits)")

        # ── Find sqm patterns ───────────────────────────────────────────────
        sqm = re.findall(r'\d[\d,]*\s*(?:sq\.?\s*m|m²|sqm)', html, re.IGNORECASE)
        if sqm:
            print(f"    Sqm patterns found (first 5): {sqm[:5]}")

        # ── Find next-page link ─────────────────────────────────────────────
        next_pg = re.findall(r'href="([^"]*page[=/]2[^"]*)"', html)
        if next_pg:
            print(f"    Next-page URL pattern: {next_pg[0]}")

        # ── Dump a snippet around first price ───────────────────────────────
        idx = html.find("฿")
        if idx == -1:
            idx = html.find("THB")
        if idx > 0:
            snippet = html[max(0, idx-300):idx+300]
            snippet = re.sub(r'\s+', ' ', snippet)
            print(f"\n    HTML snippet around first price:")
            print(f"    {snippet[:500]}")

    except Exception as e:
        print(f"    💥 ERROR: {e}")
    finally:
        await ctx.close()


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--ignore-certificate-errors"]
        )

        # ── FazWaz ──────────────────────────────────────────────────────────
        await inspect(
            browser,
            "https://www.fazwaz.com/property-for-sale/thailand/phuket?real_estate_type_id=1",
            "FazWaz — Phuket condos",
        )
        await inspect(
            browser,
            "https://www.fazwaz.com/property-for-sale/thailand/phuket?real_estate_type_id=1&search%5Barea_name%5D%5B%5D=Bang+Tao",
            "FazWaz — Bang Tao condos (area filter)",
        )

        # ── DotProperty ─────────────────────────────────────────────────────
        await inspect(
            browser,
            "https://www.dotproperty.co.th/properties-for-sale/phuket?location=bang-tao&property_type=Condominium",
            "DotProperty — Bang Tao condos",
        )

        await browser.close()
    print("\n✅ Done")

asyncio.run(main())
