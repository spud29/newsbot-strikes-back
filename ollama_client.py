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
            str: Category name (defaults to 'ignore' if unclear)
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
            
            # Prepare the prompt
            prompt = f"{system_prompt}\n\nContent to categorize:\n{content}"
            
            # Call Ollama API
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.categorization_model,
                    "prompt": prompt,
                    "stream": False
                },
                timeout=60
            )
            
            response.raise_for_status()
            result = response.json()
            
            # Extract category from response
            category_raw = result.get('response', '').strip().lower()
            category = self._parse_category(category_raw)
            
            # If the returned category is in the exclusion list, force to a different default
            if exclude_categories and category in exclude_categories:
                logger.warning(f"AI returned excluded category '{category}', forcing to alternative")
                # Try to find a suitable fallback category
                valid_categories = [cat for cat in config.DISCORD_CHANNELS.keys() 
                                   if cat not in exclude_categories]
                # Use DEFAULT_CATEGORY if it's not excluded, otherwise use first valid category
                if config.DEFAULT_CATEGORY not in exclude_categories:
                    category = config.DEFAULT_CATEGORY
                elif valid_categories:
                    # Find the most generic category (prefer 'news/politics' or first available)
                    if 'news/politics' in valid_categories:
                        category = 'news/politics'
                    else:
                        category = valid_categories[0]
                    logger.info(f"Using fallback category: {category}")
                else:
                    logger.error("All categories excluded! Using DEFAULT_CATEGORY anyway")
                    category = config.DEFAULT_CATEGORY
            
            logger.info(f"Categorized as: {category} (raw: {category_raw})")
            return category
            
        except Exception as e:
            logger.error(f"Error categorizing content: {e}")
            # Make sure we don't return an excluded category even on error
            if exclude_categories and config.DEFAULT_CATEGORY in exclude_categories:
                valid_categories = [cat for cat in config.DISCORD_CHANNELS.keys() 
                                   if cat not in exclude_categories]
                return valid_categories[0] if valid_categories else config.DEFAULT_CATEGORY
            return config.DEFAULT_CATEGORY
    
    def _parse_category(self, category_raw):
        """
        Parse and validate category from model response
        
        Args:
            category_raw: Raw category string from model
        
        Returns:
            str: Validated category name
        """
        # Clean up the response
        category = category_raw.lower().strip()
        
        # Check if it matches any valid category
        valid_categories = list(config.DISCORD_CHANNELS.keys())
        
        if category in valid_categories:
            return category
        
        # Try partial matching
        for valid_cat in valid_categories:
            if valid_cat in category or category in valid_cat:
                logger.debug(f"Partial match: '{category}' -> '{valid_cat}'")
                return valid_cat
        
        # Default to ignore if no match
        logger.warning(f"Unknown category '{category}', defaulting to '{config.DEFAULT_CATEGORY}'")
        return config.DEFAULT_CATEGORY
    
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
            # Build the rating prompt with strict filtering criteria
            prompt = f"""You are a strict news editor. Rate this content's newsworthiness on three criteria (1-10 each).
BE VERY HARSH - most content is noise. Only truly significant news should score 7+.

## AUTOMATIC LOW SCORES (1-3) - These are NOISE, not news:

ADVERTISEMENTS & PROMOTIONS (score 1 - absolute garbage):
- Product feature updates ("We added X to our tool")
- Sales and promotions ("Black Friday Sale", "discount", "sale ends today")
- Self-promotional content from financial services
- Tool/platform announcements ("generate your own scanners at...")
- Links to products or services being sold

STOCKS/FINANCE NOISE:
- Daily market summaries ("Market tide today", daily heatmaps)
- ETF/fund regulatory filings and paperwork (Form S-1, withdrawals)
- Token unlock schedules or vesting announcements
- Individual whale/trader positions or trades
- TVL rankings, "top projects" lists without major news
- Routine price updates without record-breaking context

NEWS/POLITICS NOISE:
- Poll results and approval ratings (e.g., "Congress has 14% approval")
- Survey results (e.g., "86% of Americans support X")
- Scheduled diplomatic visits (e.g., "Putin to visit India on Dec 4")
- Someone expressing interest/willingness (e.g., "X says he'd be happy to serve as Y")
- Resource or mineral discoveries (routine geological news)
- Routine economic data releases without major surprise
- Politicians pushing for things they always push for (ongoing battles)

GENERAL NOISE:
- Generic "JUST IN" headlines with no real substance
- Scheduled announcements or expected events
- Minor partnership announcements
- Daily/weekly statistics without significant change
- Ongoing stories without new major developments

## HIGH SCORES (7-10) - Actual newsworthy content:
- RECORD-BREAKING: "highest ever", "lowest ever", "first time in history"
- Major institutional research/projections (Goldman, Morgan Stanley, etc. with specific forecasts)
- Significant investor sentiment from major banks (BoA, JPMorgan surveys with striking findings)
- ACTIONS TAKEN: Someone actually DID something significant (not just said they would)
- Major policy decisions or executive orders with immediate effect
- Unprecedented moves (closing airspace, military action, major legal rulings)
- Government scandals, fraud investigations, corruption charges
- Economic warnings from officials (recession, negative GDP)
- Events that would make someone say "holy shit, really?"

## SCORING CRITERIA:

1. SURPRISING (be strict - most things are predictable):
   - 1-3: Expected, routine, polls, surveys, scheduled events, expressions of interest
   - 4-6: Somewhat notable but not shocking
   - 7-10: Genuinely unexpected, actual action taken, would make someone say "what the fuck"

2. IMPACT (who actually cares?):
   - 1-3: Niche audience, statistics nerds only, doesn't affect average person
   - 4-6: Industry-relevant but limited broader impact
   - 7-10: Affects many people directly, major implications, changes the game

3. ACTIONABLE (does anyone need to DO something?):
   - 1-3: Pure information/statistics, no action needed, just interesting trivia
   - 4-6: Good to know for future reference
   - 7-10: Requires immediate attention, affects travel/money/safety

Category: {category}
Content: {content[:1500]}

Respond with ONLY valid JSON, no other text:
{{"surprising": X, "impact": X, "actionable": X, "reasoning": "brief 10-word max explanation"}}"""

            # Call Ollama API
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.categorization_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,  # Lower temperature for more consistent ratings
                        "num_predict": 100   # Limit response length
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
            # Return a middle score on parse error (let it through but log the issue)
            return {
                'score': 6.0,
                'surprising': 6,
                'impact': 6,
                'actionable': 6,
                'reasoning': 'JSON parse error - defaulting to pass',
                'passed': True
            }
        except Exception as e:
            logger.error(f"Error rating newsworthiness: {e}")
            # On error, default to passing (don't block news due to rating failures)
            return {
                'score': 6.0,
                'surprising': 6,
                'impact': 6,
                'actionable': 6,
                'reasoning': f'Rating error: {str(e)[:50]}',
                'passed': True
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

