import sys, ast, pathlib
sys.stdout.reconfigure(encoding="utf-8")

src = pathlib.Path("main.py").read_text(encoding="utf-8")
ast.parse(src)
print("main.py: syntax OK")

assert "async def cmd_rent" in src, "cmd_rent missing"
assert "async def _upsert_rentals" in src, "_upsert_rentals missing"
assert "'rent'" in src or '"rent"' in src, "rent command not wired"
print("cmd_rent: defined and wired")
print("_upsert_rentals: defined")

from parsers.airbnb import AirbnbParser, _parse_listing, _location_to_district
from parsers.fazwaz_rental import FazWazRentalParser, RawRentalListing
print("AirbnbParser: import OK")
print("FazWazRentalParser: import OK")

print("\nAll checks passed.")
