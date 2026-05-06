"""Find price + ownership + CAM in detail page. Also check if basic-info needs JS wait."""
import sys, pathlib, re, json
sys.stdout.reconfigure(encoding="utf-8")
from bs4 import BeautifulSoup

for prop_id in (1, 2):
    path = pathlib.Path(f"tests/fazwaz_detail_debug/prop_{prop_id}.html")
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")
    full_text = soup.get_text(" ", strip=True).lower()

    print(f"\n{'='*70}")
    print(f" PROPERTY {prop_id}")
    print(f"{'='*70}")

    # All elements containing price patterns
    print("  --- price candidates ---")
    for el in soup.find_all(string=re.compile(r"[฿,]\s*[\d,]{3,}")):
        parent = el.find_parent()
        cls = " ".join(parent.get("class") or [])
        val = el.strip()[:80]
        if "script" not in parent.name.lower():
            print(f"    [{cls[:40]}] {val!r}")

    # header-detail-page (full)
    print("\n  --- header-detail-page text ---")
    hdr = soup.select_one(".header-detail-page-desktop") or soup.select_one(".header-detail-page")
    if hdr:
        print(f"    {hdr.get_text(' ', strip=True)[:400]!r}")

    # Ownership / freehold / leasehold
    print("\n  --- ownership signals in text ---")
    for kw in ["freehold", "leasehold", "chanote", "nor sor", "title deed", "ownership"]:
        idx = full_text.find(kw)
        if idx >= 0:
            snippet = full_text[max(0, idx-20):idx+60]
            print(f"    [{kw}]: ...{snippet!r}...")

    # CAM fee
    print("\n  --- CAM / maintenance fee ---")
    for kw in ["cam fee", "common area", "maintenance fee", "monthly fee", "common fee"]:
        idx = full_text.find(kw)
        if idx >= 0:
            snippet = full_text[max(0, idx-10):idx+60]
            print(f"    [{kw}]: ...{snippet!r}...")

    # Year built
    print("\n  --- year built ---")
    for kw in ["year built", "built in", "completed", "completion", "year of"]:
        idx = full_text.find(kw)
        if idx >= 0:
            snippet = full_text[max(0, idx-10):idx+60]
            print(f"    [{kw}]: ...{snippet!r}...")

    # all .basic-information* elements raw
    print("\n  --- .basic-information (all) ---")
    bi = soup.select_one(".basic-information")
    if bi:
        print(f"    text: {bi.get_text(' ', strip=True)[:400]!r}")
        # How many basic-information-info items
        items = bi.select(".basic-information-info")
        print(f"    .basic-information-info count: {len(items)}")
        for item in items[:5]:
            print(f"      item html: {str(item)[:200]}")

    # financing-detail
    fin = soup.select_one(".financing-detail")
    if fin:
        print(f"\n  .financing-detail: {fin.get_text(' ', strip=True)[:200]!r}")
