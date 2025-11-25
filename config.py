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
PERPLEXITY_API_KEY = os.getenv('PERPLEXITY_API_KEY')

# Perplexity AI Configuration
PERPLEXITY_BASE_URL = "https://api.perplexity.ai"
# Common models: sonar-small-online, sonar-medium-online, sonar-pro
# See https://docs.perplexity.ai/getting-started/models for full list
PERPLEXITY_MODEL = "sonar-reasoning-pro"  # Model with web search capability
PERPLEXITY_BUTTON_ENABLED = True  # Enable/disable Perplexity search buttons on Discord posts
PERPLEXITY_BUTTON_LABEL = "Get More Info"
PERPLEXITY_BUTTON_EMOJI = "🔍"
PERPLEXITY_BUTTON_STYLE = "primary"  # primary (blue), secondary (gray), success (green), danger (red)

# Citations button configuration (appears after Perplexity search completes)
PERPLEXITY_CITATIONS_BUTTON_ENABLED = True  # Enable/disable citations button
PERPLEXITY_CITATIONS_BUTTON_LABEL = "View Citations"
PERPLEXITY_CITATIONS_BUTTON_EMOJI = "📚"
PERPLEXITY_CITATIONS_BUTTON_STYLE = "secondary"  # primary (blue), secondary (gray), success (green), danger (red)

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
    "crypto": 775513484221743124,
    "news/politics": 1379921787629867138,
    "stocks": 854937605590220810,
    "artificial intelligence": 985273104483885137,
    "video games": 846045909002354739,
    "sports": 845809605934317639,
    "food": 852256197494046731,
    "technology": 928462998228598794,
    "music": 1300884069583687800,
    "fashion": 867223341626294282,
    "pop culture": 1432086691862024403,
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
SYSTEM_PROMPT = """You are an expert news categorization assistant. Your task is to analyze content and assign it to exactly ONE category with high precision.

## AVAILABLE CATEGORIES

### crypto
- Cryptocurrencies (Bitcoin, Ethereum, altcoins, etc.)
- Blockchain technology and applications
- NFTs, DeFi, Web3, DAOs
- Crypto exchanges, trading, regulations
- Crypto market analysis and price movements

### news/politics
- Political events, elections, government policies
- International relations, diplomacy, geopolitics
- Social issues, protests, activism
- Legal cases and legislation
- General breaking news and current events
- Economic policy and government decisions

### stocks
- Stock market movements and indices
- Individual company stock performance
- IPOs, mergers, acquisitions
- Traditional finance and banking
- Investment strategies and market analysis
- Corporate earnings and financial reports

### artificial intelligence
- AI/ML models, research, and breakthroughs
- Large language models (GPT, Claude, etc.)
- AI applications and tools
- AI ethics, safety, and regulation
- Machine learning techniques and papers
- Computer vision, robotics powered by AI

### video games
- Game releases, updates, patches
- Gaming industry news
- Esports, tournaments, competitions
- Game reviews and announcements
- Gaming hardware and platforms
- Game development and studios

### sports
- Sporting events, matches, games
- Athletes, teams, leagues
- Sports news, trades, signings
- Championships, tournaments
- Sports statistics and records
- Fantasy sports

### food
- Restaurants, chefs, culinary news
- Food trends and recipes
- Restaurant reviews and openings
- Food industry developments
- Nutrition and dietary topics
- Cooking techniques and cuisines

### technology
- General tech products and gadgets
- Software updates and releases
- Tech company news (that isn't AI/crypto specific)
- Internet services and platforms
- Cybersecurity and privacy
- Hardware, electronics, consumer tech
- Space technology and exploration

### music
- Music releases, albums, singles
- Artist news and announcements
- Music industry developments
- Concerts, tours, festivals
- Music streaming and platforms
- Musical instruments and production

### fashion
- Fashion shows, collections, trends
- Designer news and brand updates
- Fashion industry developments
- Style and clothing trends
- Fashion technology and sustainability
- Models, fashion photography

### pop culture
- Celebrity news and entertainment gossip
- Movies, TV shows, and streaming content
- Pop culture trends and viral moments
- Awards shows and entertainment events
- Celebrity social media and controversies
- Entertainment industry news (Hollywood, actors, directors)
- Reality TV and popular culture phenomena
- Influencers and internet personalities

### ignore
- Low-quality or spam content
- Unclear, ambiguous, or incomplete content
- Personal messages or conversations
- Advertisements without newsworthy content
- Content that doesn't fit any category above
- Duplicate or redundant information
- Memes without substantive news value
- When uncertain about relevance or quality

## CATEGORIZATION GUIDELINES

1. **Read Carefully**: Analyze the entire content, not just keywords
2. **Primary Topic**: Choose the category that represents the PRIMARY focus
3. **Be Specific**: If content spans multiple categories, pick the most dominant one
4. **Quality Matters**: Low-quality content should go to 'ignore' regardless of topic
5. **Context Clues**: Consider source, tone, and depth of information
6. **When Unclear**: Default to 'ignore' rather than miscategorizing
7. **Entertainment Context**: Theme parks, entertainment venues, and entertainment-focused technology (Disney animatronics, movie theater tech, concert staging) should be categorized as **pop culture**, NOT technology. Consider the PRIMARY CONTEXT: Is this entertainment news or tech industry news?

## DECISION TREE

1. Is the content clear, complete, and newsworthy? 
   → NO: Choose 'ignore'
   → YES: Continue

2. Does it primarily discuss a specific topic area?
   → NO: Choose 'ignore'
   → YES: Match to the most relevant category

3. If multiple categories could apply:
   → Choose the one that represents 60%+ of the content
   → If truly equal split, choose based on what a reader would search for

## EXAMPLES

"Tesla stock drops 5% after earnings report" → stocks
"Elon Musk tweets about Dogecoin" → crypto
"OpenAI releases GPT-5 with improved reasoning" → artificial intelligence
"New MacBook Pro features M4 chip" → technology
"Bitcoin reaches new all-time high" → crypto
"Fed raises interest rates by 0.25%" → news/politics
"Call of Duty releases new battle pass" → video games
"LeBron James scores 40 points in playoff game" → sports
"Taylor Swift announces new album release date" → pop culture
"Netflix cancels popular series after two seasons" → pop culture
"Disney reveals new Olaf animatronic for theme park" → pop culture
"Universal Studios adds holographic effects to attraction" → pop culture
"Robotics lab develops new AI-powered humanoid robot" → technology
"Engineers create breakthrough in autonomous navigation" → artificial intelligence
"Random meme with no context" → ignore
"Incomplete sentence..." → ignore

## OUTPUT FORMAT

Respond with ONLY the category name exactly as listed above. No explanation, no punctuation, no extra text.

Valid responses: crypto, news/politics, stocks, artificial intelligence, video games, sports, food, technology, music, fashion, pop culture, ignore"""

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

# Discord file attachment size limit (in MB)
# Discord limits: 25MB (free), 50MB (level 2 boost), 100MB (level 3 boost)
DISCORD_FILE_SIZE_LIMIT_MB = 25

# Feedback Learning Configuration
FEEDBACK_LEARNING_ENABLED = True  # Enable learning from user feedback (removed entries)
FEEDBACK_EXAMPLES_COUNT = 20  # Number of removed entries to include in system prompt
NOT_VALUABLE_BUTTON_ENABLED = True  # Enable "Not Valuable" button on Discord posts
NOT_VALUABLE_BUTTON_LABEL = "Not Valuable"
NOT_VALUABLE_BUTTON_EMOJI = "🗑️"
NOT_VALUABLE_BUTTON_STYLE = "danger"  # primary (blue), secondary (gray), success (green), danger (red)
NOT_VALUABLE_VOTES_REQUIRED = 2  # Number of unique votes needed to remove entry

# Re-categorize command configuration
RECATEGORIZE_COMMAND_ENABLED = True  # Enable text command for re-categorizing entries
RECATEGORIZE_COMMAND_PREFIX = "!recategorize"  # Command prefix (e.g., "!recategorize crypto")
RECATEGORIZE_ALLOWED_USER_IDS = [144983485268885504]  # Discord user IDs allowed to re-categorize entries

