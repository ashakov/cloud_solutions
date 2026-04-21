"""
URL probe — найти рабочие URL-шаблоны FazWaz и DotProperty.
Запусти: python probe_urls.py
"""
import asyncio
import httpx

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

CANDIDATES = [
    # ── FazWaz ──────────────────────────────────────────────────────────────
    "https://www.fazwaz.com/condos-for-sale/thailand/phuket",
    "https://www.fazwaz.com/condos-for-sale/thailand/phuket/bang-tao-beach",
    "https://www.fazwaz.com/condos-for-sale/thailand/phuket/bang-tao",
    "https://www.fazwaz.com/property-for-sale/thailand/phuket",
    "https://www.fazwaz.com/property-for-sale/thailand/phuket/bang-tao",
    "https://www.fazwaz.com/property-for-sale/thailand/phuket?search[area_name][]=Bang+Tao",
    "https://www.fazwaz.com/property-for-sale/thailand/phuket?search%5Breal_estate_type_id%5D=1",
    "https://www.fazwaz.com/villas-for-sale/thailand/phuket",
    "https://www.fazwaz.com/villas-for-sale/thailand/phuket/bang-tao-beach",

    # ── DotProperty ─────────────────────────────────────────────────────────
    "https://www.dotproperty.co.th/properties-for-sale/phuket",
    "https://www.dotproperty.co.th/properties-for-sale/phuket/bang-tao",
    "https://www.dotproperty.co.th/properties-for-sale/phuket?location=bang-tao",
    "https://www.dotproperty.co.th/properties-for-sale/phuket?search=bang+tao",
    "https://www.dotproperty.co.th/buy/phuket",
    "https://www.dotproperty.co.th/buy/phuket/bang-tao",
    "https://www.dotproperty.co.th/condominium-for-sale/phuket",
]


async def probe():
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=10) as client:
        for url in CANDIDATES:
            try:
                r = await client.get(url)
                marker = "✅" if r.status_code == 200 else ("🔀" if r.status_code in (301, 302) else "❌")
                print(f"{marker} {r.status_code}  {url}")
                if r.status_code in (301, 302):
                    print(f"       → {r.headers.get('location', '')}")
            except Exception as e:
                print(f"💥 ERR   {url}  ({e})")

asyncio.run(probe())
