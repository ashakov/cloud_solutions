"""
Telegram channel monitor — listens for new property listings in real time.

Requires in .env:
  TELEGRAM_API_ID=12345678
  TELEGRAM_API_HASH=abc123...
  TELEGRAM_CHANNELS=@phuket_real_estate,@phuket_invest,@phuket_condos

Install:  pip install telethon
Session file:  telegram_session.session  (auto-created on first run, store safely)

Usage:
  python -m parsers.telegram_monitor          # runs forever
  python main.py telegram                     # via CLI
"""
import asyncio
import hashlib
import logging
import os

from config import DATABASE_URL
from core.ai_extractor import extract
from db.database import init_db, AsyncSessionLocal
from parsers.normalizer import upsert_listing

logger = logging.getLogger(__name__)

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_CHANNELS_RAW = os.getenv("TELEGRAM_CHANNELS", "")
SESSION_FILE = "telegram_session"

# Keywords that suggest a listing is property-related (filter noise)
_LISTING_KW = [
    "฿", "thb", "sqm", "bedroom", "condo", "villa", "sale", "rent",
    "freehold", "leasehold", "pool", "phuket", "rawai", "patong", "bang tao",
    "kamala", "chalong", "ขาย", "เช่า", "คอนโด", "วิลล่า", "ห้องนอน",
]


def _is_listing(text: str) -> bool:
    t = text.lower()
    return sum(1 for kw in _LISTING_KW if kw in t) >= 3


async def run_monitor() -> None:
    """Start Telegram listener. Blocks until cancelled."""
    try:
        from telethon import TelegramClient, events
    except ImportError:
        logger.error("telethon not installed — run: pip install telethon")
        return

    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        logger.error(
            "Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env "
            "(get them at https://my.telegram.org/apps)"
        )
        return

    channels = [c.strip() for c in TELEGRAM_CHANNELS_RAW.split(",") if c.strip()]
    if not channels:
        logger.warning(
            "No TELEGRAM_CHANNELS set in .env. "
            "Example: TELEGRAM_CHANNELS=@phuket_real_estate,@phuket_invest"
        )
        return

    await init_db()
    client = TelegramClient(SESSION_FILE, TELEGRAM_API_ID, TELEGRAM_API_HASH)

    @client.on(events.NewMessage(chats=channels))
    async def handler(event):
        text = event.raw_text or ""
        if len(text) < 40 or not _is_listing(text):
            return

        msg_id = f"tg_{event.chat_id}_{event.id}"
        source_id = hashlib.sha1(msg_id.encode()).hexdigest()[:40]
        url = f"https://t.me/c/{abs(event.chat_id)}/{event.id}"

        listing = extract(text, source="telegram", source_id=source_id, url=url)
        if listing is None:
            return

        async with AsyncSessionLocal() as session:
            is_new, _ = await upsert_listing(session, listing)
            await session.commit()

        status = "NEW" if is_new else "seen"
        logger.info(
            "[Telegram] %s | %s ฿%s %s %s",
            status, listing.district, listing.price_thb, listing.property_type, url
        )

    logger.info("[Telegram] Connecting to channels: %s", ", ".join(channels))
    await client.start()
    logger.info("[Telegram] Listening for new messages (Ctrl+C to stop)...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_monitor())
