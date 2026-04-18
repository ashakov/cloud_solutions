import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./phuket_invest.db")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

SCRAPER_DELAY_MIN = float(os.getenv("SCRAPER_DELAY_MIN", "2.0"))
SCRAPER_DELAY_MAX = float(os.getenv("SCRAPER_DELAY_MAX", "5.0"))
SCRAPER_MAX_PAGES = int(os.getenv("SCRAPER_MAX_PAGES", "20"))
SCRAPER_HEADLESS = os.getenv("SCRAPER_HEADLESS", "true").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Phuket districts in FazWaz/DotProperty URL format
PHUKET_DISTRICTS = {
    "bang-tao": "Bang Tao",
    "kamala": "Kamala",
    "patong": "Patong",
    "kata": "Kata",
    "karon": "Karon",
    "rawai": "Rawai",
    "nai-harn": "Nai Harn",
    "surin": "Surin",
    "cherng-talay": "Cherng Talay",
    "mai-khao": "Mai Khao",
    "chalong": "Chalong",
    "phuket-town": "Phuket Town",
    "laguna": "Laguna",
    "layan": "Layan",
}

PROPERTY_TYPES = ["condo", "villa", "house", "townhouse", "land"]

# FazWaz search base URLs
FAZWAZ_BASE_URL = "https://www.fazwaz.com"
FAZWAZ_SEARCH_TEMPLATE = (
    "https://www.fazwaz.com/property-for-sale/thailand/phuket/{district}/{prop_type}"
    "?search%5Borg_id%5D=0&search%5Breal_estate_type_id%5D=&page={page}"
)

# DotProperty search base URLs
DOTPROPERTY_BASE_URL = "https://www.dotproperty.co.th"
DOTPROPERTY_SEARCH_TEMPLATE = (
    "https://www.dotproperty.co.th/properties-for-sale/phuket/{district}"
    "?property_type={prop_type}&page={page}"
)

# USD exchange rate (update periodically)
THB_TO_USD = 0.028
