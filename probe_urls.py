"""
URL probe — найти рабочие URL-шаблоны FazWaz и DotProperty.
Запусти: python probe_urls.py
"""
import asyncio
import httpx
from playwright.async_api import async_playwright

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ── httpx probe ───────────────────────────────────────────────────────────────
HTTPX_CANDIDATES = [
    # DotProperty — property type filter variants
    "https://www.dotproperty.co.th/properties-for-sale/phuket?location=bang-tao&property_type=Condominium",
    "https://www.dotproperty.co.th/properties-for-sale/phuket?location=bang-tao&property_type=Villa",
    "https://www.dotproperty.co.th/properties-for-sale/phuket?location=kamala&property_type=Condominium",
    "https://www.dotproperty.co.th/properties-for-sale/phuket?location=patong",
    "https://www.dotproperty.co.th/properties-for-sale/phuket?location=rawai",
    "https://www.dotproperty.co.th/properties-for-sale/phuket?location=surin",
]

# ── Playwright probe for FazWaz (needs real browser) ─────────────────────────
FAZWAZ_PLAYWRIGHT_CANDIDATES = [
    "https://www.fazwaz.com/condos-for-sale/thailand/phuket",
    "https://www.fazwaz.com/condos-for-sale/thailand/phuket/bang-tao-beach",
    "https://www.fazwaz.com/condos-for-sale/thailand/phuket/bang-tao",
    "https://www.fazwaz.com/villas-for-sale/thailand/phuket",
    "https://www.fazwaz.com/property-for-sale/thailand/phuket?category=1",
    "https://www.fazwaz.com/property-for-sale/thailand/phuket?real_estate_type_id=1",
]


async def probe_httpx():
    print("\n── httpx probe ──────────────────────────────────────────────")
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=10) as client:
        for url in HTTPX_CANDIDATES:
            try:
                r = await client.get(url)
                marker = "✅" if r.status_code == 200 else ("🔀" if r.status_code in (301, 302) else "❌")
                body_hint = ""
                if r.status_code == 200:
                    # Show how many listing-like elements appear in response
                    count = r.text.count("property-card") + r.text.count("listing-card") + r.text.count("data-id=")
                    body_hint = f"  (hits≈{count})"
                print(f"{marker} {r.status_code}  {url}{body_hint}")
            except Exception as e:
                print(f"💥 ERR  {url}  ({e})")


async def probe_playwright():
    print("\n── Playwright probe (FazWaz) ────────────────────────────────")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--ignore-certificate-errors"])
        ctx = await browser.new_context(user_agent=HEADERS["User-Agent"], ignore_https_errors=True)
        page = await ctx.new_page()
        for url in FAZWAZ_PLAYWRIGHT_CANDIDATES:
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(2000)
                html = await page.content()
                # Count listing indicators
                count = (html.count("listing-card") + html.count("PropertyCard")
                         + html.count("data-testid") + html.count('"price"'))
                marker = "✅" if resp.status == 200 else "❌"
                print(f"{marker} {resp.status}  {url}  (hits≈{count}, html={len(html)}b)")
            except Exception as e:
                print(f"💥 ERR  {url}  ({e})")
        await browser.close()


async def main():
    await probe_httpx()
    await probe_playwright()
    print("\nDone.")

asyncio.run(main())
