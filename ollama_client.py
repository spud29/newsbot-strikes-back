"""
Ollama API client for categorization and embeddings
"""
import requests
from utils import logger, retry_with_backoff
import config

class OllamaClient:
    """Client for interacting with local Ollama API"""
    
    def __init__(self):
        """Initialize Ollama client"""
        self.base_url = config.OLLAMA_BASE_URL
        self.categorization_model = config.OLLAMA_CATEGORIZATION_MODEL
        self.embedding_model = config.OLLAMA_EMBEDDING_MODEL
        logger.info(f"Ollama client initialized: {self.base_url}")
    
    @retry_with_backoff(max_retries=3, initial_delay=2)
    def categorize(self, content):
        """
        Categorize content using Ollama
        
        Args:
            content: Text content to categorize
        
        Returns:
            str: Category name (defaults to 'ignore' if unclear)
        """
        logger.debug(f"Categorizing content: {content[:100]}...")
        
        try:
            # Prepare the prompt
            prompt = f"{config.SYSTEM_PROMPT}\n\nContent to categorize:\n{content}"
            
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
            
            logger.info(f"Categorized as: {category} (raw: {category_raw})")
            return category
            
        except Exception as e:
            logger.error(f"Error categorizing content: {e}")
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

