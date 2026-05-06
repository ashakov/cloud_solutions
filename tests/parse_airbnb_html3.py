"""Extract and print all listing fields from the real Airbnb niobeClientData structure."""
import sys, json, re, pathlib
sys.stdout.reconfigure(encoding="utf-8")

html = pathlib.Path("tests/airbnb_debug/page.html").read_text(encoding="utf-8")
m = re.search(r'<script[^>]*>\s*(\{"niobeClientData":.+?)</script>', html, re.DOTALL)
data = json.loads(m.group(1))
payload = data["niobeClientData"][0][1]

def find_card_nodes(obj, depth=0, results=None):
    if results is None:
        results = []
    if depth > 15:
        return results
    if isinstance(obj, dict):
        if "propertyId" in obj and "structuredDisplayPrice" in obj:
            results.append(obj)
            return results
        for v in obj.values():
            find_card_nodes(v, depth + 1, results)
    elif isinstance(obj, list):
        for item in obj:
            find_card_nodes(item, depth + 1, results)
    return results

cards = find_card_nodes(payload)
print(f"Card nodes found: {len(cards)}\n")

for i, card in enumerate(cards[:3]):
    print(f"=== Card {i+1} ===")
    # Top-level card fields
    print(f"  propertyId:          {card.get('propertyId')}")
    print(f"  title:               {card.get('title')}")
    print(f"  nameLocalized:       {card.get('nameLocalized')}")
    print(f"  avgRatingLocalized:  {card.get('avgRatingLocalized')}")
    print(f"  subtitle:            {card.get('subtitle')}")

    # Price
    sdp = card.get("structuredDisplayPrice", {})
    primary = sdp.get("primaryLine", {})
    print(f"  price primaryLine:   {primary}")

    # demandStayListing has bedrooms, location, etc.
    dsl = card.get("demandStayListing", {})
    if dsl:
        print(f"  demandStayListing keys: {list(dsl.keys())[:15]}")
        for f in ["id","name","roomTypeCategory","bedrooms","bathrooms",
                   "personCapacity","city","coordinate","avgRating",
                   "previewAmenities","neighborhoodId","locationTitle"]:
            if f in dsl:
                print(f"    {f}: {str(dsl[f])[:80]}")

    # structuredContent might have location text
    sc = card.get("structuredContent", {})
    if sc:
        print(f"  structuredContent mapMarker: {sc.get('mapMarker','')}")
        items = sc.get("primaryLine", []) or sc.get("secondaryLine", [])
        if items:
            print(f"  structuredContent line: {items[:2]}")

    print()
