"""
Configuration for the Discord News Aggregator Bot
"""
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Sensitive credentials from .env
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
TELEGRAM_API_ID = os.getenv('TELEGRAM_API_ID')
TELEGRAM_API_HASH = os.getenv('TELEGRAM_API_HASH')

# RSS Feed URLs
RSS_FEEDS = {
    "unusual_whales": "https://rss.app/feeds/MRsE23OX1FDxCdJ6.xml",
    "dexerto_twitter": "https://rss.app/feeds/jj6pbdE2H5AEwfeY.xml",
    "solana_floor": "https://rss.app/feeds/cJaLGwWKeTNniyhL.xml",
    "quiver_quant": "https://rss.app/feeds/yiVD4vcQbQ8i2HDs.xml",
    "degenerate_news": "https://rss.app/feeds/lJkV7xfSTsJOoYoD.xml",
    "watcher_guru": "https://rss.app/feeds/jQfpcfiYsZL0NwkI.xml",
    "newswire": "https://rss.app/feeds/DVrZpUnw9TZqLVNg.xml"
}

# Discord Channel IDs for each category
DISCORD_CHANNELS = {
    "crypto": 1317592423962251275,
    "news/politics": 1317592486927007784,
    "stocks": 1317592539192229918,
    "artificial intelligence": 1317592582368268338,
    "video games": 1317592652044046347,
    "sports": 1317592748005654688,
    "food": 1317592771258749078,
    "technology": 1317592703554420796,
    "music": 1343736462939783259,
    "fashion": 1344412433552248973,
    "ignore": 1344410355224547441
}

# Default category for uncertain/unmatched content
DEFAULT_CATEGORY = "ignore"

# Telegram channels to monitor
TELEGRAM_CHANNELS = [
    "Fin_Watch",
    "news_crypto",
    "drops_analytics",
    "joescrypt",
    "unfolded",
    "unfolded_defi",
    "infinityhedge"
]

# Ollama configuration
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_CATEGORIZATION_MODEL = "gpt-oss:20b"
OLLAMA_EMBEDDING_MODEL = "nomic-embed-text"

# System prompt for categorization
SYSTEM_PROMPT = """You are a news categorization assistant. Your job is to categorize news articles into ONE of the following categories:

- crypto
- news/politics
- stocks
- artificial intelligence
- video games
- sports
- food
- technology
- music
- fashion
- ignore (if the content doesn't fit any category or is unclear)

Respond with ONLY the category name, nothing else. Be precise and choose the single most appropriate category."""

# Duplicate detection thresholds (cosine similarity)
DUPLICATE_THRESHOLD = 0.95  # Exact duplicates only (>0.95 similarity)
SIMILARITY_THRESHOLD = 0.70  # Similar content - route to ignore channel

# Database paths
DB_PROCESSED_IDS = "data/processed_ids.json"
DB_EMBEDDINGS = "data/embeddings_cache.json"
DB_LAST_MESSAGE_IDS = "data/last_message_ids.json"

# Polling interval (seconds)
POLL_INTERVAL = 300  # 5 minutes

# Database retention period (hours)
DB_RETENTION_HOURS = 48

# OCR Configuration
OCR_ENABLED = True  # Set to False to disable OCR text extraction
TESSERACT_PATH = None  # Set to custom path if Tesseract is not in standard location
OCR_LANGUAGE = 'eng'  # Language for OCR (default: English)

