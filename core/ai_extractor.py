"""
AI-powered extractor for unstructured property text (Telegram/Facebook posts).

Uses Gemini (already configured) to convert free-form Thai/English listing
text into a structured RawListing.  Falls back to regex heuristics if
the LLM quota is exhausted or the key is not set.
"""
from __future__ import annotations

import json
import logging
import re

import google.generativeai as genai

from config import GEMINI_API_KEY, PHUKET_DISTRICTS
from parsers.base import RawListing

logger = logging.getLogger(__name__)

_SYSTEM = """\
You are a real-estate data extraction engine specialising in Phuket, Thailand.
Given a raw property listing text (Thai or English), extract the fields below and
return ONLY a JSON object. Use null for any field that cannot be determined.

Fields:
  price_thb         (number, Thai Baht)
  district          (one of: """ + ", ".join(PHUKET_DISTRICTS.keys()) + """)
  property_type     (condo | villa | house | townhouse | land)
  ownership_type    (freehold | leasehold | unknown)
  leasehold_years   (number or null)
  bedrooms          (number or null)
  bathrooms         (number or null)
  area_sqm          (number or null)
  has_pool          (true | false)
  has_gym           (true | false)
  is_off_plan       (true | false)
  rental_type       (short_term | long_term | unknown)
  monthly_rent_thb  (number or null — for rental listings)
  title_summary     (string, 1 sentence)

Return ONLY valid JSON, no markdown fences."""

# District keyword map for heuristic fallback
_DIST_KW = {
    "bang-tao": ["bang tao", "bangtao", "laguna"],
    "kamala": ["kamala"], "patong": ["patong"],
    "rawai": ["rawai"], "kata": ["kata"], "karon": ["karon"],
    "surin": ["surin"], "nai-harn": ["nai harn", "naiharn"],
    "cherng-talay": ["cherng talay", "choeng thale"],
    "chalong": ["chalong"], "phuket-town": ["phuket town"],
    "mai-khao": ["mai khao", "sakhu"], "layan": ["layan", "si sunthon"],
}


def _district_from_text(t: str) -> str:
    for slug, kws in _DIST_KW.items():
        if any(kw in t for kw in kws):
            return slug
    return "phuket-other"


def _heuristic(text: str, source: str, source_id: str, url: str = "") -> RawListing:
    """Pure-regex fallback when LLM is unavailable."""
    t = text.lower()

    price_m = re.search(r"฿?\s*([\d,]+(?:\.\d+)?)\s*(m\b|k\b|mb\b)?", text.upper())
    price = None
    if price_m:
        val = float(price_m.group(1).replace(",", ""))
        suf = price_m.group(2) or ""
        if suf in ("M", "MB"):
            val *= 1_000_000
        elif suf == "K":
            val *= 1_000
        price = val if val > 50_000 else None  # ignore tiny numbers

    beds_m = re.search(r"(\d)\s*(?:bed|br|bedroom|ห้องนอน)", t)
    area_m = re.search(r"([\d,]+)\s*(?:sqm|sq\.m|m²|ตร\.ม)", t)
    monthly_m = re.search(r"(\d[\d,]+)\s*(?:/month|/mo|thb/mo|บาท/เดือน)", t)

    if "villa" in t:
        prop_type = "villa"
    elif "land" in t or "ที่ดิน" in t:
        prop_type = "land"
    elif "townhouse" in t:
        prop_type = "townhouse"
    elif "house" in t or "บ้าน" in t:
        prop_type = "house"
    else:
        prop_type = "condo"

    return RawListing(
        source=source,
        source_id=source_id,
        url=url,
        district=_district_from_text(t),
        property_type=prop_type,
        ownership_type="freehold" if "freehold" in t else "leasehold" if "lease" in t else "unknown",
        bedrooms=int(beds_m.group(1)) if beds_m else None,
        area_sqm=float(area_m.group(1).replace(",", "")) if area_m else None,
        price_thb=price,
        monthly_rent_thb=float(monthly_m.group(1).replace(",", "")) if monthly_m else None,
        has_pool=bool(re.search(r"\bpool\b|\bสระ", t)),
        has_gym=bool(re.search(r"\bgym\b|\bfitness", t)),
        is_off_plan=bool(re.search(r"off.?plan|pre.?sale|under construction", t)),
    )


def extract(
    text: str,
    source: str,
    source_id: str,
    url: str = "",
    use_llm: bool = True,
) -> RawListing | None:
    """
    Convert free-form listing text → RawListing.

    Args:
        text:       Raw message/post text
        source:     e.g. "telegram", "facebook"
        source_id:  Message ID or post ID
        url:        Original URL if available
        use_llm:    Try Gemini first; fall back to regex if False or on error
    """
    if not text or len(text.strip()) < 30:
        return None

    if use_llm and GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel("gemini-1.5-flash", system_instruction=_SYSTEM)
            resp = model.generate_content(
                f"Extract property data from this listing:\n\n{text[:3000]}"
            )
            raw_json = resp.text.strip()
            # Strip markdown fences if present
            raw_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_json, flags=re.MULTILINE)
            data = json.loads(raw_json)

            district = data.get("district") or _district_from_text(text.lower())
            price_sqm = None
            price = data.get("price_thb")
            area = data.get("area_sqm")
            if price and area and area > 0:
                price_sqm = round(price / area, 2)

            return RawListing(
                source=source,
                source_id=source_id,
                url=url,
                project_name=data.get("title_summary"),
                district=district,
                property_type=data.get("property_type") or "condo",
                ownership_type=data.get("ownership_type") or "unknown",
                leasehold_years=data.get("leasehold_years"),
                bedrooms=data.get("bedrooms"),
                bathrooms=data.get("bathrooms"),
                area_sqm=area,
                price_thb=price,
                price_per_sqm_thb=price_sqm,
                monthly_rent_thb=data.get("monthly_rent_thb"),
                rental_type=data.get("rental_type"),
                has_pool=bool(data.get("has_pool")),
                has_gym=bool(data.get("has_gym")),
                is_off_plan=bool(data.get("is_off_plan")),
            )
        except Exception as exc:
            logger.warning("[AIExtractor] LLM failed (%s), using heuristic", exc)

    return _heuristic(text, source, source_id, url)
