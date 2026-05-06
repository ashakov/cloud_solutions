"""
Base class for Scrapling-based parsers (no browser — pure HTTP + smart headers).

Scrapling automatically rotates User-Agent, sets realistic Accept/Accept-Language
headers, and handles redirects. Use this for sites that do NOT require JS execution
(server-rendered HTML or Next.js __NEXT_DATA__ extraction).

Use parsers/base.py (Playwright) for pages that require JS evaluation.
"""
import asyncio
import json
import logging
import random
import re
from abc import ABC, abstractmethod
from typing import AsyncIterator

from scrapling.fetchers import Fetcher

from config import SCRAPER_DELAY_MIN, SCRAPER_DELAY_MAX
from parsers.base import RawListing

logger = logging.getLogger(__name__)


class ScraplingParser(ABC):
    """Synchronous HTTP parser powered by Scrapling."""

    SOURCE: str = ""

    def __init__(self) -> None:
        self._fetcher = Fetcher(auto_match=False)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _fetch(self, url: str, retries: int = 2) -> object | None:
        """GET url, return Scrapling page or None on failure."""
        for attempt in range(retries + 1):
            try:
                page = self._fetcher.get(url, follow_redirects=True, timeout=20)
                if page and page.status == 200:
                    return page
                logger.warning("[%s] HTTP %s for %s", self.SOURCE, getattr(page, "status", "?"), url)
            except Exception as exc:
                logger.warning("[%s] fetch error (attempt %d): %s", self.SOURCE, attempt + 1, exc)
                if attempt < retries:
                    asyncio.get_event_loop().run_until_complete(asyncio.sleep(2 ** attempt))
        return None

    @staticmethod
    def _extract_next_data(page) -> dict:
        """Pull __NEXT_DATA__ JSON from a Next.js page."""
        try:
            tag = page.find("script", {"id": "__NEXT_DATA__"})
            if tag:
                return json.loads(tag.text)
        except Exception:
            pass
        return {}

    @staticmethod
    def _parse_thb(raw: str | None) -> float | None:
        """Extract first THB price value from raw text."""
        if not raw:
            return None
        m = re.search(r"฿?\s*([\d,]+(?:\.\d+)?)\s*(M\b|K\b)?", raw.upper())
        if not m:
            return None
        val = float(m.group(1).replace(",", ""))
        suffix = m.group(2) or ""
        if suffix == "M":
            val *= 1_000_000
        elif suffix == "K":
            val *= 1_000
        return val if val > 0 else None

    async def _polite_delay(self) -> None:
        await asyncio.sleep(random.uniform(SCRAPER_DELAY_MIN, SCRAPER_DELAY_MAX))

    @abstractmethod
    async def scrape(
        self, district: str, property_type: str, max_pages: int
    ) -> AsyncIterator[RawListing]:
        """Yield RawListing objects. Must be implemented by each parser."""
        ...
