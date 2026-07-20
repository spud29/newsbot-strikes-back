"""
Ollama API client for categorization and embeddings
"""
import requests
import time
import json
import re
from utils import logger, retry_with_backoff, fix_sentence_capitalization, capitalize_known_news_sources
import config

# Known LLM abbreviations and alternate forms mapped to canonical category names.
# Keys are lowercased; values are entries in VALID_CATEGORIES.
# Runs AFTER exact match and BEFORE partial match inside _parse_category.
_CATEGORY_ALIASES = {
    "ai":                "artificial intelligence",
    "ml":                "artificial intelligence",
    "machine learning":  "artificial intelligence",
    "gaming":            "video games",
    "games":             "video games",
    "tech":                   "science & technology",
    "technology":             "science & technology",
    "science":                "science & technology",
    "sci tech":               "science & technology",
    "science technology":     "science & technology",
    "science&technology":     "science & technology",
    "science and technology": "science & technology",
    "sciencetechnology":      "science & technology",
    # Retired categories (2026-07-11 revamp) mapped to their absorbers so old
    # labels in learning examples and stale LLM habits still parse.
    "software":             "science & technology",
    "software development": "science & technology",
    "dev":                  "science & technology",
    "development":          "science & technology",
    "music":                "pop culture",
    "fashion":              "pop culture",
    "fitness":              "general news",
    # Old unified 'politics' label; US domestic was its larger half.
    "politics":          "us politics",
    "us political":      "us politics",
    "american politics": "us politics",
    "domestic politics": "us politics",
    "geopolitics":       "world news",
    "international":     "world news",
    "world":             "world news",
    "world politics":    "world news",
    "foreign affairs":   "world news",
    "news":              "general news",
    "general":           "general news",
    "culture":           "pop culture",
    "entertainment":     "pop culture",
    "celebrity":         "pop culture",
    "finance":           "stocks",
    "stock":             "stocks",
    "blockchain":        "crypto",
    "web3":              "crypto",
    "web 3":             "crypto",
    "cryptocurrency":    "crypto",
    "cryptocurrencies":  "crypto",
    "nft":               "crypto",
    "defi":              "crypto",
    "sport":             "sports",
    "film":              "pop culture",
    "movies":            "pop culture",
    "television":        "pop culture",
}

# Ollama has no tokenize endpoint and pulling in a real tokenizer just for a warning
# log isn't worth it, so this rough chars-per-token ratio is enough to catch a runaway
# prompt before it silently overflows the model's context window (Ollama/llama.cpp
# shifts older tokens out rather than erroring, so overflow fails silently otherwise).
_CHARS_PER_TOKEN_ESTIMATE = 4


def _estimate_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN_ESTIMATE


class OllamaClient:
    """Client for interacting with local Ollama API"""
    
    def __init__(self, removed_entries_db=None, database=None):
        """
        Initialize Ollama client

        Args:
            removed_entries_db: Optional RemovedEntriesDB instance for feedback learning
            database: Optional Database instance for ignore-entry learning
        """
        self.base_url = config.OLLAMA_BASE_URL
        self.categorization_model = config.OLLAMA_CATEGORIZATION_MODEL
        self.categorization_num_ctx = config.OLLAMA_CATEGORIZATION_NUM_CTX
        self.embedding_model = config.OLLAMA_EMBEDDING_MODEL
        self.embedding_num_ctx = config.OLLAMA_EMBEDDING_NUM_CTX
        self.removed_entries_db = removed_entries_db
        self.database = database

        # Cache for enhanced system prompt (refreshed every hour)
        self._enhanced_prompt_cache = None
        self._cache_timestamp = 0
        self._cache_ttl = 3600  # 1 hour

        # Cache for the reaction-gate feedback block (user demotions as negative examples)
        self._gate_feedback_cache = None
        self._gate_feedback_timestamp = 0

        logger.info(f"Ollama client initialized: {self.base_url}")
    
    def generate_enhanced_system_prompt(self):
        """
        Generate enhanced system prompt with feedback from removed entries
        
        Returns:
            str: Enhanced system prompt with negative examples
        """
        # Check cache
        current_time = time.time()
        if (self._enhanced_prompt_cache and 
            (current_time - self._cache_timestamp) < self._cache_ttl):
            return self._enhanced_prompt_cache
        
        # Start with base system prompt
        enhanced_prompt = config.SYSTEM_PROMPT
        
        # Add feedback learning if enabled and database is available
        if (config.FEEDBACK_LEARNING_ENABLED and 
            self.removed_entries_db and 
            hasattr(self.removed_entries_db, 'get_content_previews')):
            
            try:
                # Get recent removed entries as negative examples
                previews = self.removed_entries_db.get_content_previews(
                    limit=config.FEEDBACK_EXAMPLES_COUNT,
                    max_preview_length=150
                )
                
                if previews:
                    # Add negative examples section
                    enhanced_prompt += "\n\n" + "=" * 60
                    enhanced_prompt += "\nIMPORTANT: Based on user feedback, the following types of content should be categorized as 'ignore':\n\n"
                    
                    for i, preview in enumerate(previews, 1):
                        # Clean preview for prompt (remove newlines, excessive spaces)
                        clean_preview = " ".join(preview.split())
                        enhanced_prompt += f"{i}. {clean_preview}\n"
                    
                    enhanced_prompt += "\n" + "=" * 60
                    enhanced_prompt += "\nNote that this list represents what was filtered — do not over-apply. Content with a clear category should still be categorized, even if modest in impact."
                    
                    logger.debug(f"Enhanced system prompt with {len(previews)} negative examples")
                else:
                    logger.debug("No removed entries available for feedback learning")
            
            except Exception as e:
                logger.error(f"Error generating enhanced system prompt: {e}", exc_info=True)
        
        # Add ignore-entry learning if enabled and database is available
        if (getattr(config, 'IGNORE_EXAMPLES_ENABLED', True) and
                self.database and
                hasattr(self.database, 'get_ignore_entry_previews')):

            try:
                ignore_previews = self.database.get_ignore_entry_previews(
                    limit=getattr(config, 'IGNORE_EXAMPLES_COUNT', 30),
                    max_preview_length=150
                )

                if ignore_previews:
                    enhanced_prompt += "\n\n" + "=" * 60
                    enhanced_prompt += "\nThe following content has been routed to the ignore channel. Use these as additional guidance for what to categorize as 'ignore':\n\n"

                    for i, (preview, user_flagged) in enumerate(ignore_previews, 1):
                        clean_preview = " ".join(preview.split())
                        flag = " (user-flagged)" if user_flagged else ""
                        enhanced_prompt += f"{i}. {clean_preview}{flag}\n"

                    enhanced_prompt += "\n" + "=" * 60
                    enhanced_prompt += "\nPay particular attention to entries marked '(user-flagged)' — a human explicitly moved those to ignore."

                    logger.debug(f"Enhanced system prompt with {len(ignore_previews)} ignore-channel examples")
                else:
                    logger.debug("No ignore entries available for learning")

            except Exception as e:
                logger.error(f"Error adding ignore entries to system prompt: {e}", exc_info=True)

        # Add category-correction examples (AI said X, user changed to Y)
        if (getattr(config, 'CORRECTION_EXAMPLES_ENABLED', True) and
                self.database and
                hasattr(self.database, 'get_recategorization_examples')):

            try:
                corrections = self.database.get_recategorization_examples(
                    limit=getattr(config, 'CORRECTION_EXAMPLES_COUNT', 15),
                    max_preview_length=150
                )

                if corrections:
                    enhanced_prompt += "\n\n" + "=" * 60
                    enhanced_prompt += "\nKNOWN CATEGORIZATION CORRECTIONS — the AI was wrong about these. Learn from them:\n\n"

                    # Group by (original, corrected) pair so repeated patterns surface as
                    # frequency-weighted signals rather than redundant identical lines.
                    correction_groups = {}
                    for preview, orig_cat, corrected_cat, user_reason in corrections:
                        key = (orig_cat, corrected_cat)
                        if key not in correction_groups:
                            correction_groups[key] = {'count': 0, 'example': preview, 'reason': None}
                        correction_groups[key]['count'] += 1
                        if user_reason and not correction_groups[key]['reason']:
                            correction_groups[key]['reason'] = user_reason

                    sorted_corrections = sorted(
                        correction_groups.items(),
                        key=lambda x: -x[1]['count']
                    )

                    for i, ((orig_cat, corrected_cat), info) in enumerate(sorted_corrections, 1):
                        count = info['count']
                        count_str = f"({count}×) " if count > 1 else ""
                        clean_preview = " ".join(info['example'].split())
                        enhanced_prompt += (
                            f"{i}. {count_str}AI said '{orig_cat}' → correct category is '{corrected_cat}': {clean_preview}\n"
                        )
                        if info['reason']:
                            enhanced_prompt += f"   User's reason: \"{info['reason']}\"\n"

                    enhanced_prompt += "\n" + "=" * 60
                    enhanced_prompt += "\nWhen content resembles the examples above, choose the corrected category shown, not the AI's original guess."

                    logger.debug(f"Enhanced system prompt with {len(sorted_corrections)} correction patterns ({len(corrections)} examples)")
                else:
                    logger.debug("No category-correction examples available")

            except Exception as e:
                logger.error(f"Error adding correction examples to system prompt: {e}", exc_info=True)

        # Add ignore-promotion examples (AI posted it, user moved it to ignore)
        if (getattr(config, 'CORRECTION_EXAMPLES_ENABLED', True) and
                self.database and
                hasattr(self.database, 'get_ignore_promotion_examples')):

            try:
                promotions = self.database.get_ignore_promotion_examples(
                    limit=getattr(config, 'IGNORE_PROMOTION_EXAMPLES_COUNT', 10),
                    max_preview_length=150
                )

                if promotions:
                    enhanced_prompt += "\n\n" + "=" * 60
                    enhanced_prompt += "\nENTRIES MOVED TO IGNORE BY USER — the AI posted these but they should have been ignored:\n\n"

                    for i, (preview, orig_cat, user_reason) in enumerate(promotions, 1):
                        clean_preview = " ".join(preview.split())
                        enhanced_prompt += f"{i}. (AI said '{orig_cat}'): {clean_preview}\n"
                        if user_reason:
                            enhanced_prompt += f"   User's reason: \"{user_reason}\"\n"

                    enhanced_prompt += "\n" + "=" * 60
                    enhanced_prompt += "\nThese were edge cases the user felt crossed a quality line. If content has a clear category and a real news hook, categorize it; reserve 'ignore' for structural junk."

                    logger.debug(f"Enhanced system prompt with {len(promotions)} ignore-promotion examples")
                else:
                    logger.debug("No ignore-promotion examples available")

            except Exception as e:
                logger.error(f"Error adding ignore-promotion examples to system prompt: {e}", exc_info=True)

        # Add ignore-rescue examples (AI said ignore, user promoted to real category)
        if (getattr(config, 'IGNORE_RESCUE_ENABLED', True) and
                self.database and
                hasattr(self.database, 'get_ignore_rescue_examples')):

            try:
                rescues = self.database.get_ignore_rescue_examples(
                    limit=getattr(config, 'IGNORE_RESCUE_EXAMPLES_COUNT', 10),
                    max_preview_length=150
                )

                if rescues:
                    enhanced_prompt += "\n\n" + "=" * 60
                    enhanced_prompt += "\nCONTENT INCORRECTLY IGNORED — AI said 'ignore' but users promoted these to real categories. Don't miss similar content:\n\n"

                    for i, (preview, correct_cat) in enumerate(rescues, 1):
                        clean_preview = " ".join(preview.split())
                        enhanced_prompt += f"{i}. (should be '{correct_cat}'): {clean_preview}\n"

                    enhanced_prompt += "\n" + "=" * 60
                    enhanced_prompt += "\nWhen content resembles the examples above, assign the correct category instead of 'ignore'."

                    logger.debug(f"Enhanced system prompt with {len(rescues)} ignore-rescue examples")
                else:
                    logger.debug("No ignore-rescue examples available")

            except Exception as e:
                logger.error(f"Error adding ignore-rescue examples to system prompt: {e}", exc_info=True)

        # Warn well before this shares the categorization context window with per-item
        # content and output — leave that room by flagging use above 80% of the window.
        ctx_window = self.categorization_num_ctx
        token_estimate = _estimate_tokens(enhanced_prompt)
        warn_budget = ctx_window * 0.8
        if token_estimate > warn_budget:
            logger.warning(
                f"Enhanced system prompt is ~{token_estimate} tokens "
                f"(~{token_estimate / ctx_window:.0%} of the "
                f"{ctx_window}-token context window) — little room left for "
                f"per-item content and output. Consider lowering the *_EXAMPLES_COUNT settings "
                f"in config.py."
            )
        else:
            logger.debug(f"Enhanced system prompt is ~{token_estimate} tokens")

        # Cache the enhanced prompt
        self._enhanced_prompt_cache = enhanced_prompt
        self._cache_timestamp = current_time

        return enhanced_prompt
    
    @retry_with_backoff(max_retries=4, initial_delay=3)
    def categorize(self, content, exclude_categories=None):
        """
        Categorize content using Ollama
        
        Args:
            content: Text content to categorize
            exclude_categories: List of category names to exclude from results
        
        Returns:
            tuple: (category_name, reasoning) where reasoning is a brief explanation
        """
        logger.debug(f"Categorizing content: {content[:100]}...")
        if exclude_categories:
            logger.debug(f"Excluding categories: {exclude_categories}")
        
        try:
            # Use enhanced system prompt if feedback learning is enabled
            system_prompt = self.generate_enhanced_system_prompt()
            
            # Add exclusion information to prompt if categories are excluded
            if exclude_categories:
                exclusion_note = f"\n\nIMPORTANT: Do NOT categorize this content as any of the following: {', '.join(exclude_categories)}. Choose the next most appropriate category."
                system_prompt += exclusion_note

            # Build list of valid category names to embed in the user prompt.
            # Showing the exact names at inference time reduces abbreviation errors
            # (the model has to recall them from the long system prompt otherwise).
            valid_cats = getattr(config, 'VALID_CATEGORIES', list(config.DISCORD_CHANNELS.keys()))
            if exclude_categories:
                valid_cats = [c for c in valid_cats if c not in exclude_categories]
            valid_names = ", ".join(sorted(valid_cats))

            # Prepare the user prompt (content + response format instruction)
            prompt = (
                f"Content to categorize:\n{content}\n\n"
                f"Valid categories (use the exact name): {valid_names}\n\n"
                f"Respond with ONLY valid JSON: {{\"category\": \"<exact name from above>\", \"reasoning\": \"<1-2 sentence explanation of why this category was chosen over others>\", \"secondary_category\": \"<exact name from above, or null if not applicable>\"}}"
            )

            # Call Ollama API — use separate 'system' field so Ollama can reuse
            # KV cache for the system prompt across consecutive categorize calls
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.categorization_model,
                    "system": system_prompt,
                    "prompt": prompt,
                    "stream": False,
                    "keep_alive": "30m",
                    # NOTE: "think" is a top-level API parameter — inside "options" it is
                    # silently ignored and qwen-family models burn the whole num_predict
                    # budget thinking.
                    "think": False,
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 500,
                        # Must be identical across every call site using categorization_model —
                        # a mismatch forces Ollama to reload/reallocate the KV cache (root cause
                        # of the 2026-06-28 VRAM-thrash outage). See OLLAMA_CATEGORIZATION_NUM_CTX
                        # in config.py.
                        "num_ctx": self.categorization_num_ctx
                    }
                },
                timeout=300
            )

            response.raise_for_status()
            result = response.json()

            # Extract category and reasoning from response
            response_text = result.get('response', '').strip()
            category_raw = ''
            reasoning = None
            
            # Strategy 1: Try full JSON parse (balanced-brace extraction handles '}' inside strings)
            parsed = self._extract_json(response_text)
            if parsed is not None:
                category_raw = parsed.get('category', '').lower().strip()
                reasoning = parsed.get('reasoning', None)
            
            # Strategy 2: Extract category from truncated JSON (e.g. {"category":"politics","reasoning":"the...
            if not category_raw:
                cat_field = re.search(r'"category"\s*:\s*"([^"]+)"', response_text, re.IGNORECASE)
                if cat_field:
                    category_raw = cat_field.group(1).lower().strip()
                    # Also try to extract partial reasoning from truncated JSON
                    reason_field = re.search(r'"reasoning"\s*:\s*"([^"]*)', response_text, re.IGNORECASE)
                    if reason_field:
                        reasoning = reason_field.group(1).strip() or None
                    logger.debug(f"Extracted category from truncated JSON: '{category_raw}'")
            
            # Strategy 3: Plain text fallback — use first meaningful word/phrase
            if not category_raw:
                first_line = response_text.split('\n')[0].strip().lower()
                category_raw = first_line.split(',')[0].split('.')[0].strip()
                if category_raw:
                    logger.warning(f"JSON parse failed, using first line as category: '{category_raw}'")
            
            category = self._parse_category(category_raw)

            # Extract optional secondary category
            secondary_category = None
            if parsed is not None:
                secondary_raw = parsed.get('secondary_category')
                if secondary_raw and str(secondary_raw).lower().strip() not in ('null', 'none', ''):
                    secondary_category = self._parse_category(str(secondary_raw).lower().strip())
                    if secondary_category == category or secondary_category == config.DEFAULT_CATEGORY:
                        secondary_category = None

            # If the returned category is in the exclusion list, force to a different default
            if exclude_categories and category in exclude_categories:
                logger.warning(f"AI returned excluded category '{category}', forcing to alternative")
                reasoning = f"AI suggested '{category}' but it was excluded"
                # Try to find a suitable fallback category
                valid_categories = [cat for cat in config.DISCORD_CHANNELS.keys()
                                   if cat not in exclude_categories]
                # Use DEFAULT_CATEGORY if it's not excluded, otherwise use first valid category
                if config.DEFAULT_CATEGORY not in exclude_categories:
                    category = config.DEFAULT_CATEGORY
                elif valid_categories:
                    # Find the most generic category (prefer FALLBACK_CATEGORY or first available)
                    if config.FALLBACK_CATEGORY in valid_categories:
                        category = config.FALLBACK_CATEGORY
                    else:
                        category = valid_categories[0]
                    logger.info(f"Using fallback category: {category}")
                else:
                    logger.error("All categories excluded! Using DEFAULT_CATEGORY anyway")
                    category = config.DEFAULT_CATEGORY
                secondary_category = None

            logger.info(f"Categorized as: {category} (raw: {category_raw}, secondary: {secondary_category}, reasoning: {reasoning})")
            return category, reasoning, secondary_category

        except Exception as e:
            logger.error(f"Error categorizing content: {e}")
            # Make sure we don't return an excluded category even on error
            if exclude_categories and config.DEFAULT_CATEGORY in exclude_categories:
                valid_categories = [cat for cat in config.DISCORD_CHANNELS.keys()
                                   if cat not in exclude_categories]
                fallback = valid_categories[0] if valid_categories else config.DEFAULT_CATEGORY
                return fallback, f"Error during categorization: {str(e)[:50]}", None
            return config.DEFAULT_CATEGORY, f"Error during categorization: {str(e)[:50]}", None
    
    def _extract_json(self, text: str) -> "dict | None":
        """
        Robustly extract the first complete JSON object from a string.

        Uses a two-pass approach:
          1. Try parsing the whole stripped text as JSON directly.
          2. Walk character-by-character with a brace counter, tracking string
             context so that '}' inside quoted values is NOT counted as a
             closing brace.

        The old re.search(r'\\{[^}]+\\}') regex stops at the first '}' it sees,
        which breaks when the LLM writes '}' inside a reasoning string. This
        helper handles that case correctly.
        """
        text = text.strip()

        # Pass 1: the entire response may already be valid JSON.
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        # Pass 2: balanced-brace walk with string-context tracking.
        start = None
        depth = 0
        in_string = False
        escape_next = False

        for i, ch in enumerate(text):
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start is not None:
                    candidate = text[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        # Balanced span wasn't valid JSON; reset and keep searching.
                        start = None

        return None

    def _parse_category(self, category_raw):
        """
        Parse and validate category from model response
        
        Args:
            category_raw: Raw category string from model
        
        Returns:
            str: Validated category name (mapped to a configured Discord channel)
        """
        # Clean up the response
        category = category_raw.lower().strip()
        # Normalize separators: some models write "pop-culture", "video_games", or
        # "general_news" instead of the canonical space-separated form. Doing this once
        # here keeps all three matching stages (exact, alias, partial) consistent.
        category_normalized = category.replace('-', ' ').replace('_', ' ').strip()

        # Validate against ALL known categories (not just active Discord channels)
        # This prevents correct AI answers from being rejected when a channel is disabled
        valid_categories = getattr(config, 'VALID_CATEGORIES', list(config.DISCORD_CHANNELS.keys()))

        if category_normalized in valid_categories:
            # Category is valid — route to its channel if configured, otherwise default
            if category_normalized in config.DISCORD_CHANNELS:
                return category_normalized
            else:
                logger.info(f"Category '{category_normalized}' is valid but has no Discord channel, routing to '{config.DEFAULT_CATEGORY}'")
                return config.DEFAULT_CATEGORY

        # Alias lookup — maps known LLM abbreviations/alternate forms to canonical names.
        # More precise than substring partial matching below.
        canonical = _CATEGORY_ALIASES.get(category_normalized)
        if canonical is not None:
            logger.info(f"Alias match: '{category_normalized}' -> '{canonical}'")
            if canonical in config.DISCORD_CHANNELS:
                return canonical
            else:
                logger.info(
                    f"Alias '{category_normalized}' -> '{canonical}' valid but has no Discord channel, "
                    f"routing to '{config.DEFAULT_CATEGORY}'"
                )
                return config.DEFAULT_CATEGORY

        # Only do partial matching if the string is short and non-empty
        # (avoids matching reasoning text, and prevents "" from matching everything)
        if category_normalized and len(category_normalized) < 50:
            # Sort longest-first so more specific categories match before shorter ones
            # (e.g. "artificial intelligence" matches before "science & technology" when both could apply)
            for valid_cat in sorted(valid_categories, key=len, reverse=True):
                # Use word-boundary matching to prevent "crypto" from matching "cryptocurrency"
                pattern = r'\b' + re.escape(valid_cat) + r'\b'
                if re.search(pattern, category_normalized):
                    mapped = valid_cat if valid_cat in config.DISCORD_CHANNELS else config.DEFAULT_CATEGORY
                    logger.debug(f"Partial match: '{category_normalized}' -> '{valid_cat}' -> channel: '{mapped}'")
                    return mapped

        # Default to ignore if no match
        logger.warning(f"Unknown category '{category}', defaulting to '{config.DEFAULT_CATEGORY}'")
        return config.DEFAULT_CATEGORY
    
    @retry_with_backoff(max_retries=2, initial_delay=1)
    def format_text(self, content):
        """
        Clean up text using Ollama: fix ALL CAPS, grammar, punctuation, and spelling.

        Preserves all meaning, wording, URLs, numbers, acronyms, and structure.
        Only the four surface-level issues above are corrected.

        Args:
            content: Raw text to clean up

        Returns:
            str: Cleaned text, or original text on failure
        """
        logger.debug(f"Formatting text for: {content[:100]}...")

        try:
            prompt = (
                "You are a text-cleanup assistant for a news feed. "
                "Clean up the following text by fixing ALL FOUR of these issues and NOTHING ELSE:\n\n"
                "1. CAPITALIZATION: Convert ALL CAPS words to proper sentence case. "
                "Capitalize the first letter of each sentence and proper nouns "
                "(names, cities, countries, companies, organisations). "
                "This applies INSIDE quotation marks too — quoted text that is ALL CAPS "
                "is wire-service style, not preserved speech, and must be corrected. "
                "Example: '@handle ASKS \"STOP THE HACK NOW\" - \"WE AGREE\"' "
                "→ '@handle asks \"Stop the hack now\" - \"We agree\"'\n"
                "2. GRAMMAR: Fix obvious grammatical errors (wrong verb tense, "
                "subject-verb disagreement, misused words like their/there/they're).\n"
                "3. PUNCTUATION: Remove excessive punctuation (e.g. '!!!' → '!', "
                "'???' → '?'). Add a missing full stop at the end of a sentence if "
                "clearly absent. Do not add commas or other punctuation speculatively.\n"
                "4. SPELLING: Correct obvious misspellings. Do not change correctly "
                "spelled uncommon words or proper nouns you are unsure about.\n\n"
                "STRICT RULES — violating any of these means the output is rejected:\n"
                "- Do NOT add any words, sentences, commentary, or explanations\n"
                "- Do NOT remove any words or sentences\n"
                "- Do NOT change the meaning, tone, or factual content\n"
                "- Keep ALL acronyms uppercase: U.S., NATO, SEC, ETF, AI, CEO, "
                "GDP, FBI, EU, UK, UN, IMF, IPO, DeFi, TVL, and similar\n"
                "- Preserve ALL URLs exactly character-for-character\n"
                "- Preserve ALL numbers, percentages, and currency values exactly\n"
                "- If text contains multiple lines, preserve the line structure exactly\n"
                "- Output ONLY the cleaned text — no labels, no quotes, no preamble\n\n"
                f"INPUT TEXT:\n{content}\n\n"
                "CLEANED TEXT:"
            )

            def _generate(think, temperature, num_predict):
                resp = requests.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.categorization_model,
                        "prompt": prompt,
                        "stream": False,
                        "keep_alive": "30m",
                        "think": think,
                        "options": {
                            "temperature": temperature,
                            "num_predict": num_predict,
                            "num_ctx": self.categorization_num_ctx  # keep in sync — see categorize() above
                        }
                    },
                    timeout=300
                )
                resp.raise_for_status()
                return resp.json()

            result = _generate(think=True, temperature=0.0, num_predict=4000)

            # With thinking enabled, a too-small token budget can be entirely
            # consumed by reasoning before any answer is written, leaving
            # response empty with done_reason == 'length'. Retry once without
            # thinking (the pre-thinking-mode settings) so ALL CAPS content
            # still gets rewritten instead of posted verbatim when this happens.
            if result.get('done_reason') == 'length':
                logger.warning("format_text hit the token limit while thinking, retrying without thinking")
                result = _generate(think=False, temperature=0.1, num_predict=500)
                if result.get('done_reason') == 'length':
                    logger.warning("format_text hit the token limit on non-thinking retry too, using original")
                    return content

            rewritten = result.get('response', '').strip()

            if not rewritten:
                logger.warning("Ollama returned empty format_text response, using original")
                return content

            # Sanity check: rewritten text should be roughly the same length
            # Guards against hallucination or the model adding commentary
            len_ratio = len(rewritten) / len(content) if content else 1.0
            if len_ratio < 0.7 or len_ratio > 1.3:
                logger.warning(
                    f"format_text length mismatch "
                    f"(original: {len(content)}, rewritten: {len(rewritten)}, "
                    f"ratio: {len_ratio:.2f}), using original"
                )
                return content

            rewritten = fix_sentence_capitalization(rewritten)
            rewritten = capitalize_known_news_sources(rewritten)
            logger.info(f"Text formatted: '{content[:60]}...' -> '{rewritten[:60]}...'")
            return rewritten

        except Exception as e:
            logger.error(f"Error in format_text: {e}")
            return content

    @retry_with_backoff(max_retries=2, initial_delay=1)
    def verify_similarity(self, new_content, existing_content):
        """
        Use the LLM to verify whether two pieces of content are about the same specific
        event or story, not just the same general topic.
        
        This is used as a second pass after embedding cosine similarity flags a potential
        match, to reduce false positives (e.g. two unrelated political headlines both
        involving world leaders).
        
        Args:
            new_content: Full text of the new entry
            existing_content: Full text of the existing entry that was flagged as similar
        
        Returns:
            bool: True if the LLM confirms they are about the same event/story
        """
        logger.debug(f"LLM verifying similarity between entries...")
        
        prompt = (
            "You are a duplicate news detector. Determine if these two headlines/articles "
            "are about the SAME specific event or story — not just the same general topic "
            "or category.\n\n"
            "For example:\n"
            "- Two articles about Macron commenting on free speech = SAME story = yes\n"
            "- One article about Macron on free speech and another about Khamenei on warships = DIFFERENT stories = no\n"
            "- Two articles about Bitcoin hitting $100k = SAME story = yes\n"
            "- One article about Bitcoin price and another about Ethereum upgrade = DIFFERENT stories = no\n"
            "- 'Trump threatens 50% tariff on China' and 'Trump proposes 50% tariff on nations including China' = SAME story = yes\n"
            "  (Same event from different sources with different wording still counts as SAME)\n"
            "- A brief headline 'Justin Sun accuses WLFI of embedding a backdoor in its token contract' and a detailed article "
            "'Justin Sun accuses Trump's crypto project WLFI of secretly embedding a backdoor — froze his $75M wallet, token down 83%' = SAME story = yes\n"
            "  (A short summary and a detailed article covering the same event are still the SAME story)\n"
            "- 'Trump has posted himself seemingly as Jesus on Truth Social' and "
            "'Trump shares image depicting himself as Jesus Christ' = SAME story = yes\n"
            "  (One headline using hedged/uncertain language and another being definitive "
            "about the same action are still the SAME story)\n"
            "- A tweet 'Something happened. pic.twitter.com/xyz123' and a headline "
            "'Something happened' = SAME story = yes\n"
            "  (Ignore appended Twitter media URLs — they don't change the story)\n"
            "- A NYC mayor discussing racial wealth gaps and a US president discussing trade with Hungary = DIFFERENT stories = no\n"
            "  (Sharing the same general domain like economics or politics does NOT make them the same story)\n\n"
            f"ARTICLE A:\n{new_content[:500]}\n\n"
            f"ARTICLE B:\n{existing_content[:500]}\n\n"
            "Are these about the SAME specific event or story? Answer ONLY \"yes\" or \"no\"."
        )
        
        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.categorization_model,
                    "prompt": prompt,
                    "stream": False,
                    "keep_alive": "30m",
                    "think": False,
                    "options": {
                        "temperature": 0.0,
                        "num_predict": 100,
                        "num_ctx": self.categorization_num_ctx
                    }
                },
                timeout=300
            )
            response.raise_for_status()
            result = response.json().get('response', '').strip().lower()

            if not result:
                raise ValueError("LLM returned empty response for similarity check")

            is_same = result.startswith('yes')
            logger.info(f"LLM similarity verdict: {'SAME story' if is_same else 'DIFFERENT stories'} (raw: '{result}')")
            return is_same

        except Exception as e:
            logger.error(f"Error verifying similarity via LLM: {e}")
            # Fail open: if LLM check errors or times out after retries, allow the
            # entry through rather than silently suppressing it. A missed duplicate
            # is better than a missed genuine story.
            return False
    
    @retry_with_backoff(max_retries=2, initial_delay=2)
    def compare_entries(self, new_content, existing_content):
        """
        Compare a new entry against an existing one about the same story.
        Determines whether the new entry should supersede (replace) the old one.

        Returns:
            dict: {'verdict': 'supersede'|'ignore', 'reasoning': str}
        """
        logger.debug("Comparing entries for supersede decision...")

        prompt = (
            "You are comparing two news articles about the same story. "
            "Decide what to do with the NEW article.\n\n"
            f"EXISTING ARTICLE (already published):\n{existing_content[:800]}\n\n"
            f"NEW ARTICLE (just arrived):\n{new_content[:800]}\n\n"
            "Choose one verdict:\n"
            "- \"supersede\": The new article is a STRICT UPGRADE — it contains every key fact "
            "from the existing article AND adds corrections or critical new details. The existing "
            "article has NOTHING unique that the new one doesn't already cover. Replacing it loses "
            "ZERO information. Use this sparingly and only when you are certain.\n"
            "- \"keep_both\": The new article adds significant new information, BUT the existing "
            "article still contains facts, context, or details NOT found in the new one. This "
            "includes update/follow-up articles (e.g. initial alert vs confirmed details), "
            "articles with different scope or angle, or any case where you are uncertain. "
            "When in doubt, choose this.\n"
            "- \"ignore\": The new article covers the same story and adds nothing not already in "
            "the existing article. Discard the new one.\n\n"
            "Important signals:\n"
            "- If the existing article contains a DIRECT QUOTE (someone's exact words in "
            "quotation marks) that the new article does not, the existing article has unique "
            "information — choose keep_both.\n"
            "- If the new article is noticeably shorter than the existing article, it likely "
            "omits details — lean toward keep_both unless you are certain nothing was lost.\n\n"
            "Respond with ONLY valid JSON: "
            "{\"verdict\": \"supersede\", \"keep_both\", or \"ignore\", \"reasoning\": \"brief explanation\"}"
        )

        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.categorization_model,
                    "prompt": prompt,
                    "stream": False,
                    "keep_alive": "30m",
                    "think": False,
                    "options": {
                        "temperature": 0.2,
                        "num_predict": 200,
                        "num_ctx": self.categorization_num_ctx
                    }
                },
                timeout=300
            )
            response.raise_for_status()
            response_text = response.json().get('response', '').strip()

            # Try JSON parse (balanced-brace extraction handles '}' inside strings)
            parsed = self._extract_json(response_text)
            if parsed is not None:
                verdict = parsed.get('verdict', '').lower().strip()
                reasoning = parsed.get('reasoning', '')
                if verdict in ('supersede', 'keep_both', 'ignore'):
                    logger.info(f"Compare verdict: {verdict} — {reasoning}")
                    return {'verdict': verdict, 'reasoning': reasoning}

            # Fallback: could not parse clean JSON — default to keep_both (safe)
            logger.info("Compare verdict (fallback): keep_both — could not parse clean JSON")
            return {'verdict': 'keep_both', 'reasoning': 'Could not parse response, defaulting to keep_both'}

        except Exception as e:
            logger.error(f"Error comparing entries: {e}")
            return {'verdict': 'ignore', 'reasoning': f'Error: {e}'}

    @retry_with_backoff(max_retries=4, initial_delay=3)
    def generate_embedding(self, content):
        """
        Generate embedding vector for content
        
        Args:
            content: Text content to embed
        
        Returns:
            list: Embedding vector
        """
        logger.debug(f"Generating embedding for: {content[:100]}...")
        
        try:
            response = requests.post(
                f"{self.base_url}/api/embeddings",
                json={
                    "model": self.embedding_model,
                    "prompt": content,
                    "keep_alive": "30m",
                    # Cap context so the embedder loads small (~1 GB vs ~2.9 GB at the 8192
                    # default) and can stay GPU-resident next to the categorizer — see
                    # OLLAMA_EMBEDDING_NUM_CTX in config.py.
                    "options": {"num_ctx": self.embedding_num_ctx}
                },
                timeout=120
            )

            response.raise_for_status()
            result = response.json()

            embedding = result.get('embedding', [])
            
            if not embedding:
                raise ValueError("No embedding returned from Ollama")
            
            logger.debug(f"Generated embedding with {len(embedding)} dimensions")
            return embedding
            
        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            raise
    
    def _gate_feedback_block(self):
        """
        Build the user-feedback section of the rating prompt: recent entries the
        gate passed but the user demoted to ignore. Cached for an hour so each
        rating call doesn't hit the DB.

        Returns:
            str: Prompt block (empty string when disabled or no examples yet)
        """
        if not getattr(config, 'GATE_FEEDBACK_EXAMPLES_ENABLED', True) or not self.database:
            return ""

        now = time.time()
        if self._gate_feedback_cache is not None and (now - self._gate_feedback_timestamp) < self._cache_ttl:
            return self._gate_feedback_cache

        block = ""
        try:
            examples = self.database.get_gate_demotion_examples(
                limit=getattr(config, 'GATE_FEEDBACK_EXAMPLES_COUNT', 8),
                max_preview_length=120
            )
            if examples:
                lines = []
                for preview, old_score in examples:
                    clean = " ".join(preview.split())
                    lines.append(f'- (was wrongly scored {old_score}) "{clean}"')
                block = (
                    "\nOVERRIDING RULE — RECENT USER FEEDBACK. The channel owner removed these "
                    "exact posts as boring. This overrides every scoring rule above: if the item "
                    "is one of these or closely resembles one (same company, same event type, or "
                    "same style of story), cap surprising, impact, AND talkability at 4:\n"
                    + "\n".join(lines) + "\n"
                )
                logger.debug(f"Gate feedback block built with {len(examples)} demotion examples")
        except Exception as e:
            logger.error(f"Error building gate feedback block: {e}")

        # This block shares its context window with a large fixed rules/examples template
        # in rate_newsworthiness() plus the item content, so it needs to stay a short list —
        # warn if it's grown past ~10% of the window (well beyond what 8 one-liners costs).
        if block:
            token_estimate = _estimate_tokens(block)
            warn_budget = self.categorization_num_ctx * 0.1
            if token_estimate > warn_budget:
                logger.warning(
                    f"Gate feedback block is ~{token_estimate} tokens "
                    f"(GATE_FEEDBACK_EXAMPLES_COUNT={getattr(config, 'GATE_FEEDBACK_EXAMPLES_COUNT', 8)}) "
                    f"— it shares the rating prompt's context window with a large fixed "
                    f"instructions block; consider lowering the count in config.py."
                )

        self._gate_feedback_cache = block
        self._gate_feedback_timestamp = now
        return block

    @retry_with_backoff(max_retries=3, initial_delay=2)
    def rate_newsworthiness(self, content, category, threshold=None):
        """
        Rate how reaction-worthy content is: surprise, impact, and talkability.

        Args:
            content: Text content to rate
            category: The category this content was assigned to (for context)
            threshold: Pass/fail cutoff; defaults to the category's threshold from
                NEWSWORTHINESS_THRESHOLD_BY_CATEGORY, else NEWSWORTHINESS_THRESHOLD

        Returns:
            dict: {
                'score': float (weighted average 1-10),
                'surprising': int (1-10),
                'impact': int (1-10),
                'talkability': int (1-10),
                'reasoning': str (brief explanation),
                'passed': bool (whether score >= threshold)
            }
        """
        logger.debug(f"Rating reaction-worthiness for: {content[:100]}...")

        # Check if filter is enabled
        if not getattr(config, 'NEWSWORTHINESS_FILTER_ENABLED', False):
            logger.debug("Newsworthiness filter disabled, returning max score")
            return {
                'score': 10.0,
                'surprising': 10,
                'impact': 10,
                'talkability': 10,
                'reasoning': 'Filter disabled',
                'passed': True
            }

        try:
            # Recent user demotions become negative few-shot examples (cached hourly)
            feedback_block = self._gate_feedback_block()

            # Build the rating prompt — kept concise so the model reliably returns JSON
            prompt = f"""You rate news items for a Discord news channel whose readers want stories that get a REACTION — a reply, a share, a "whoa". Rate this item 1-10 on three criteria:

- surprising: Would a reader stop scrolling? Firsts, records, reversals, weird discoveries, dramatic numbers, things that break a pattern.
- impact: Real stakes. Does it change what things cost, what people can do, who holds power, or move serious money? Statements by heads of state and top-tier public figures (presidents, prime ministers, Elon Musk-level figures) score at least 6 here — their words move markets and policy. Routine commentary from lesser officials does not.
- talkability: Would someone tag a friend, argue about it, or react with an emoji? Outrage, humor, spectacle, controversy, "can you believe this" energy all count. Prediction-market odds on dramatic questions count too.

CALIBRATION — score 1-2 on ALL THREE, no matter how big the numbers look:
- Routine scheduled data: price tickers, gainers/losers lists, fear & greed index, TVL/dominance stats, market overviews
- Whale-wallet transfer alerts, stablecoin mint/burn notices
- Promotions, airdrops, referral content, token launch hype
- Fan memes and reaction content without a real event
- Incremental follow-ups that add nothing new to an already-known story

SINGLE-COMPANY RULE — a big dollar figure is NOT impact. Ask: whose life changes?
- ONE firm's operational news scores at most 4-5 overall: its regulatory approval or license, treasury buys/sells, debt moves, product/feature launches, exchange listings, earnings-as-expected.
- Hacks, exploits, or losses hitting ONE wallet, whale, or firm are insider noise: at most 4-5 overall. Systemic events (major exchange halts withdrawals, protocol-wide exploit affecting thousands) are the exception.
- Reserve impact 6+ for industry-wide rules, government policy, or events that change what MANY ordinary people can do or pay.

Score 3-5 for real but ordinary news: routine official commentary ("inflation still too high"), minor product updates, industry housekeeping, scheduled events going as planned.

Score 6-8 for stories with a genuine hook: consumer price hikes, unusual scientific finds, provocative quotes from major figures, surprising study results, policy shifts with broad reach, David-vs-Goliath conflicts.

Score 9-10 for jaw-droppers: major breaking events, historic firsts, huge reversals, scandals with names attached.

EXAMPLES:
- "Microsoft raises Xbox prices: Series X now $799.99" -> surprising 7, impact 7, talkability 8 (hits wallets, provokes outrage)
- "Archaeologists find 5,000-year-old prototype of Stonehenge nearby" -> surprising 8, impact 4, talkability 7 (wow factor)
- "Japan moves toward legalizing crypto ETFs under financial products law" -> surprising 7, impact 7, talkability 7 (industry-wide policy shift)
- "Circle receives final OCC approval to establish national trust bank" -> surprising 4, impact 4, talkability 3 (one firm's regulatory milestone)
- "Bitcoin treasury firm sold 1,400 BTC to repay debt" -> surprising 3, impact 3, talkability 3 (one firm's treasury housekeeping)
- "Early Solana whale hacked for $14.2M, funds bridged to Ethereum" -> surprising 4, impact 3, talkability 4 (one wallet's loss, insider noise)
- "Fed official: core inflation still too high, trending wrong way" -> surprising 2, impact 4, talkability 2 (routine commentary, no event)
- "Top 100 24h Gainers: M +73%, UNI +15%..." -> surprising 1, impact 1, talkability 1 (scheduled data dump)
- "Whale Alert: 50,000 ETH moved to Binance" -> surprising 1, impact 1, talkability 1 (wallet tracking noise)
{feedback_block}
Category: {category}
Content: {content[:1000]}

Respond with ONLY valid JSON in this exact format (replace each <> with your rating):
{{"surprising": <1-10>, "impact": <1-10>, "talkability": <1-10>, "reasoning": "<one sentence>"}}"""

            # Call Ollama API
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.categorization_model,
                    "prompt": prompt,
                    "stream": False,
                    "keep_alive": "30m",
                    "think": False,
                    "options": {
                        "temperature": 0.1,  # Low temperature for consistent, comparable ratings
                        "num_predict": 200,  # Give model enough room for JSON response
                        "num_ctx": self.categorization_num_ctx
                    }
                },
                timeout=300
            )

            response.raise_for_status()
            result = response.json()

            # Parse the JSON response
            response_text = result.get('response', '').strip()
            
            # Extract JSON from response — balanced-brace walk handles '}' inside strings
            rating_data = self._extract_json(response_text)
            if rating_data is None:
                raise ValueError(f"No JSON found in response: {response_text}")
            
            # Extract and validate scores
            surprising = max(1, min(10, int(rating_data.get('surprising', 5))))
            impact = max(1, min(10, int(rating_data.get('impact', 5))))
            talkability = max(1, min(10, int(rating_data.get('talkability', 5))))
            reasoning = str(rating_data.get('reasoning', 'No reasoning provided'))

            # Calculate weighted score
            weights = getattr(config, 'NEWSWORTHINESS_WEIGHTS', {
                'surprising': 0.4,
                'impact': 0.3,
                'talkability': 0.3
            })

            weighted_score = (
                surprising * weights.get('surprising', 0.4) +
                impact * weights.get('impact', 0.3) +
                talkability * weights.get('talkability', 0.3)
            )

            # Check against threshold: explicit arg > per-category > global default
            if threshold is None:
                per_category = getattr(config, 'NEWSWORTHINESS_THRESHOLD_BY_CATEGORY', {})
                threshold = per_category.get(category, getattr(config, 'NEWSWORTHINESS_THRESHOLD', 5.0))
            passed = weighted_score >= threshold

            result_dict = {
                'score': round(weighted_score, 1),
                'surprising': surprising,
                'impact': impact,
                'talkability': talkability,
                'reasoning': reasoning,
                'passed': passed
            }

            # Log the rating
            status = "PASS" if passed else "FAIL"
            logger.info(
                f"Reaction-worthiness: {weighted_score:.1f}/10 (S:{surprising} I:{impact} T:{talkability}) "
                f"[{status} @ {threshold}] - \"{reasoning}\""
            )

            return result_dict
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse newsworthiness JSON: {e}")
            # Fail open — entry already passed AI categorization; don't silently drop it
            # because Ollama returned a malformed response
            return {
                'score': 10.0,
                'surprising': 10,
                'impact': 10,
                'talkability': 10,
                'reasoning': f'JSON parse error — letting through: {str(e)[:50]}',
                'passed': True
            }
        except Exception as e:
            logger.error(f"Error rating newsworthiness: {e}")
            # Fail open — if Ollama errors (empty response, timeout, etc.), give the
            # entry the benefit of the doubt rather than silently dropping it
            return {
                'score': 10.0,
                'surprising': 10,
                'impact': 10,
                'talkability': 10,
                'reasoning': f'Rating error — letting through: {str(e)[:50]}',
                'passed': True
            }
    
    def health_check(self):
        """
        Check if Ollama is running and models are available
        
        Returns:
            bool: True if healthy
        """
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=10)
            response.raise_for_status()
            
            models = response.json().get('models', [])
            model_names = [m.get('name', '') for m in models]
            
            logger.info(f"Ollama health check passed. Available models: {model_names}")
            
            # Helper function to check if model exists (handles :latest suffix)
            def model_exists(model_name, available_models):
                # Check exact match
                if model_name in available_models:
                    return True
                # Check with :latest suffix
                if f"{model_name}:latest" in available_models:
                    return True
                # Check if any model starts with the name (handles any tag)
                for available in available_models:
                    if available.startswith(f"{model_name}:"):
                        return True
                return False
            
            # Check if our required models are available
            if not model_exists(self.categorization_model, model_names):
                logger.warning(f"Categorization model '{self.categorization_model}' not found in Ollama")
            
            if not model_exists(self.embedding_model, model_names):
                logger.warning(f"Embedding model '{self.embedding_model}' not found in Ollama")
            
            return True
            
        except Exception as e:
            logger.error(f"Ollama health check failed: {e}")
            return False

