"""
Perplexity AI client for generating search results with shareable URLs
"""
import time
import re
from openai import OpenAI
from utils import logger, retry_with_backoff
import config


class PerplexityClient:
    """Client for interacting with Perplexity AI API"""
    
    def __init__(self):
        """Initialize Perplexity client"""
        self.api_key = config.PERPLEXITY_API_KEY
        self.base_url = config.PERPLEXITY_BASE_URL
        self.model = config.PERPLEXITY_MODEL
        
        if not self.api_key:
            logger.warning("PERPLEXITY_API_KEY not found in environment variables. Perplexity features will be disabled.")
            self.client = None
        else:
            # Perplexity API is OpenAI-compatible
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )
            logger.info("Perplexity client initialized")
    
    def is_available(self):
        """Check if Perplexity client is available"""
        return self.client is not None
    
    def clean_response(self, text):
        """
        Clean Perplexity response by removing thinking text and citations
        
        Args:
            text: Raw response from Perplexity
        
        Returns:
            str: Cleaned response
        """
        # Remove thinking text (appears between <think> and </think> tags)
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        
        # Remove citation numbers in brackets like [1], [2], [123], etc.
        text = re.sub(r'\[\d+\]', '', text)
        
        # Remove any remaining XML-style tags
        text = re.sub(r'<[^>]+>', '', text)
        
        # Clean up extra whitespace and newlines
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)  # Replace 3+ newlines with 2
        text = text.strip()
        
        return text
    
    @retry_with_backoff(max_retries=3, initial_delay=2)
    def search(self, content):
        """
        Perform a Perplexity search for more information about news content
        
        Args:
            content: The news headline/content to search for
        
        Returns:
            dict: {
                'success': bool,
                'url': str or None (shareable URL to the answer),
                'error': str or None (error message if failed)
            }
        """
        if not self.client:
            return {
                'success': False,
                'url': None,
                'error': 'Perplexity API key not configured'
            }
        
        try:
            # Truncate content to reasonable length (avoid token limits)
            max_content_length = 500
            if len(content) > max_content_length:
                content = content[:max_content_length] + "..."
            
            # Construct the search prompt
            prompt = f"I need you to find more information about this news headline: {content}"
            
            logger.info(f"Sending Perplexity search request...")
            logger.debug(f"Prompt: {prompt[:100]}...")
            
            # Make the API call
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a helpful research assistant. Provide comprehensive, well-organized information about the given news headline. Write in a clear, direct style without showing your thinking process."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                stream=False
            )
            
            # Extract the response
            if response and response.choices:
                answer = response.choices[0].message.content
                logger.info(f"Perplexity search successful ({len(answer)} chars)")
                
                # Clean the response to remove thinking text and citations
                cleaned_answer = self.clean_response(answer)
                logger.debug(f"Cleaned answer ({len(cleaned_answer)} chars)")
                
                # Perplexity API doesn't provide shareable URLs to perplexity.ai
                # We return the cleaned answer text directly
                return {
                    'success': True,
                    'url': None,  # No shareable URL available from API
                    'answer': cleaned_answer,
                    'error': None
                }
            else:
                logger.error("Perplexity API returned empty response")
                return {
                    'success': False,
                    'url': None,
                    'error': 'Empty response from Perplexity API'
                }
        
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Perplexity search failed: {error_msg}")
            
            # Check for specific error types
            if "rate_limit" in error_msg.lower():
                return {
                    'success': False,
                    'url': None,
                    'error': 'Rate limit exceeded. Please try again later.'
                }
            elif "unauthorized" in error_msg.lower() or "authentication" in error_msg.lower():
                return {
                    'success': False,
                    'url': None,
                    'error': 'Invalid API key. Please check your Perplexity API key.'
                }
            elif "invalid model" in error_msg.lower() or "invalid_model" in error_msg.lower():
                return {
                    'success': False,
                    'url': None,
                    'error': f'Invalid model "{self.model}". Please check https://docs.perplexity.ai/getting-started/models and update PERPLEXITY_MODEL in config.py'
                }
            else:
                return {
                    'success': False,
                    'url': None,
                    'error': f'Search failed: {error_msg}'
                }
    
    def format_search_url(self, query):
        """
        Construct a Perplexity search URL with the query pre-filled
        This can be used as a fallback if share URLs are not available
        
        Args:
            query: The search query
        
        Returns:
            str: URL to Perplexity search page
        """
        import urllib.parse
        encoded_query = urllib.parse.quote(query)
        return f"https://www.perplexity.ai/search?q={encoded_query}"

