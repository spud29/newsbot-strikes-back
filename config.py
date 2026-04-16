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
TELEGRAM_2FA_PASSWORD = os.getenv('TELEGRAM_2FA_PASSWORD')  # Optional: Telegram 2FA password

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

# Dexerto tweet pair merger
# Dexerto posts stories as two tweets: tweet 1 = headline, tweet 2 = blurb + article URL.
# Tweet 1 is held in the dexerto_pending SQLite table until tweet 2 arrives (survives restarts).
# If no follow-up arrives within this window, tweet 1 is posted alone during the cleanup cycle.
DEXERTO_PENDING_MAX_AGE_HOURS = 4.0

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
    "stocks": 854937605590220810,
    "artificial intelligence": 985273104483885137,
    "video games": 1317592652044046347,
    "sports": 845809605934317639,
    "food": 852256197494046731,
    "technology": 928462998228598794,
    "music": 1300884069583687800,
    "fashion": 867223341626294282,
    "pop culture": 1432086691862024403,
    "software development": 1332081237380173857,
    "fitness": 748918222551777412,
    "ignore": 1344410355224547441
}

# All valid categories the AI can return (used for validation)
# This is separate from DISCORD_CHANNELS so the AI's correct answers
# aren't rejected just because a channel is disabled
VALID_CATEGORIES = [
    "crypto", "politics", "stocks", "artificial intelligence",
    "video games", "sports", "food", "technology",
    "music", "fashion", "pop culture", "software development", "fitness", "ignore"
]

# Default category for uncertain/unmatched content
DEFAULT_CATEGORY = "ignore"

# Pause Mode - When enabled, ALL entries are routed to the 'ignore' channel
# regardless of their AI categorization. The original category is preserved in reasoning.
# Useful for temporarily silencing all channels without changing any other config.
PAUSE_MODE = False

# Unified Channel Mode - route ALL non-ignore categories to a single Discord channel.
# Each message is prefixed with a bold [Category] tag so readers can filter by topic.
# 'ignore' entries still route to the ignore channel as normal.
UNIFIED_CHANNEL_MODE = True
UNIFIED_CHANNEL_ID = 1379921787629867138  # The single channel to post everything to

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
- War, military conflict, and armed operations
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

### fitness
- Gym culture, workout trends, and training techniques
- Nutrition, dieting, and supplementation news
- Running, cycling, and endurance sports (recreational)
- Yoga, mindfulness, and wellness
- Fitness influencers, apps, and equipment
- Health-focused fitness topics (weight loss, muscle gain, rehabilitation)

### technology
- General tech products and gadgets
- Software updates and releases
- Tech company news (that isn't AI/crypto specific)
- Internet services and platforms
- Cybersecurity and privacy
- Hardware, electronics, consumer tech
- Space technology and exploration

### software development
- Programming languages, frameworks, and libraries
- Developer tools, IDEs, and workflows
- Open source projects and contributions
- Software engineering practices and methodologies
- APIs, SDKs, and developer platforms
- Version control, CI/CD, DevOps
- Coding tutorials, documentation, and developer communities

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
- Clearly non-news content: personal messages, advertisements without newsworthy value, memes without substantive content
- Content from or mentioning @BTC_Tick (always ignore regardless of topic)
- Routine recurring market data: daily price tickers, gainers/losers lists, fear & greed index readings, TVL rankings, dominance charts — data published on a schedule, not triggered by an event
- Routine stablecoin treasury operations: USDC/USDT minting and burning announcements with no broader context
- Promotional content: airdrops, referral programs, token launches from unknown projects
- Speculative predictions with no triggering news event: "My price target for BTC is $250K", "Bitcoin will hit $300K next year"
- Opinion/commentary threads without a concrete triggering event: "Here's why...", "Thread:", "My take on...", "I think..."
- Whale wallet tracking: large buy/sell/transfer notifications without broader market context or explanation
- Reposted old stories being recirculated without new developments
- Audience engagement bait that ends with reader questions: "Do you agree?", "What do you think?", "Thoughts?"

## CATEGORIZATION GUIDELINES

1. **Read Carefully**: Analyze the entire content, not just keywords
2. **Primary Topic**: Choose the category that represents the PRIMARY focus
3. **Be Specific**: If content spans multiple categories, pick the most dominant one
4. **Quality is Downstream**: If content is categorizable (matches a real topic), assign the correct category even if it seems routine or modest in impact. The newsworthiness filter evaluates quality AFTER categorization.
5. **Context Clues**: Consider source, tone, and depth of information
6. **When Unclear**: Assign the closest real category. Use 'ignore' only when content matches a structural ignore reason (spam, @BTC_Tick, routine scheduled data, opinion without an event, etc.), not because it feels borderline newsworthy.
7. **Entertainment Context**: Theme parks, entertainment venues, and entertainment-focused technology (Disney animatronics, movie theater tech, concert staging) should be categorized as **pop culture**, NOT technology. Consider the PRIMARY CONTEXT: Is this entertainment news or tech industry news?
8. **Structural vs. Quality**: Routine market data and scheduled metrics are 'ignore' because they're structural junk (no news event triggered them), not because of quality. A modest but real news event (e.g. "Bitcoin up 2% after Fed comment") is categorizable as 'crypto' and will go through the quality filter downstream.

## DECISION TREE

1. Is this clearly structural junk? (matches ignore criteria: spam, @BTC_Tick, routine scheduled market data, promotions, opinion threads without a triggering event)
   → YES: Choose 'ignore'
   → NO: Continue

2. Does it primarily discuss a specific topic area?
   → YES: Match to the most relevant category
   → NO: Choose the closest category based on dominant subject matter

3. If multiple categories could apply:
   → Choose the one that represents 60%+ of the content
   → If truly equal split, choose based on what a reader would search for

## EXAMPLES

"Tesla stock drops 5% after earnings report" → stocks
"Elon Musk tweets about Dogecoin" → crypto
"OpenAI releases GPT-5 with improved reasoning" → artificial intelligence
"New MacBook Pro features M4 chip" → technology
"Bitcoin reaches new all-time high" → crypto
"Bitcoin jumps 3% as traders respond to Fed rate decision" → crypto (categorizable event, even if modest)
"Fed raises interest rates by 0.25%" → politics
"Iran estimates $270 billion in war damage" → politics
"US airstrikes target Houthi positions in Yemen" → politics
"Call of Duty releases new battle pass" → video games
"LeBron James scores 40 points in playoff game" → sports
"Taylor Swift announces new album release date" → pop culture
"Netflix cancels popular series after two seasons" → pop culture
"Disney reveals new Olaf animatronic for theme park" → pop culture
"Universal Studios adds holographic effects to attraction" → pop culture
"Robotics lab develops new AI-powered humanoid robot" → technology
"Engineers create breakthrough in autonomous navigation" → artificial intelligence
"Coinbase reports record Q3 revenue of $2.3B" → stocks (corporate earnings — concrete news event)
"Top 100 24h Gainers: INJ $3.84 +13.12%, MORPHO $1.62 +7.00%... Top 100 24h Losers: NIGHT $0.0582 -4.26%..." → ignore (routine market data, no event)
"95.6% of Pump.fun wallets broke even or lost money. Only 0.4% ever made over $10K. The platform collected $950M in fees." → ignore
"Crypto Fear and Greed Index Value: 9, Sentiment: Extreme Fear, BTC Price: $68005" → ignore (routine index reading)
"Top DeFi Projects By TVL:" → ignore (routine TVL ranking, no event)
"Daily BTC dominance: 54.2%, ETH dominance: 17.8%" → ignore (routine scheduled metric)
"Top 10 cryptos by market cap this week" → ignore
"Random meme with no context" → ignore
"Incomplete sentence..." → ignore
"JUST IN : Bitcoin hits $67,000 @BTC_Tick" → ignore
"Circle minted another ~2.25 billion $USDC on Solana last week." → ignore (routine stablecoin op)
"My price target for BTC this cycle: $250K. Here's why 🧵" → ignore (speculative opinion, no news event)
"🐳 Whale Alert: 50,000 ETH moved from unknown wallet to Binance" → ignore (whale tracking without context)
"[Protocol X] has added [Token Y] as collateral. New yield: 4.2% APY" → ignore (minor routine protocol update)
"Thread: 5 reasons why [coin] is undervalued right now 🚀" → ignore (opinion thread, no news event)
"📢 AIRDROP LIVE: Connect wallet to claim your [Project] tokens" → ignore (promotion)
"SEC charges crypto exchange with securities fraud" → crypto (concrete news event, not a price move or opinion)

## OUTPUT FORMAT

Respond with ONLY valid JSON matching this format exactly:
{"category": "<category name>", "reasoning": "<1-2 sentence explanation of why this category was chosen over others>"}

Valid categories: crypto, politics, stocks, artificial intelligence, video games, sports, food, technology, music, fashion, pop culture, software development, fitness, ignore"""

# Duplicate detection thresholds (cosine similarity)
DUPLICATE_THRESHOLD = 0.95  # Exact duplicates only (>0.95 similarity)
SIMILARITY_THRESHOLD = 0.75  # Similar content - route to ignore channel

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
FEEDBACK_EXAMPLES_COUNT = 10  # Number of removed entries to include in system prompt

# Ignore-entry Learning Configuration
# Feeds entries categorized as 'ignore' (including user-recategorized ones) into the AI
# system prompt as additional negative examples. User-flagged ignores are prioritized.
IGNORE_EXAMPLES_ENABLED = True  # Enable learning from ignore-channel entries
IGNORE_EXAMPLES_COUNT = 15  # Number of recent ignore entries to include in system prompt


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

# Delete Message Context Menu Command (Right-click on bot messages → Apps → "Delete Message")
# Force-deletes any bot message from Discord without requiring a database entry.
# Useful for removing duplicate posts where only one has a DB mapping.
# Uses the same RECATEGORIZE_ALLOWED_USER_IDS for permission checks
DELETE_COMMAND_ENABLED = True

# Newsworthiness Filter Configuration
# Filters out mundane/routine news by rating surprise, impact, and actionability
# Only posts that score ABOVE the threshold will be posted to their category
# Posts below threshold go to the 'ignore' channel for review
SHORT_VIDEO_FILTER_ENABLED = True  # Enable/disable the short video filter
SHORT_VIDEO_THRESHOLD = 60  # Videos under this duration (seconds) are sent to ignore

# Audience Engagement Question Filter
# Filters entries that end with questions soliciting reader opinions (e.g. "Do you agree?", "Thoughts?")
AUDIENCE_QUESTION_FILTER_ENABLED = True

# ALL CAPS Capitalization Fix
# When enabled, entries detected as ALL CAPS are rewritten to proper
# sentence capitalization using Ollama before posting to Discord.
CAPS_FIX_ENABLED = True
CAPS_FIX_THRESHOLD = 0.65  # Ratio of uppercase letters to trigger rewrite (0.0-1.0)

NEWSWORTHINESS_FILTER_ENABLED = True  # Enable/disable the newsworthiness filter
NEWSWORTHINESS_THRESHOLD = 7.0  # Only post genuinely surprising or high-impact news (7+/10 "wow" tier)
NEWSWORTHINESS_WEIGHTS = {
    'surprising': 0.45,   # 45% weight - must be genuinely unexpected, not routine
    'impact': 0.35,       # 35% weight - must affect many people significantly
    'actionable': 0.20    # 20% weight - should prompt action or attention
}

# Entry Superseding Configuration
# When a new entry about the same story arrives, compare quality and replace
# the old Discord message if the new one is clearly better
SUPERSEDE_ENABLED = True
SUPERSEDE_MAX_AGE_HOURS = 24  # Don't supersede entries older than this

# Audio Transcription (Whisper via faster-whisper, runs 100% locally)
# Models are downloaded automatically to ~/.cache/huggingface/ on first use.
# Model options: "large-v3-turbo" (recommended), "medium", "base"
TRANSCRIPTION_ENABLED = False
TRANSCRIPTION_MODEL = "large-v3-turbo"  # ~3 GB RAM/VRAM; use "medium" if constrained
TRANSCRIPTION_DEVICE = "auto"           # "cuda", "cpu", or "auto" (detects GPU)
TRANSCRIPTION_MAX_DURATION = 300        # Skip videos longer than this (seconds)
