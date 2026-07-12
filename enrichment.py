"""
Enrichment pipeline: search for related articles, extract text, and rewrite
headlines into more informative Discord posts.

Uses SearXNG for web search, Jina Reader for article extraction, and the
categorization model configured in config.py for summarization. Falls back to
the original content on any failure.
"""
import requests
import time
from utils import logger
import config


def search_related_articles(headline, max_results=None):
    """
    Search SearXNG for articles related to a headline.

    Returns:
        list[dict]: Each dict has 'title', 'url', and 'snippet'.
    """
    base_url = getattr(config, 'SEARXNG_BASE_URL', 'http://localhost:8080')
    if max_results is None:
        max_results = getattr(config, 'ENRICHMENT_MAX_ARTICLES', 3)

    try:
        resp = requests.get(
            f"{base_url}/search",
            params={
                'q': headline[:200],
                'format': 'json',
                'categories': 'news',
                'language': 'en',
                'time_range': 'week',
            },
            timeout=getattr(config, 'ENRICHMENT_SEARCH_TIMEOUT', 10),
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get('results', [])[:max_results]:
            results.append({
                'title': item.get('title', ''),
                'url': item.get('url', ''),
                'snippet': item.get('content', ''),
            })
        logger.info(f"SearXNG returned {len(results)} results for: {headline[:80]}")
        return results

    except Exception as e:
        logger.warning(f"SearXNG search failed: {e}")
        return []


def extract_article_text(url, max_chars=None):
    """
    Extract article text via Jina Reader.

    Returns:
        str: Extracted article text, or empty string on failure.
    """
    if max_chars is None:
        max_chars = getattr(config, 'ENRICHMENT_MAX_ARTICLE_CHARS', 3000)
    jina_base = getattr(config, 'JINA_READER_BASE_URL', 'https://r.jina.ai')

    try:
        resp = requests.get(
            f"{jina_base}/{url}",
            headers={'Accept': 'text/plain'},
            timeout=getattr(config, 'ENRICHMENT_EXTRACT_TIMEOUT', 15),
        )
        resp.raise_for_status()
        text = resp.text.strip()
        if len(text) > max_chars:
            text = text[:max_chars]
        return text

    except Exception as e:
        logger.warning(f"Jina Reader extraction failed for {url}: {e}")
        return ''


def synthesize_enriched_post(original_content, articles, category):
    """
    Use Ollama to rewrite the headline into a richer post using extracted
    article context.

    Args:
        original_content: The raw headline / entry text.
        articles: list of dicts with 'title', 'url', and 'text' keys.
        category: The assigned news category (for tone guidance).

    Returns:
        str: The enriched post text, or empty string on failure.
    """
    base_url = getattr(config, 'OLLAMA_BASE_URL', 'http://localhost:11434')
    model = getattr(config, 'ENRICHMENT_MODEL', config.OLLAMA_CATEGORIZATION_MODEL)

    context_blocks = []
    source_urls = []
    for i, art in enumerate(articles, 1):
        text = art.get('text', '')
        if not text:
            continue
        context_blocks.append(
            f"[Source {i}: {art['title']}]\n{text[:1500]}"
        )
        source_urls.append(art['url'])

    if not context_blocks:
        logger.info("No article text extracted — skipping synthesis")
        return ''

    context_section = '\n\n'.join(context_blocks)

    system_msg = f"You are a concise news writer for a Discord channel in the \"{category}\" category."

    user_msg = f"""ORIGINAL HEADLINE:
{original_content}

RELATED ARTICLE CONTEXT:
{context_section}

TASK:
Rewrite the headline into a short, informative Discord post (2-4 sentences max).
Add key context, numbers, or details from the articles that make the story clearer.
Do NOT add speculation or opinions. Stick to facts from the sources.
Keep the tone neutral and direct. No emojis, no hashtags.
Do NOT include source URLs in the text — they will be appended separately.
Output ONLY the rewritten post text, nothing else."""

    try:
        resp = requests.post(
            f"{base_url}/api/chat",
            json={
                'model': model,
                'messages': [
                    {'role': 'system', 'content': system_msg},
                    {'role': 'user', 'content': user_msg},
                ],
                'stream': False,
                'think': False,
                'options': {
                    'temperature': 0.3,
                    'num_predict': 300,
                    'num_ctx': config.OLLAMA_CATEGORIZATION_NUM_CTX,
                },
            },
            timeout=getattr(config, 'ENRICHMENT_SYNTHESIS_TIMEOUT', 60),
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get('message', {}).get('content', '').strip()

        if not result or len(result) < 20:
            logger.warning("Ollama synthesis returned empty or too-short result")
            return ''

        # Strip thinking tags if present (fallback for models that ignore think:false)
        import re
        result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL).strip()

        if not result or len(result) < 20:
            logger.warning("Ollama synthesis empty after stripping thinking tags")
            return ''

        # Append source links
        if source_urls:
            links = ' | '.join(source_urls[:2])
            result = f"{result}\n\nSources: {links}"

        logger.info(f"Enrichment synthesis produced {len(result)} chars")
        return result

    except Exception as e:
        logger.warning(f"Ollama enrichment synthesis failed: {e}")
        return ''


def enrich(content, category):
    """
    Full enrichment pipeline: search → extract → synthesize.

    This is a blocking function — call via asyncio.to_thread() from the
    async entry pipeline.

    Args:
        content: Raw headline / entry content.
        category: Assigned category string.

    Returns:
        str or None: Enriched content, or None to signal fallback to original.
    """
    if not getattr(config, 'ENRICHMENT_ENABLED', False):
        return None

    start = time.time()

    # Step 1: Search for related articles
    articles = search_related_articles(content)
    if not articles:
        logger.info("Enrichment: no search results, keeping original")
        return None

    # Step 2: Extract text from top articles
    for art in articles:
        art['text'] = extract_article_text(art['url'])

    articles_with_text = [a for a in articles if a.get('text')]
    if not articles_with_text:
        logger.info("Enrichment: no article text extracted, keeping original")
        return None

    # Step 3: Synthesize enriched post
    enriched = synthesize_enriched_post(content, articles_with_text, category)

    elapsed = time.time() - start
    if enriched:
        logger.info(f"Enrichment completed in {elapsed:.1f}s ({len(enriched)} chars)")
        return enriched
    else:
        logger.info(f"Enrichment synthesis failed after {elapsed:.1f}s, keeping original")
        return None
