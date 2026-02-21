"""
Ollama API client for categorization and embeddings
"""
import requests
import time
import json
import re
from utils import logger, retry_with_backoff
import config

class OllamaClient:
    """Client for interacting with local Ollama API"""
    
    def __init__(self, removed_entries_db=None):
        """
        Initialize Ollama client
        
        Args:
            removed_entries_db: Optional RemovedEntriesDB instance for feedback learning
        """
        self.base_url = config.OLLAMA_BASE_URL
        self.categorization_model = config.OLLAMA_CATEGORIZATION_MODEL
        self.embedding_model = config.OLLAMA_EMBEDDING_MODEL
        self.removed_entries_db = removed_entries_db
        
        # Cache for enhanced system prompt (refreshed every hour)
        self._enhanced_prompt_cache = None
        self._cache_timestamp = 0
        self._cache_ttl = 3600  # 1 hour
        
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
                    enhanced_prompt += "\nAvoid posting content similar to the examples above. When in doubt, use 'ignore'."
                    
                    logger.debug(f"Enhanced system prompt with {len(previews)} negative examples")
                else:
                    logger.debug("No removed entries available for feedback learning")
            
            except Exception as e:
                logger.error(f"Error generating enhanced system prompt: {e}", exc_info=True)
        
        # Cache the enhanced prompt
        self._enhanced_prompt_cache = enhanced_prompt
        self._cache_timestamp = current_time
        
        return enhanced_prompt
    
    @retry_with_backoff(max_retries=3, initial_delay=2)
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
            
            # Prepare the prompt - request JSON with category and reasoning
            prompt = (
                f"{system_prompt}\n\n"
                f"Content to categorize:\n{content}\n\n"
                f"Respond with ONLY valid JSON: {{\"category\": \"<name>\", \"reasoning\": \"<1-2 sentence explanation of why this category was chosen over others>\"}}"
            )
            
            # Call Ollama API
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.categorization_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 300
                    }
                },
                timeout=60
            )
            
            response.raise_for_status()
            result = response.json()
            
            # Extract category and reasoning from response
            response_text = result.get('response', '').strip()
            category_raw = ''
            reasoning = None
            
            # Strategy 1: Try full JSON parse
            try:
                json_match = re.search(r'\{[^}]+\}', response_text)
                if json_match:
                    parsed = json.loads(json_match.group())
                    category_raw = parsed.get('category', '').lower().strip()
                    reasoning = parsed.get('reasoning', None)
            except (json.JSONDecodeError, AttributeError):
                pass
            
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
                    # Find the most generic category (prefer 'politics' or first available)
                    if 'politics' in valid_categories:
                        category = 'politics'
                    else:
                        category = valid_categories[0]
                    logger.info(f"Using fallback category: {category}")
                else:
                    logger.error("All categories excluded! Using DEFAULT_CATEGORY anyway")
                    category = config.DEFAULT_CATEGORY
            
            logger.info(f"Categorized as: {category} (raw: {category_raw}, reasoning: {reasoning})")
            return category, reasoning
            
        except Exception as e:
            logger.error(f"Error categorizing content: {e}")
            # Make sure we don't return an excluded category even on error
            if exclude_categories and config.DEFAULT_CATEGORY in exclude_categories:
                valid_categories = [cat for cat in config.DISCORD_CHANNELS.keys() 
                                   if cat not in exclude_categories]
                fallback = valid_categories[0] if valid_categories else config.DEFAULT_CATEGORY
                return fallback, f"Error during categorization: {str(e)[:50]}"
            return config.DEFAULT_CATEGORY, f"Error during categorization: {str(e)[:50]}"
    
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
        
        # Validate against ALL known categories (not just active Discord channels)
        # This prevents correct AI answers from being rejected when a channel is disabled
        valid_categories = getattr(config, 'VALID_CATEGORIES', list(config.DISCORD_CHANNELS.keys()))
        
        if category in valid_categories:
            # Category is valid — route to its channel if configured, otherwise default
            if category in config.DISCORD_CHANNELS:
                return category
            else:
                logger.info(f"Category '{category}' is valid but has no Discord channel, routing to '{config.DEFAULT_CATEGORY}'")
                return config.DEFAULT_CATEGORY
        
        # Only do partial matching if the string is short and non-empty
        # (avoids matching reasoning text, and prevents "" from matching everything)
        if category and len(category) < 50:
            for valid_cat in valid_categories:
                if valid_cat in category or category in valid_cat:
                    mapped = valid_cat if valid_cat in config.DISCORD_CHANNELS else config.DEFAULT_CATEGORY
                    logger.debug(f"Partial match: '{category}' -> '{valid_cat}' -> channel: '{mapped}'")
                    return mapped
        
        # Default to ignore if no match
        logger.warning(f"Unknown category '{category}', defaulting to '{config.DEFAULT_CATEGORY}'")
        return config.DEFAULT_CATEGORY
    
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
            "- One article about Bitcoin price and another about Ethereum upgrade = DIFFERENT stories = no\n\n"
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
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 10
                    }
                },
                timeout=15
            )
            response.raise_for_status()
            result = response.json().get('response', '').strip().lower()
            
            is_same = result.startswith('yes')
            logger.info(f"LLM similarity verdict: {'SAME story' if is_same else 'DIFFERENT stories'} (raw: '{result}')")
            return is_same
            
        except Exception as e:
            logger.error(f"Error verifying similarity via LLM: {e}")
            # Fail safe: if the LLM check fails, assume they ARE similar
            # (preserves the old behavior of trusting the embedding match)
            return True
    
    @retry_with_backoff(max_retries=3, initial_delay=2)
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
                    "prompt": content
                },
                timeout=30
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
    
    @retry_with_backoff(max_retries=3, initial_delay=2)
    def rate_newsworthiness(self, content, category):
        """
        Rate content newsworthiness on surprise, impact, and actionability.
        
        Args:
            content: Text content to rate
            category: The category this content was assigned to (for context)
        
        Returns:
            dict: {
                'score': float (weighted average 1-10),
                'surprising': int (1-10),
                'impact': int (1-10),
                'actionable': int (1-10),
                'reasoning': str (brief explanation),
                'passed': bool (whether score >= threshold)
            }
        """
        logger.debug(f"Rating newsworthiness for: {content[:100]}...")
        
        # Check if filter is enabled
        if not getattr(config, 'NEWSWORTHINESS_FILTER_ENABLED', False):
            logger.debug("Newsworthiness filter disabled, returning max score")
            return {
                'score': 10.0,
                'surprising': 10,
                'impact': 10,
                'actionable': 10,
                'reasoning': 'Filter disabled',
                'passed': True
            }
        
        try:
            # Build the rating prompt — kept concise so the model reliably returns JSON
            prompt = f"""Rate this news content's newsworthiness from 1-10 on three criteria.

Score 1-3 for routine/mundane news (polls, surveys, scheduled events, minor updates, ads, promotions, daily stats, ongoing stories without new developments).
Score 4-6 for moderately notable news (industry-relevant, limited broader impact).
Score 7-10 for genuinely surprising, high-impact, or urgent news that would make someone say "wow".

Category: {category}
Content: {content[:1000]}

Respond with ONLY this JSON, no other text:
{{"surprising": 5, "impact": 5, "actionable": 5, "reasoning": "brief explanation"}}"""

            # Call Ollama API
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.categorization_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,  # Lower temperature for more consistent ratings
                        "num_predict": 200   # Give model enough room for JSON response
                    }
                },
                timeout=60
            )
            
            response.raise_for_status()
            result = response.json()
            
            # Parse the JSON response
            response_text = result.get('response', '').strip()
            
            # Try to extract JSON from response (handle potential extra text)
            # Find JSON object in response
            json_match = re.search(r'\{[^}]+\}', response_text)
            if json_match:
                rating_data = json.loads(json_match.group())
            else:
                raise ValueError(f"No JSON found in response: {response_text}")
            
            # Extract and validate scores
            surprising = max(1, min(10, int(rating_data.get('surprising', 5))))
            impact = max(1, min(10, int(rating_data.get('impact', 5))))
            actionable = max(1, min(10, int(rating_data.get('actionable', 5))))
            reasoning = str(rating_data.get('reasoning', 'No reasoning provided'))[:100]
            
            # Calculate weighted score
            weights = getattr(config, 'NEWSWORTHINESS_WEIGHTS', {
                'surprising': 0.4,
                'impact': 0.35,
                'actionable': 0.25
            })
            
            weighted_score = (
                surprising * weights.get('surprising', 0.4) +
                impact * weights.get('impact', 0.35) +
                actionable * weights.get('actionable', 0.25)
            )
            
            # Check against threshold
            threshold = getattr(config, 'NEWSWORTHINESS_THRESHOLD', 5.0)
            passed = weighted_score >= threshold
            
            result_dict = {
                'score': round(weighted_score, 1),
                'surprising': surprising,
                'impact': impact,
                'actionable': actionable,
                'reasoning': reasoning,
                'passed': passed
            }
            
            # Log the rating
            status = "PASS" if passed else "FAIL"
            logger.info(
                f"Newsworthiness: {weighted_score:.1f}/10 (S:{surprising} I:{impact} A:{actionable}) "
                f"[{status}] - \"{reasoning}\""
            )
            
            return result_dict
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse newsworthiness JSON: {e}")
            # On parse error, fail closed — route to ignore for manual review
            return {
                'score': 0.0,
                'surprising': 0,
                'impact': 0,
                'actionable': 0,
                'reasoning': 'JSON parse error - defaulting to fail',
                'passed': False
            }
        except Exception as e:
            logger.error(f"Error rating newsworthiness: {e}")
            # On error, fail closed — route to ignore for manual review
            return {
                'score': 0.0,
                'surprising': 0,
                'impact': 0,
                'actionable': 0,
                'reasoning': f'Rating error: {str(e)[:50]}',
                'passed': False
            }
    
    def health_check(self):
        """
        Check if Ollama is running and models are available
        
        Returns:
            bool: True if healthy
        """
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
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

