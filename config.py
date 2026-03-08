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

# Context Menu Commands (Right-click on bot messages → Apps → Command Name)
# Note: These replaced buttons for a cleaner UI. Users right-click bot messages to access features.
PERPLEXITY_BUTTON_ENABLED = True  # Enable/disable "Get More Info" context menu command
# The following button appearance configs are kept for backward compatibility but are no longer used
# (Context menu commands use simple text names)
PERPLEXITY_BUTTON_LABEL = "Get More Info"  # Not used by context menu
PERPLEXITY_BUTTON_EMOJI = "🔍"  # Not used by context menu
PERPLEXITY_BUTTON_STYLE = "primary"  # Not used by context menu

# Citations are now automatically included in the "Get More Info" thread response
PERPLEXITY_CITATIONS_BUTTON_ENABLED = True  # Not used (citations always shown if available)
PERPLEXITY_CITATIONS_BUTTON_LABEL = "View Citations"  # Not used by context menu
PERPLEXITY_CITATIONS_BUTTON_EMOJI = "📚"  # Not used by context menu
PERPLEXITY_CITATIONS_BUTTON_STYLE = "secondary"  # Not used by context menu

# /news Slash Command Configuration
NEWS_SEARCH_COMMAND_ENABLED = True
NEWS_SEARCH_MODEL = "sonar-reasoning-pro"  # Model for news topic searches
NEWS_SEARCH_COOLDOWN_SECONDS = 30  # Per-user cooldown to prevent API abuse

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
    "politics": 1379921787629867138,
    # "stocks": 1317592539192229918,
    "artificial intelligence": 1317592582368268338,
    "video games": 1317592652044046347,
    "sports": 1317592748005654688,
    # "food": 1317592771258749078,
    "technology": 1317592703554420796,
    "music": 1343736462939783259,
    "fashion": 1344412433552248973,
    "pop culture": 1442779289526472774,
    "ignore": 1344410355224547441
}

# All valid categories the AI can return (used for validation)
# This is separate from DISCORD_CHANNELS so the AI's correct answers
# aren't rejected just because a channel is disabled
VALID_CATEGORIES = [
    "crypto", "politics", "stocks", "artificial intelligence",
    "video games", "sports", "food", "technology",
    "music", "fashion", "pop culture", "ignore"
]

# Default category for uncertain/unmatched content
DEFAULT_CATEGORY = "ignore"

# Pause Mode - When enabled, ALL entries are routed to the 'ignore' channel
# regardless of their AI categorization. The original category is preserved in reasoning.
# Useful for temporarily silencing all channels without changing any other config.
PAUSE_MODE = True

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

### politics
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
- Content from or mentioning @BTC_Tick (always ignore regardless of topic)
- Routine crypto/financial market data: daily price tickers, gainers/losers lists, fear & greed index readings, TVL rankings, wallet statistics, dominance charts, and other recurring metrics

## CATEGORIZATION GUIDELINES

1. **Read Carefully**: Analyze the entire content, not just keywords
2. **Primary Topic**: Choose the category that represents the PRIMARY focus
3. **Be Specific**: If content spans multiple categories, pick the most dominant one
4. **Quality Matters**: Low-quality content should go to 'ignore' regardless of topic
5. **Context Clues**: Consider source, tone, and depth of information
6. **When Unclear**: Default to 'ignore' rather than miscategorizing
7. **Entertainment Context**: Theme parks, entertainment venues, and entertainment-focused technology (Disney animatronics, movie theater tech, concert staging) should be categorized as **pop culture**, NOT technology. Consider the PRIMARY CONTEXT: Is this entertainment news or tech industry news?
8. **Data vs. News**: Recurring market data and statistics (daily price lists, index readings, TVL rankings, wallet breakdowns, gainers/losers) are NOT news — categorize as 'ignore' regardless of topic. Only truly unexpected events or breaking developments are newsworthy.

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
"Fed raises interest rates by 0.25%" → politics
"Call of Duty releases new battle pass" → video games
"LeBron James scores 40 points in playoff game" → sports
"Taylor Swift announces new album release date" → pop culture
"Netflix cancels popular series after two seasons" → pop culture
"Disney reveals new Olaf animatronic for theme park" → pop culture
"Universal Studios adds holographic effects to attraction" → pop culture
"Robotics lab develops new AI-powered humanoid robot" → technology
"Engineers create breakthrough in autonomous navigation" → artificial intelligence
"Top 100 24h Gainers: INJ $3.84 +13.12%, MORPHO $1.62 +7.00%... Top 100 24h Losers: NIGHT $0.0582 -4.26%..." → ignore
"95.6% of Pump.fun wallets broke even or lost money. Only 0.4% ever made over $10K. The platform collected $950M in fees." → ignore
"Crypto Fear and Greed Index Value: 9, Sentiment: Extreme Fear, BTC Price: $68005" → ignore
"Top DeFi Projects By TVL:" → ignore
"Daily BTC dominance: 54.2%, ETH dominance: 17.8%" → ignore
"Top 10 cryptos by market cap this week" → ignore
"Random meme with no context" → ignore
"Incomplete sentence..." → ignore
"JUST IN : Bitcoin hits $67,000 @BTC_Tick" → ignore

## OUTPUT FORMAT

Respond with ONLY the category name exactly as listed above. No explanation, no punctuation, no extra text.

Valid responses: crypto, politics, stocks, artificial intelligence, video games, sports, food, technology, music, fashion, pop culture, ignore"""

# Duplicate detection thresholds (cosine similarity)
DUPLICATE_THRESHOLD = 0.95  # Exact duplicates only (>0.95 similarity)
SIMILARITY_THRESHOLD = 0.60  # Similar content - route to ignore channel

# Database path (single SQLite file)
DB_PATH = "data/newsbot.db"

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

# "Not Valuable" Context Menu Command (Right-click on bot messages → Apps → "Not Valuable")
# Note: This replaced the button for a cleaner UI. Users right-click bot messages to vote.
NOT_VALUABLE_BUTTON_ENABLED = True  # Enable/disable "Not Valuable" context menu command
# The following button appearance configs are kept for backward compatibility but are no longer used
NOT_VALUABLE_BUTTON_LABEL = "Not Valuable"  # Not used by context menu
NOT_VALUABLE_BUTTON_EMOJI = "🗑️"  # Not used by context menu
NOT_VALUABLE_BUTTON_STYLE = "danger"  # Not used by context menu
NOT_VALUABLE_VOTES_REQUIRED = 2  # Number of unique votes needed to remove entry

# Re-categorize Context Menu Command (Right-click on bot messages → Apps → "Re-categorize")
# Note: This is a restricted command that only specific users can access
RECATEGORIZE_COMMAND_ENABLED = True  # Enable/disable "Re-categorize" context menu command
RECATEGORIZE_ALLOWED_USER_IDS = [144983485268885504]  # Discord user IDs allowed to re-categorize entries

# Source Context Menu Command (Right-click on bot messages → Apps → "Source")
# Shows the original Telegram/Twitter source URL to the user who triggered it
SOURCE_COMMAND_ENABLED = True

# Edit Text Context Menu Command (Right-click on bot messages → Apps → "Edit Text")
# Allows authorized users to edit the text of a posted entry
# Uses the same RECATEGORIZE_ALLOWED_USER_IDS for permission checks
EDIT_TEXT_COMMAND_ENABLED = True

# Newsworthiness Filter Configuration
# Filters out mundane/routine news by rating surprise, impact, and actionability
# Only posts that score ABOVE the threshold will be posted to their category
# Posts below threshold go to the 'ignore' channel for review
SHORT_VIDEO_FILTER_ENABLED = True  # Enable/disable the short video filter
SHORT_VIDEO_THRESHOLD = 60  # Videos under this duration (seconds) are sent to ignore

# ALL CAPS Capitalization Fix
# When enabled, entries detected as ALL CAPS are rewritten to proper
# sentence capitalization using Ollama before posting to Discord.
CAPS_FIX_ENABLED = True
CAPS_FIX_THRESHOLD = 0.65  # Ratio of uppercase letters to trigger rewrite (0.0-1.0)

NEWSWORTHINESS_FILTER_ENABLED = False  # Enable/disable the newsworthiness filter
NEWSWORTHINESS_THRESHOLD = 7.0  # STRICT: 1-10 scale, only high-quality news gets through
NEWSWORTHINESS_WEIGHTS = {
    'surprising': 0.45,   # 45% weight - must be genuinely unexpected, not routine
    'impact': 0.35,       # 35% weight - must affect many people significantly  
    'actionable': 0.20    # 20% weight - should prompt action or attention
}
