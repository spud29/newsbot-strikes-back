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
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')  # Required only when LLM_BACKEND = "openrouter"
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

# gallery-dl config path (explicit so it works under NSSM/LocalSystem,
# where ~ resolves to the system profile instead of the user's home)
GALLERY_DL_CONFIG = r"C:\Users\spud9\AppData\Roaming\gallery-dl\config.json"

# Dexerto tweet pair merger
# Dexerto posts stories as two tweets: tweet 1 = headline, tweet 2 = blurb + article URL.
# Tweet 1 is held in the dexerto_pending SQLite table until tweet 2 arrives (survives restarts).
# If no follow-up arrives within this window, tweet 1 is posted alone during the cleanup cycle.
DEXERTO_PENDING_MAX_AGE_HOURS = 1.0

# RSS Feed URLs
RSS_FEEDS = {
    "unusual_whales": "https://rss.app/feeds/MRsE23OX1FDxCdJ6.xml",
    "dexerto_twitter": "https://rss.app/feeds/jj6pbdE2H5AEwfeY.xml",
    "solana_floor": "https://rss.app/feeds/cJaLGwWKeTNniyhL.xml",
    "quiver_quant": "https://rss.app/feeds/yiVD4vcQbQ8i2HDs.xml",
    "degenerate_news": "https://rss.app/feeds/lJkV7xfSTsJOoYoD.xml",
    "watcher_guru": "https://rss.app/feeds/jQfpcfiYsZL0NwkI.xml",
    "newswire": "https://rss.app/feeds/DVrZpUnw9TZqLVNg.xml",
    "polymarket": "https://rss.app/feeds/iyFNm1K9DmiIS07m.xml"
}

# Discord Channel IDs for each category
# 2026-07-11 revamp: 'politics' split into 'us politics' + 'world news' (both keep
# the old politics channel); music/fashion/fitness/software development retired
# (folded into pop culture / general news / science & technology).
DISCORD_CHANNELS = {
    "crypto": 775513484221743124,
    "us politics": 1379921787629867138,
    "world news": 1379921787629867138,
    "general news": 1379921787629867138,
    "stocks": 854937605590220810,
    "artificial intelligence": 985273104483885137,
    "video games": 1317592652044046347,
    "sports": 845809605934317639,
    "food": 852256197494046731,
    "science & technology": 928462998228598794,
    "pop culture": 1432086691862024403,
    "ignore": 1344410355224547441
}

# All valid categories the AI can return (used for validation)
# This is separate from DISCORD_CHANNELS so the AI's correct answers
# aren't rejected just because a channel is disabled
VALID_CATEGORIES = [
    "crypto", "us politics", "world news", "stocks", "artificial intelligence",
    "video games", "sports", "food", "science & technology",
    "pop culture", "general news", "ignore"
]

# Default category for uncertain/unmatched content
DEFAULT_CATEGORY = "ignore"

# Fallback category when AI says 'ignore' and user un-ignores an entry via
# "Move to Channel" or /move. Must be a valid non-ignore category.
FALLBACK_CATEGORY = "general news"

# Pause Mode - When enabled, ALL entries are routed to the 'ignore' channel
# regardless of their AI categorization. The original category is preserved in reasoning.
# Useful for temporarily silencing all channels without changing any other config.
# This is the master kill switch: it overrides AUTO_POST_CATEGORIES.
# Disabled 2026-07-10: automation enabled with the calibrated reaction gate
# (see evals/calibrate_reaction_gate.py and PLAN.md status log).
PAUSE_MODE = True

# Categories allowed to auto-post when PAUSE_MODE is False (PLAN.md Phase 2/3).
# None  = every category auto-posts (full automation).
# list  = only the listed categories auto-post; everything else routes to the
#         ignore channel for manual review. Use this as the per-category
#         rollback lever if one category starts misbehaving.
AUTO_POST_CATEGORIES = None

# High-score rescue: when the AI says 'ignore' but the reaction-worthiness gate
# scores the entry at/above this threshold, re-categorize it (with 'ignore'
# excluded) and let it post. Catches interesting stories the categorizer's
# quality rules would otherwise bury (28% of AI ignores were user-rescued).
# Threshold calibrated with evals/calibrate_reaction_gate.py — keep it strict:
# junk flooding back into the feed defeats the point of the filter.
HIGH_SCORE_RESCUE_ENABLED = True
RESCUE_NEWSWORTHINESS_THRESHOLD = 7.5

# Flood guard: max non-ignore posts per rolling hour when auto-posting.
# Entries over the cap go to the ignore channel with a rate-limit reason.
MAX_AUTO_POSTS_PER_HOUR = 25

# Unified Channel Mode - route ALL non-ignore categories to a single Discord channel.
# Each message is prefixed with a bold [Category] tag so readers can filter by topic.
# 'ignore' entries still route to the ignore channel as normal.
UNIFIED_CHANNEL_MODE = True
UNIFIED_CHANNEL_ID = 1379921787629867138  # The single channel to post everything to

# URL shortening - don't shorten URLs already this short or shorter. A TinyURL is
# ~28 chars, so converting anything shorter just makes it LONGER and wastes an API
# call (which also burns through the free rate limit faster).
URL_SHORTEN_MIN_LENGTH = 30

# Telegram channels to monitor
TELEGRAM_CHANNELS = [
    "Fin_Watch",
    "news_crypto",
    "drops_analytics",
    "unfolded",
    "unfolded_defi",
    "infinityhedge"
]

# Ollama configuration
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_CATEGORIZATION_MODEL = "qwen3.5:9b"
OLLAMA_EMBEDDING_MODEL = "qwen3-embedding:0.6b"
# Context window for embedding requests. Kept small because embeddings run over short
# news text — at the 8192 default this model occupies ~2.9 GB of VRAM, but at 512 it
# needs only ~1.0 GB, letting it stay GPU-resident alongside the categorizer when
# OLLAMA_MAX_LOADED_MODELS=2. This avoids the model load/unload thrash that fragmented
# VRAM and stalled all processing for ~6.5 hours on 2026-06-28. Raise only if you also
# free VRAM elsewhere (e.g. a smaller categorizer or lower categorizer context).
OLLAMA_EMBEDDING_NUM_CTX = 512
# Context window for the categorization model — shared by categorize(), format_text(),
# verify_similarity(), compare_entries(), and rate_newsworthiness() in ollama_client.py.
# Ollama sizes a model's KV cache to num_ctx at load time: if any of these five call
# sites requested a different num_ctx for
# the same model, Ollama would reload/reallocate between calls — this exact churn
# fragmented VRAM and stalled all processing for ~6.5 hours on 2026-06-28 (see
# OLLAMA_EMBEDDING_NUM_CTX above; same failure mode, other model). Every one of the five
# call sites must pass this exact value.
# 16384 (vs. Ollama's 8192 implicit default) was chosen deliberately conservatively:
# qwen3.5:9b's weights alone (6.59 GB, Q4_K_M) already exceed qwen3:8b's entire old
# footprint at 8192 ctx, so headroom is tighter than before even at this value. Do not
# raise without first re-verifying VRAM headroom (`ollama ps` cross-checked against
# `nvidia-smi` — Ollama's self-reported free VRAM has been wrong on this machine before).
OLLAMA_CATEGORIZATION_NUM_CTX = 16384

# ---------------------------------------------------------------------------
# Categorization backend
# ---------------------------------------------------------------------------
# Which backend serves the *categorization* model — categorize(), format_text(),
# verify_similarity(), compare_entries() and rate_newsworthiness(). Embeddings are
# NOT affected: generate_embedding() always talks to local Ollama, so dedup vectors
# stay identical and the database never needs a reset.
#
#   "ollama"     — local qwen3.5:9b (the original setup)
#   "openrouter" — hosted API, frees ~7-9 GB of VRAM on this machine
#
# Switching back to "ollama" is a one-line rollback + restart. qwen3.5:9b stays
# installed for exactly that reason; it just isn't kept loaded. Note the fallback is
# manual — nothing auto-reverts if OpenRouter has an outage. On a hosted outage
# categorize() returns its "Error during categorization" fallback, which main.py
# already treats as "skip this entry, retry next cycle".
#
# After cutting over, set OLLAMA_MAX_LOADED_MODELS=1 on the host (env var, needs an
# Ollama restart) so only the embedder stays resident. qwen3.5:9b unloads on its own
# 30 minutes after the last categorize call.
LLM_BACKEND = "ollama"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Pin an explicit model id — a retired model surfaces as a 404 that health_check()
# catches at startup rather than mid-poll.
#
# Verified live on OpenRouter 2026-07-26: $0.10/M input, $0.40/M output, 1M context,
# supports response_format. Note gemini-2.0-flash was RETIRED from OpenRouter — this is
# its same-price successor. Cheaper alternatives if quality holds: google/gemma-3-12b-it
# ($0.05/$0.15). More capable if it doesn't: google/gemini-2.5-flash ($0.30/$2.50).
OPENROUTER_CATEGORIZATION_MODEL = "google/gemini-2.5-flash-lite"
# Per-attempt request timeout (seconds). Retries/backoff are handled by
# _request_generate; the OpenAI SDK's own retries are disabled so attempt counting
# lives in exactly one place.
OPENROUTER_TIMEOUT = 120
# Optional attribution headers shown on openrouter.ai/rankings. Harmless to leave.
OPENROUTER_APP_NAME = "newsbot"
OPENROUTER_APP_URL = "https://github.com/spud29/newsbot"

# COST NOTE — the per-call payload is the *enhanced* system prompt, not the ~4.3k-token
# SYSTEM_PROMPT below. The feedback layers append up to 76 examples
# (FEEDBACK_EXAMPLES_COUNT + IGNORE_EXAMPLES_COUNT + CORRECTION_EXAMPLES_COUNT +
# IGNORE_PROMOTION_EXAMPLES_COUNT + IGNORE_RESCUE_EXAMPLES_COUNT, all further down this
# file), pushing each categorize() call to ~8.5k input tokens. At ~180 entries/day that
# is the single largest line on the bill — trim those counts first if cost needs to come
# down. That prefix is byte-stable for an hour (see _cache_ttl in ollama_client.py), so
# cached reads at $0.01/M should absorb much of it; measure on the dashboard rather than
# assuming. Budget ~$6/mo uncached.

# System prompt for categorization
SYSTEM_PROMPT = """You are an expert news categorization assistant. Your task is to analyze content and assign it to exactly ONE category with high precision.

## AVAILABLE CATEGORIES

### crypto
- Cryptocurrencies (Bitcoin, Ethereum, altcoins, etc.)
- Blockchain technology and applications
- NFTs, DeFi, Web3, DAOs
- Crypto exchanges, trading, regulations
- Crypto market analysis and price movements

### us politics
- US federal, state, and local politics: White House, Congress, governors, city halls
- US elections, campaigns, polling, and political parties
- US courts and legal cases with a political angle (Supreme Court, federal enforcement actions)
- US legislation, regulation, and executive actions
- US economic policy: Fed decisions, tariffs, taxes, budget, debt ceiling
- US political figures, appointments, and controversies
- US social issues, protests, and activism
- Use 'general news' for breaking US events that aren't driven by government, policy, or ideology

### world news
- International relations, diplomacy, and geopolitics
- War, military conflict, and armed operations anywhere in the world
- Foreign governments, elections, and political events outside the US
- International organizations and alliances (UN, NATO, EU)
- US involvement abroad counts as world news: airstrikes, negotiations, treaties, sanctions, troop deployments — if the story is about the US acting in the world, it goes here; if it's about US domestic governance, use 'us politics'
- Foreign protests, unrest, and political crises

### general news
- Natural disasters, extreme weather events, and accidents
- Crime, law enforcement, and court cases without a political angle (including recreational drug busts, trafficking, and other drug-related crime)
- Public health and medical news: outbreaks, hospital incidents, drug-use statistics and societal trends — NOT pharma R&D, clinical trials, or FDA drug approvals (see 'science & technology')
- Fitness, wellness, and lifestyle trends (workout culture, dieting trends, wellness influencers)
- Human interest and society: local events, viral real-world stories
- Consumer and economic news affecting everyday people (inflation, housing, jobs) without a direct government policy angle

### stocks
- Stock market movements and indices
- Individual company stock performance
- IPOs, mergers, acquisitions
- Traditional finance and banking
- Investment strategies and market analysis
- Corporate earnings and financial reports
- Company news with the potential to influence the stock price, even when no price move is reported yet: lawsuits involving a public company, product launches and recalls, executive changes, regulatory action against a company, major partnerships or breakups
- When a story about a public company could fit a topical category (food, tech, entertainment) but its significance is what it means for the company, prefer 'stocks'

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
- Nutrition and dietary topics
- Cooking techniques and cuisines
- Corporate actions by public food companies (M&A, lawsuits, earnings) follow the stocks rule — use 'stocks' when the significance is what it means for the company

### science & technology
- General tech products and gadgets
- Software updates and releases
- Tech company news (that isn't AI/crypto specific)
- Internet services and platforms
- Cybersecurity and privacy
- Hardware, electronics, consumer tech
- Space exploration and space technology: missions, launches, astronomy, and discoveries
- Scientific discoveries and research (non-AI): physics, biology, chemistry, archaeology, climate data
- Pharmaceutical and medical research: drug development, clinical trials, and FDA drug approvals — NOT recreational drug crime, drug policy, or drug-use statistics (see 'general news'/'us politics')
- Software development: programming languages, frameworks, developer tools, open source projects, APIs and developer platforms

### pop culture
- Celebrity news and entertainment gossip
- Movies, TV shows, and streaming content
- Pop culture trends and viral moments
- Awards shows and entertainment events
- Celebrity social media and controversies
- Entertainment industry news (Hollywood, actors, directors)
- Reality TV and popular culture phenomena
- Influencers and internet personalities
- Music: releases, artist news, concerts, tours, festivals, streaming platforms
- Fashion: shows, designers, brand updates, style trends

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
- Quality/completeness failures: bare headlines with no additional context beyond the headline text, first-report fragments that a more complete entry will supersede, entries that state a single fact with no elaboration or significance explained
- Lesser duplicates: when a story is covered by a brief entry and a more detailed entry, the brief one should be ignored even if the topic is legitimately newsworthy — the topic matters, but this specific entry doesn't add enough
- Minor routine announcements: small protocol updates, routine exchange token additions, minor product features from well-known companies with no broader significance (e.g. "Coinbase adds [token] to [platform]" with nothing else)

## CATEGORIZATION GUIDELINES

1. **Read Carefully**: Analyze the entire content, not just keywords
2. **Primary Topic**: Choose the category that represents the PRIMARY focus
3. **Be Specific**: If content spans multiple categories, pick the most dominant one. If the content genuinely has strong relevance to a second category (e.g., a crypto regulation story is both 'crypto' and 'us politics'), include a secondary_category. Most entries should NOT have one — only assign it when a reader in the second category would genuinely want to see this entry. Never set secondary_category to 'ignore' or to the same value as category.
4. **Newsworthiness is Downstream**: Once an entry has enough substance to stand alone (passes the quality check), assign the correct category even if the event seems modest. The newsworthiness filter evaluates impact AFTER categorization — don't pre-filter modest-but-real events at this stage.
5. **Context Clues**: Consider source, tone, and depth of information
6. **When Unclear**: If the content discusses a real topic but lacks sufficient context or detail to stand alone as a worthwhile post, 'ignore' is appropriate — it's a quality filter, not just a spam filter. Assign a real category when the entry has enough substance to stand alone.
7. **Entertainment Context**: Theme parks, entertainment venues, and entertainment-focused technology (Disney animatronics, movie theater tech, concert staging) should be categorized as **pop culture**, NOT science & technology. Consider the PRIMARY CONTEXT: Is this entertainment news or tech industry news?
8. **Drug & Pharma Boundary**: Pharmaceutical R&D, clinical trial results, and FDA drug approvals/regulatory actions belong in **science & technology** — medical research is science. Recreational drug crime (busts, trafficking, smuggling), drug policy/legislation/court rulings, and drug-use statistics or societal trends stay in **general news**, **us politics**, or **world news** per their normal rules; do NOT move them just because the word "drug" appears. If the story is primarily framed as a stock-price reaction to trial results, prefer **stocks** over science & technology.
9. **Structural vs. Thin vs. Modest**: Three distinct cases — (a) structural junk (routine scheduled data, no triggering event) → always ignore; (b) thin/bare entry about a real event (single headline with no context, no elaboration) → ignore, a better entry will follow; (c) modest but real event WITH some context ("Bitcoin up 2% after Fed rate hold") → categorize as 'crypto', the newsworthiness filter handles impact downstream.

## DECISION TREE

1. Is this clearly structural junk? (matches ignore criteria: spam, @BTC_Tick, routine scheduled market data, promotions, opinion threads without a triggering event)
   → YES: Choose 'ignore'
   → NO: Continue

2. Does this entry have enough substance to stand alone as a post?
   → Too brief / bare headline with no context or elaboration: Choose 'ignore'
   → Has meaningful detail, context, or impact beyond just the headline: Continue

3. Does it primarily discuss a specific topic area?
   → YES: Match to the most relevant category
   → NO: Choose the closest category based on dominant subject matter

4. If multiple categories could apply:
   → Choose the one that represents 60%+ of the content
   → If truly equal split, choose based on what a reader would search for

## EXAMPLES

"Tesla stock drops 5% after earnings report" → stocks
"Elon Musk tweets about Dogecoin" → crypto
"OpenAI releases GPT-5 with improved reasoning" → artificial intelligence
"New MacBook Pro features M4 chip" → science & technology
"Bitcoin reaches new all-time high" → crypto
"Bitcoin jumps 3% as traders respond to Fed rate decision" → crypto (categorizable event, even if modest)
"Fed raises interest rates by 0.25%" → us politics
"Senate passes budget bill after all-night session" → us politics
"7.8 magnitude earthquake strikes Turkey, rescue operations underway" → general news
"CDC confirms new measles outbreak across 4 states" → general news
"Man arrested after largest bank heist in US history" → general news
"NASA's James Webb captures image of earliest known galaxy" → science & technology
"UK Parliament debates new immigration policy" → world news
"Iran estimates $270 billion in war damage" → world news
"US airstrikes target Houthi positions in Yemen" → world news (US acting abroad, not domestic governance)
"Kremlin says Trump and Putin held a 90-minute call on ending the Ukraine war" → world news
"Call of Duty releases new battle pass" → video games
"LeBron James scores 40 points in playoff game" → sports
"Taylor Swift announces new album release date" → pop culture
"Netflix cancels popular series after two seasons" → pop culture
"Disney reveals new Olaf animatronic for theme park" → pop culture
"Universal Studios adds holographic effects to attraction" → pop culture
"Robotics lab develops new AI-powered humanoid robot" → science & technology
"Engineers create breakthrough in autonomous navigation" → artificial intelligence
"Coinbase reports record Q3 revenue of $2.3B" → stocks (corporate earnings — concrete news event)
"Apple, $AAPL, files a lawsuit against OpenAI alleging trade secret theft" → stocks (company news with stock-price implications)
"Danone sues Chobani, alleging its protein claims were vastly inflated" → stocks (public-company legal dispute, not culinary news)
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

Quality vs. thin coverage — same story, different substance:
"US Treasury Secretary Bessent says the US has seized $450 million in Iranian cryptocurrency." → ignore (bare headline, no context about the operation, motive, or broader significance)
"U.S. Treasury seizes nearly $500M in Iranian crypto as part of Operation Economic Fury, targeting sanctions evasion networks across Asia." → world news (named operation, context, significance explained; US acting abroad)

"The Fed Leaves Rates Unchanged." → ignore (single bare fact with no context, analysis, or market reaction — not enough to post)
"Fed holds rates steady as Powell signals caution on inflation outlook despite White House pressure for cuts." → us politics (substantive framing that gives the post meaning)

"7.4 MAGNITUDE EARTHQUAKE HITS NORTHERN JAPAN" → ignore (bare headline with no casualties, damage, or significance — a more complete entry will likely follow)
"7.4 earthquake strikes northern Japan; tsunami warning issued for coastal areas, 12 confirmed dead, rescue operations underway." → general news (has the context and impact needed to stand alone)

"Charles Schwab begins rollout of spot Bitcoin, Ethereum trading platform" → ignore (thin single-line announcement with no details about the platform, timeline, or user impact)
"Coinbase adds tGBP stablecoin to expand UK crypto payments" → crypto (specific product with a specific market, clear significance)

Drug & pharma boundary — same topic, different category depending on framing:
"Eli Lilly says its experimental obesity drug cut sleep apnea severity by 60.6% in a Phase 3 trial." → science & technology (pharma R&D / clinical trial result)
"FDA unveils Operation TrialBlazer, a new effort to speed up U.S. drug development." → science & technology (FDA regulatory/approval process, not law enforcement)
"AstraZeneca and Daiichi are reportedly nearing a UK pricing deal for breast cancer drug Enhertu." → science & technology (drug development/commercialization, not a stock-price move)
"Alibaba agrees to pay $600 million to resolve U.S. allegations over illegal drug sales." → us politics (US legal enforcement action, not medical research)
"Supreme Court rules drug users & addicts cannot automatically be barred from owning firearms." → us politics (legal ruling, not medical research)
"UN warns of an unprecedented spike in synthetic drugs as cocaine and meth surge worldwide." → general news (drug-use statistics/societal trend, not pharma R&D)
"Psychedelic drugmaker Definium surges +53% after late-stage data showed strong results for its depression treatment." → stocks (stock-price-move framing wins over the drug-trial content)

## OUTPUT FORMAT

Respond with ONLY valid JSON matching this format exactly:
{"category": "<category name>", "reasoning": "<1-2 sentence explanation of why this category was chosen over others>", "secondary_category": "<category name or null>"}

secondary_category is optional. Set it to a second category name ONLY when the content genuinely spans two categories with roughly equal relevance. Set to null when the entry fits cleanly in one category (which is most of the time).

Valid categories: crypto, us politics, world news, stocks, artificial intelligence, video games, sports, food, science & technology, pop culture, general news, ignore"""

# Duplicate detection thresholds (cosine similarity)
DUPLICATE_THRESHOLD = 0.95  # Exact duplicates only (>0.95 similarity)
SIMILARITY_THRESHOLD = 0.75  # Similar content - route to ignore channel

# Database path (single SQLite file)
DB_PATH = "data/newsbot.db"

# Polling interval (seconds)
POLL_INTERVAL = 300  # 5 minutes

# Embedding retention period (hours) — the dedup window. Embeddings are the
# expensive rows (full content + vector JSON), so this stays short.
DB_RETENTION_HOURS = 48

# processed_ids retention — deliberately much longer than the embedding window.
# These rows are tiny (id + timestamp), and pruning them at 48h opened a re-post
# hole: rss.app feeds retain items by count, so on a quiet feed an item older
# than 48h lost BOTH its "seen" flag and its dedup embedding at the same moment
# and was posted again. 30 days comfortably outlives any feed's item list.
PROCESSED_IDS_RETENTION_HOURS = 720  # 30 days

# Message-mapping retention (days). Bounds table growth (~170 rows/day). Pruning drops
# the Discord<->entry mapping for older posts, so Entry Info / Re-categorize / reaction
# tracking stop working on posts older than this window. Kept generous: the learning
# queries only look back 7 days, so this doesn't affect them.
MESSAGE_MAPPING_RETENTION_DAYS = 365

# Freshness guard for RSS entries: skip feed items whose pub_date is older than
# this, regardless of DB state. Belt-and-braces with the retention split above —
# it removes the dependency on rss.app's (undocumented) item-retention behavior.
# Items with missing/unparseable dates are processed normally.
RSS_MAX_ENTRY_AGE_HOURS = 24

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

# Reaction-Gate Feedback Learning
# Entries the gate passed but the user demoted to ignore are injected into the
# rate_newsworthiness prompt as "score content like this low" examples, so the
# gate keeps learning the user's taste from their corrections. Refreshes hourly.
GATE_FEEDBACK_EXAMPLES_ENABLED = True
GATE_FEEDBACK_EXAMPLES_COUNT = 8

# Ignore-entry Learning Configuration
# Feeds entries categorized as 'ignore' (including user-recategorized ones) into the AI
# system prompt as additional negative examples. User-flagged ignores are prioritized.
IGNORE_EXAMPLES_ENABLED = True  # Enable learning from ignore-channel entries
IGNORE_EXAMPLES_COUNT = 10  # Number of recent ignore entries to include in system prompt

# Correction Learning Configuration
# Feeds user re-categorization corrections into the AI system prompt so it learns from
# mistakes. Covers both category-to-category corrections and entries moved TO ignore.
CORRECTION_EXAMPLES_ENABLED = True  # Enable learning from user re-categorizations
CORRECTION_EXAMPLES_COUNT = 30      # Category-to-category corrections (AI said X, user changed to Y)
IGNORE_PROMOTION_EXAMPLES_COUNT = 6  # Entries the user moved TO ignore (AI said X, should be ignore)
IGNORE_RESCUE_ENABLED = True         # Learn from AI-assigned ignores that users promoted to real categories
IGNORE_RESCUE_EXAMPLES_COUNT = 20   # Max ignore-rescue examples to include in system prompt

# Automated Accuracy Report
# The bot posts the categorization accuracy report (accuracy_report.py) to Discord
# on a schedule, so graduation decisions (see PLAN.md) don't depend on remembering
# to run the script manually.
ACCURACY_REPORT_ENABLED = True
ACCURACY_REPORT_INTERVAL_DAYS = 7   # How often to post; also the report's look-back window
ACCURACY_REPORT_HOUR = 9            # Local hour of day to post (0-23)
ACCURACY_REPORT_CHANNEL_ID = DISCORD_CHANNELS["ignore"]  # Where to post the report


# Re-categorize Context Menu Command (Right-click on bot messages → Apps → "Re-categorize")
# Note: This is a restricted command that only specific users can access
RECATEGORIZE_COMMAND_ENABLED = True  # Enable/disable "Re-categorize" context menu command
RECATEGORIZE_ALLOWED_USER_IDS = [144983485268885504]  # Discord user IDs allowed to re-categorize entries
REASON_MODAL_ENABLED = False  # Show the "Why this change?" modal; set False to skip it

# Source Context Menu Command (Right-click on bot messages → Apps → "Source")
# Shows the original Telegram/Twitter source URL to the user who triggered it
SOURCE_COMMAND_ENABLED = True

# Edit Text Context Menu Command (Right-click on bot messages → Apps → "Edit Text")
# Allows authorized users to edit the text of a posted entry
# Uses the same RECATEGORIZE_ALLOWED_USER_IDS for permission checks
EDIT_TEXT_COMMAND_ENABLED = True

# "Remove Images" context menu command - strips image attachments from a bot message, keeping text
# Uses the same RECATEGORIZE_ALLOWED_USER_IDS for permission checks
REMOVE_IMAGES_COMMAND_ENABLED = True

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

NEWSWORTHINESS_FILTER_ENABLED = True  # Enable/disable the reaction-worthiness gate
# Default cutoff. Calibrated 2026-07-10 against 30 days of user move/leave decisions
# (evals/runs/reaction_calibration_20260710T203505Z.json): at 6.0 the gate passes
# 71% of user-approved entries and blocks 47% of user-left ones — the best
# precision knee for a "reaction-worthy" channel. The weekly accuracy report's
# REACTION GATE section re-derives the best threshold as cleaner data accumulates.
NEWSWORTHINESS_THRESHOLD = 6.0
# Per-category threshold overrides (categories not listed use NEWSWORTHINESS_THRESHOLD)
NEWSWORTHINESS_THRESHOLD_BY_CATEGORY = {}
# 2026-07-21 rebalance: impact promoted from 0.30 to the dominant weight (0.45),
# surprising cut 0.40->0.30, talkability 0.30->0.25. Diagnosis showed the gate was
# passing viral human-interest oddities (parkour seniors, VTuber stunts, quirky
# curiosities) at 6-8 because they max out surprising+talkability while having no
# real stakes. Weighting impact highest makes "no broader stakes" pull the score
# down even when a story is surprising and shareable. Pairs with the HUMAN-INTEREST
# & SPECTACLE RULE in rate_newsworthiness (ollama_client.py). Must sum to 1.0.
NEWSWORTHINESS_WEIGHTS = {
    'surprising': 0.30,   # would a reader stop scrolling?
    'impact': 0.45,       # real stakes — money, power, what things cost
    'talkability': 0.25   # would someone share it, argue about it, react to it?
}

# Entry Superseding Configuration
# When a new entry about the same story arrives, compare quality and replace
# the old Discord message if the new one is clearly better
SUPERSEDE_ENABLED = False
SUPERSEDE_MAX_AGE_HOURS = 24  # Don't supersede entries older than this
SUPERSEDED_CHANNEL_ID = 1344412433552248973  # Channel to archive superseded entries
SUPERSEDE_SIMILARITY_THRESHOLD = 0.85  # Min similarity to even attempt supersede (above SIMILARITY_THRESHOLD)

# Audio Transcription (Whisper via faster-whisper, runs 100% locally)
# Models are downloaded automatically to ~/.cache/huggingface/ on first use.
# Model options: "large-v3-turbo" (recommended), "medium", "base"
TRANSCRIPTION_ENABLED = False
TRANSCRIPTION_MODEL = "large-v3-turbo"  # ~3 GB RAM/VRAM; use "medium" if constrained
TRANSCRIPTION_DEVICE = "auto"           # "cuda", "cpu", or "auto" (detects GPU)
TRANSCRIPTION_MAX_DURATION = 300        # Skip videos longer than this (seconds)


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
# Default INFO keeps routing/decisions/errors visible without DEBUG sludge.
# Override at runtime with env var LOG_LEVEL=DEBUG when diagnosing an issue.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = os.getenv("LOG_FILE", "bot.log")
LOG_TO_CONSOLE = True
# Rotating app log: ~5 MB x 5 backups ≈ 25 MB max for bot.log*
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(5 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))
# Keep discord/telethon/http libraries quiet unless LOG_LEVEL=DEBUG
LOG_QUIET_LIBRARIES = True
LOG_LIBRARY_LEVEL = os.getenv("LOG_LIBRARY_LEVEL", "WARNING").upper()
# Service/stdout logs written by NSSM (separate from bot.log)
SERVICE_STDOUT_LOG = "service_stdout.log"
SERVICE_STDERR_LOG = "service_stderr.log"
# Cap retained size when trimming legacy/overgrown service logs
SERVICE_LOG_MAX_BYTES = int(os.getenv("SERVICE_LOG_MAX_BYTES", str(10 * 1024 * 1024)))
