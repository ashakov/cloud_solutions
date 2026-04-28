"""Find where full listing data (name + price + rating) lives in niobeClientData."""
import sys, json, re, pathlib
sys.stdout.reconfigure(encoding="utf-8")

html = pathlib.Path("tests/airbnb_debug/page.html").read_text(encoding="utf-8")
m = re.search(r'<script[^>]*>\s*(\{"niobeClientData":.+?)</script>', html, re.DOTALL)
data = json.loads(m.group(1))
payload = data["niobeClientData"][0][1]   # Single StaysSearch entry

# Walk full tree, collect every dict that has avgRating + name
def find_full_listings(obj, depth=0, results=None):
    if results is None:
        results = []
    if depth > 15:
        return results
    if isinstance(obj, dict):
        has_rating = "avgRating" in obj or "avgRatingLocalized" in obj
        has_name   = "name" in obj or "title" in obj
        has_id     = "id" in obj or "listingId" in obj or "listing" in obj
        if has_rating and (has_name or has_id):
            results.append(obj)
        else:
            for v in obj.values():
                find_full_listings(v, depth + 1, results)
    elif isinstance(obj, list):
        for item in obj:
            find_full_listings(item, depth + 1, results)
    return results

listings = find_full_listings(payload)
print(f"Nodes with avgRating+name/id: {len(listings)}")

if listings:
    s = listings[0]
    print(f"\nSample keys ({len(s)}): {list(s.keys())}")
    for f in ["id","name","avgRating","avgRatingLocalized","city","neighborhood",
              "roomType","roomTypeCategory","bedrooms","bathrooms","personCapacity",
              "coordinate","lat","lng","localizedCityName"]:
        if f in s:
            v = s[f]
            print(f"  {f}: {str(v)[:80]}")

# Now find price nodes adjacent to these listings
# Look for parent objects that contain both listing data and a price field
def find_listing_with_price(obj, depth=0, results=None):
    if results is None:
        results = []
    if depth > 15:
        return results
    if isinstance(obj, dict):
        # Check if this node or its direct children contain both rating and price signals
        obj_str = json.dumps(obj)
        has_rating = "avgRating" in obj_str
        has_price  = any(k in obj_str for k in ['"price"', '"rate"', '"displayPrice"',
                                                  '"structuredDisplayPrice"', '"amount"'])
        has_name   = '"name"' in obj_str
        if has_rating and has_price and has_name and len(obj_str) > 500:
            # Check it's not the entire payload
            if len(obj_str) < 50000:
                results.append(obj)
                return results  # Don't recurse into found node
        for v in obj.values():
            find_listing_with_price(v, depth + 1, results)
    elif isinstance(obj, list):
        for item in obj:
            find_listing_with_price(item, depth + 1, results)
    return results

print("\n--- Searching for listing+price nodes ---")
combo_nodes = find_listing_with_price(payload)
print(f"Found: {len(combo_nodes)}")
if combo_nodes:
    s = combo_nodes[0]
    print(f"Keys: {list(s.keys())[:20]}")
    s_str = json.dumps(s)
    print(f"Size: {len(s_str):,}")
    # Check for price keys
    for k in ["price", "rate", "displayPrice", "structuredDisplayPrice",
               "priceString", "formattedPrice", "amount"]:
        if k in s:
            print(f"  PRICE key '{k}': {str(s[k])[:100]}")
