"""Inspect niobeClientData structure in saved Airbnb HTML."""
import sys, json, re, pathlib
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

html = pathlib.Path("tests/airbnb_debug/page.html").read_text(encoding="utf-8")

# Extract niobeClientData script block
m = re.search(r'<script[^>]*>\s*(\{"niobeClientData":.+?)</script>', html, re.DOTALL)
if not m:
    print("niobeClientData NOT FOUND in HTML")
    sys.exit(1)

raw = m.group(1)
print(f"Raw length: {len(raw):,} chars")

data = json.loads(raw)
entries = data.get("niobeClientData", [])
print(f"Total entries: {len(entries)}")

# Show all entry keys
for i, entry in enumerate(entries):
    key = entry[0] if isinstance(entry, list) else str(entry)[:80]
    val_size = len(json.dumps(entry[1])) if isinstance(entry, list) and len(entry) > 1 else 0
    print(f"  [{i:02d}] key={str(key)[:80]}  val_size={val_size:,}")

# Deep-dive into StaysSearch entry
print()
for entry in entries:
    if not isinstance(entry, list):
        continue
    key = str(entry[0])
    if "StaysSearch" not in key:
        continue

    payload = entry[1]
    payload_str = json.dumps(payload)
    print(f"=== StaysSearch payload ({len(payload_str):,} chars) ===")
    print(f"  listingId mentions : {payload_str.count('listingId')}")
    print(f"  avgRating mentions : {payload_str.count('avgRating')}")
    print(f"  roomType mentions  : {payload_str.count('roomType')}")

    # Walk to find a node that looks like a listing
    def find_listings(obj, depth=0):
        if depth > 12:
            return []
        results = []
        if isinstance(obj, dict):
            if "listingId" in obj or ("id" in obj and "avgRating" in obj):
                results.append(obj)
            else:
                for v in obj.values():
                    results.extend(find_listings(v, depth + 1))
        elif isinstance(obj, list):
            for item in obj:
                results.extend(find_listings(item, depth + 1))
        return results

    listings = find_listings(payload)
    print(f"  Listing-like nodes : {len(listings)}")

    if listings:
        sample = listings[0]
        print(f"\n  Sample listing keys: {list(sample.keys())[:15]}")
        for field in ["listingId", "name", "avgRating", "city", "coordinate",
                       "roomType", "bedrooms", "bathrooms", "personCapacity"]:
            if field in sample:
                print(f"    {field}: {sample[field]}")

    # Find pricing nodes near listingId
    def find_pricing(obj, depth=0):
        if depth > 12:
            return []
        results = []
        if isinstance(obj, dict):
            has_listing = "listingId" in obj or "listing" in obj
            has_price = any(k in obj for k in ["structuredDisplayPrice", "price",
                                                "displayPrice", "rate"])
            if has_listing and has_price:
                results.append(obj)
            for v in obj.values():
                results.extend(find_pricing(v, depth + 1))
        elif isinstance(obj, list):
            for item in obj:
                results.extend(find_pricing(item, depth + 1))
        return results

    pricing_nodes = find_pricing(payload)
    print(f"\n  Nodes with listing+price: {len(pricing_nodes)}")
    if pricing_nodes:
        sample_p = pricing_nodes[0]
        print(f"  Sample keys: {list(sample_p.keys())[:12]}")

    break  # Only first StaysSearch entry
