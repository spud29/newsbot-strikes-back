"""
FastAPI Web Dashboard for Discord News Aggregator Bot
"""
import os
import json
import time
import secrets
from datetime import datetime, timedelta
from typing import Optional
from functools import wraps

from fastapi import FastAPI, Request, Depends, HTTPException, status, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import shutil
from pathlib import Path

from database import Database
from ollama_client import OllamaClient
from discord_poster import DiscordPoster
from media_handler import MediaHandler
from telegram_poller import TelegramPoller
import config
from utils import logger
import re
import asyncio
from concurrent.futures import ThreadPoolExecutor
import functools

# Initialize FastAPI app
app = FastAPI(title="NewsBot Dashboard", description="Monitoring and admin dashboard for Discord News Aggregator Bot")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Directory for serving processed media files
MEDIA_SERVE_DIR = Path("temp_media_serve")
MEDIA_SERVE_DIR.mkdir(exist_ok=True)

# Setup Jinja2 templates
templates = Jinja2Templates(directory="templates")

# HTTP Basic Auth
security = HTTPBasic()

# Get credentials from environment
DASHBOARD_USERNAME = os.getenv('DASHBOARD_USERNAME', 'admin')
DASHBOARD_PASSWORD = os.getenv('DASHBOARD_PASSWORD', '')

if not DASHBOARD_PASSWORD:
    logger.warning("DASHBOARD_PASSWORD not set in .env file! Dashboard will not be accessible.")

# Initialize shared components
db = Database()
ollama = OllamaClient()
discord_poster = DiscordPoster()
media_handler = MediaHandler()
telegram_poller_instance = None  # Will be initialized when needed

# Thread pool for CPU-bound tasks
executor = ThreadPoolExecutor(max_workers=2)

def run_with_timeout(func, timeout=30):
    """
    Wrapper to run a blocking function with proper timeout handling
    
    Args:
        func: Function to run
        timeout: Timeout in seconds
    
    Returns:
        Result of function or raises TimeoutError
    """
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as local_executor:
        future = local_executor.submit(func)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.error(f"Function {func.__name__} timed out after {timeout} seconds")
            raise TimeoutError(f"Operation timed out after {timeout} seconds")

# Custom Jinja2 filters
def format_timestamp(timestamp):
    """Convert Unix timestamp to readable datetime"""
    if not timestamp:
        return "N/A"
    try:
        dt = datetime.fromtimestamp(float(timestamp))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return "Invalid"

def time_ago(timestamp):
    """Convert timestamp to 'X hours ago' format"""
    if not timestamp:
        return "N/A"
    try:
        dt = datetime.fromtimestamp(float(timestamp))
        diff = datetime.now() - dt
        
        if diff.days > 0:
            return f"{diff.days} day{'s' if diff.days > 1 else ''} ago"
        elif diff.seconds >= 3600:
            hours = diff.seconds // 3600
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        elif diff.seconds >= 60:
            minutes = diff.seconds // 60
            return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
        else:
            return "Just now"
    except:
        return "Unknown"

templates.env.filters["format_timestamp"] = format_timestamp
templates.env.filters["time_ago"] = time_ago


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify HTTP Basic Auth credentials"""
    correct_username = secrets.compare_digest(credentials.username, DASHBOARD_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, DASHBOARD_PASSWORD)
    
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# Routes

@app.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request, username: str = Depends(verify_credentials)):
    """Dashboard home page with overview stats"""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "username": username
    })


@app.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request, username: str = Depends(verify_credentials)):
    """Source monitoring page"""
    return templates.TemplateResponse("sources.html", {
        "request": request,
        "username": username
    })


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, username: str = Depends(verify_credentials)):
    """Error logs viewer page"""
    return templates.TemplateResponse("logs.html", {
        "request": request,
        "username": username
    })


@app.get("/manual", response_class=HTMLResponse)
async def manual_page(request: Request, username: str = Depends(verify_credentials)):
    """Manual processing page"""
    return templates.TemplateResponse("manual.html", {
        "request": request,
        "username": username
    })


@app.get("/config", response_class=HTMLResponse)
async def config_page(request: Request, username: str = Depends(verify_credentials)):
    """Configuration editor page"""
    return templates.TemplateResponse("config.html", {
        "request": request,
        "username": username
    })


@app.get("/database", response_class=HTMLResponse)
async def database_page(request: Request, username: str = Depends(verify_credentials)):
    """Database tools page"""
    return templates.TemplateResponse("database.html", {
        "request": request,
        "username": username,
        "config": config
    })


# API Endpoints

@app.get("/api/health")
async def health_check(username: str = Depends(verify_credentials)):
    """Health check for bot and Ollama"""
    ollama_healthy = ollama.health_check()
    
    # Check if bot.log has recent activity
    bot_active = False
    try:
        if os.path.exists('bot.log'):
            log_modified = os.path.getmtime('bot.log')
            # Consider bot active if log was modified in last 10 minutes
            bot_active = (time.time() - log_modified) < 600
    except:
        pass
    
    return {
        "status": "healthy" if ollama_healthy and bot_active else "degraded",
        "ollama": "up" if ollama_healthy else "down",
        "bot": "active" if bot_active else "inactive",
        "timestamp": time.time()
    }


@app.get("/api/stats")
async def get_stats(username: str = Depends(verify_credentials)):
    """Get current dashboard statistics"""
    # Reload database from files to get fresh data
    db.processed_ids = db._load_json(db.processed_ids_path, {})
    db.embeddings = db._load_json(db.embeddings_path, {})
    db.message_mapping = db._load_json(db.message_mapping_path, {})
    
    db_stats = db.get_stats()
    
    # Calculate 24h statistics
    cutoff_24h = time.time() - (24 * 3600)
    processed_24h = sum(1 for ts in db.processed_ids.values() if ts >= cutoff_24h)
    
    # Get recent entries
    recent_entries = []
    sorted_entries = sorted(db.processed_ids.items(), key=lambda x: x[1], reverse=True)[:20]
    
    for entry_id, timestamp in sorted_entries:
        # Parse entry_id to extract source and category info
        parts = entry_id.split('_')
        source_type = parts[0] if parts else 'unknown'
        
        recent_entries.append({
            'id': entry_id,
            'timestamp': timestamp,
            'source': source_type
        })
    
    return {
        'database': db_stats,
        'processed_24h': processed_24h,
        'recent_entries': recent_entries,
        'rss_feeds_count': len(config.RSS_FEEDS),
        'telegram_channels_count': len(config.TELEGRAM_CHANNELS)
    }


@app.post("/api/test-category")
async def test_categorization(
    text: str = Form(...),
    username: str = Depends(verify_credentials)
):
    """Test categorization without posting"""
    try:
        category = ollama.categorize(text)
        
        # Generate embedding and check for duplicates
        embedding = ollama.generate_embedding(text)
        is_duplicate, similarity, match_preview = db.find_similar(
            embedding, 
            threshold=config.DUPLICATE_THRESHOLD
        )
        is_similar, similar_score, similar_preview = db.find_similar(
            embedding,
            threshold=config.SIMILARITY_THRESHOLD
        )
        
        return {
            "success": True,
            "category": category,
            "is_duplicate": is_duplicate,
            "duplicate_similarity": similarity if is_duplicate else 0.0,
            "is_similar": is_similar and not is_duplicate,
            "similar_score": similar_score if is_similar else 0.0,
            "match_preview": match_preview if is_duplicate else similar_preview
        }
    except Exception as e:
        logger.error(f"Error testing categorization: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/api/database/clear")
async def clear_old_entries(username: str = Depends(verify_credentials)):
    """Manually trigger database cleanup"""
    try:
        db.cleanup_old_entries()
        stats = db.get_stats()
        return {
            "success": True,
            "message": "Old entries cleaned up",
            "stats": stats
        }
    except Exception as e:
        logger.error(f"Error clearing database: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


@app.delete("/api/database/reset/{entry_id}")
async def reset_entry(entry_id: str, username: str = Depends(verify_credentials)):
    """Remove specific entry from processed IDs, embeddings, and message mapping"""
    try:
        # Reload database to get fresh data
        db.processed_ids = db._load_json(db.processed_ids_path, {})
        db.embeddings = db._load_json(db.embeddings_path, {})
        db.message_mapping = db._load_json(db.message_mapping_path, {})
        
        removed_items = []
        
        # Check if it's in processed_ids
        if entry_id in db.processed_ids:
            del db.processed_ids[entry_id]
            db._save_json(db.processed_ids_path, db.processed_ids)
            removed_items.append("processed_ids")
        
        # Check if it's in message_mapping
        if entry_id in db.message_mapping:
            del db.message_mapping[entry_id]
            db._save_json(db.message_mapping_path, db.message_mapping)
            removed_items.append("message_mapping")
        
        # Check if it's an embedding entry (starts with "embedding_")
        if entry_id.startswith("embedding_"):
            # Extract the hash prefix (first 12 chars after "embedding_")
            hash_prefix = entry_id.replace("embedding_", "")
            
            # Find matching embedding hash
            matching_hashes = [h for h in db.embeddings.keys() if h.startswith(hash_prefix)]
            
            if matching_hashes:
                for hash_key in matching_hashes:
                    # Also check if the embedding has an entry_id and remove that too
                    embedding_data = db.embeddings[hash_key]
                    stored_entry_id = embedding_data.get('entry_id')
                    
                    # Remove the embedding
                    del db.embeddings[hash_key]
                    removed_items.append(f"embedding ({hash_key[:12]}...)")
                    
                    # If embedding has a linked entry_id, remove that from processed_ids and message_mapping
                    if stored_entry_id:
                        if stored_entry_id in db.processed_ids:
                            del db.processed_ids[stored_entry_id]
                            removed_items.append(f"processed_id ({stored_entry_id})")
                        if stored_entry_id in db.message_mapping:
                            del db.message_mapping[stored_entry_id]
                            removed_items.append(f"message_mapping ({stored_entry_id})")
                
                # Save all changes
                db._save_json(db.embeddings_path, db.embeddings)
                db._save_json(db.processed_ids_path, db.processed_ids)
                db._save_json(db.message_mapping_path, db.message_mapping)
        
        if removed_items:
            return {
                "success": True,
                "message": f"Entry {entry_id} removed from: {', '.join(removed_items)}. Can be reprocessed."
            }
        else:
            return {
                "success": False,
                "error": "Entry ID not found in any database"
            }
    except Exception as e:
        logger.error(f"Error resetting entry: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/api/database/reprocess/{entry_id}")
async def reprocess_entry(entry_id: str, username: str = Depends(verify_credentials)):
    """Manually reprocess an entry through the full bot pipeline"""
    try:
        # Reload database to get fresh data
        db.processed_ids = db._load_json(db.processed_ids_path, {})
        db.embeddings = db._load_json(db.embeddings_path, {})
        db.message_mapping = db._load_json(db.message_mapping_path, {})
        
        content = None
        source_url = None
        original_entry_id = entry_id
        
        # Try to find content from message_mapping first
        if entry_id in db.message_mapping:
            mapping_data = db.message_mapping[entry_id]
            content = mapping_data.get('content')
            source_url = mapping_data.get('source_url')
            logger.info(f"Found content in message_mapping for {entry_id}")
        
        # If it's an embedding ID, find the embedding and extract content
        if not content and entry_id.startswith("embedding_"):
            hash_prefix = entry_id.replace("embedding_", "")
            matching_hashes = [h for h in db.embeddings.keys() if h.startswith(hash_prefix)]
            
            if matching_hashes:
                hash_key = matching_hashes[0]
                embedding_data = db.embeddings[hash_key]
                content = embedding_data.get('preview')
                
                # Try to get full content from linked entry_id
                stored_entry_id = embedding_data.get('entry_id')
                if stored_entry_id and stored_entry_id in db.message_mapping:
                    mapping_data = db.message_mapping[stored_entry_id]
                    content = mapping_data.get('content', content)
                    source_url = mapping_data.get('source_url')
                    original_entry_id = stored_entry_id
                
                logger.info(f"Found content in embeddings for {entry_id}")
        
        if not content:
            return {
                "success": False,
                "error": "No content found for this entry ID. Cannot reprocess without content."
            }
        
        # Check if content is too short (just preview)
        if len(content) < 100:
            logger.warning(f"Content is only {len(content)} chars, might be truncated preview")
        
        # Process through the bot pipeline
        logger.info(f"Reprocessing entry {entry_id} with content length: {len(content)}")
        
        # 1. Categorize the content
        category = ollama.categorize(content)
        logger.info(f"Categorized as: {category}")
        
        # 2. Check for duplicates (but don't block - just warn)
        embedding = ollama.generate_embedding(content)
        is_duplicate, similarity, match_preview = db.find_similar(
            embedding, 
            threshold=config.DUPLICATE_THRESHOLD
        )
        
        if is_duplicate:
            logger.warning(f"Content is a duplicate (similarity: {similarity:.3f}), but reprocessing anyway")
        
        # 3. Get the Discord channel for this category
        discord_channel_id = config.DISCORD_CHANNELS.get(category.lower())
        
        if not discord_channel_id:
            return {
                "success": False,
                "error": f"No Discord channel configured for category: {category}"
            }
        
        # 4. Post to Discord
        try:
            success, discord_message_id, returned_channel_id = await discord_poster.post_message(
                category=category,
                content=content,
                media_files=[],  # No media for manual reprocessing
                video_urls=None,
                source_type=None
            )
            
            if not success or not discord_message_id:
                return {
                    "success": False,
                    "error": "Failed to post message to Discord"
                }
            
            # 5. Store the new embedding and mark as processed
            new_entry_id = f"manual_reprocess_{int(time.time())}"
            db.add_embedding(content, embedding, entry_id=new_entry_id)
            db.mark_processed(new_entry_id)
            # Try to preserve video_urls from original entry if available
            original_video_urls = []
            if original_entry_id in db.message_mapping:
                original_mapping = db.message_mapping[original_entry_id]
                original_video_urls = original_mapping.get('video_urls', [])
            
            db.store_message_mapping(
                telegram_entry_id=new_entry_id,
                telegram_message_id=0,
                discord_channel_id=discord_channel_id,
                discord_message_id=discord_message_id,
                content=content,
                source_url=source_url,
                video_urls=original_video_urls
            )
            
            logger.info(f"Successfully reprocessed {entry_id} as {new_entry_id}")
            
            return {
                "success": True,
                "message": f"Entry reprocessed and posted to Discord",
                "category": category,
                "discord_message_id": discord_message_id,
                "duplicate_warning": f"Similarity: {similarity:.1%}" if is_duplicate else None,
                "new_entry_id": new_entry_id
            }
            
        except Exception as e:
            logger.error(f"Error posting to Discord: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Failed to post to Discord: {str(e)}"
            }
            
    except Exception as e:
        logger.error(f"Error reprocessing entry: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/api/database/export")
async def export_database(username: str = Depends(verify_credentials)):
    """Export database as JSON"""
    try:
        # Reload database to get fresh data
        db.processed_ids = db._load_json(db.processed_ids_path, {})
        db.embeddings = db._load_json(db.embeddings_path, {})
        db.message_mapping = db._load_json(db.message_mapping_path, {})
        
        export_data = {
            "processed_ids": db.processed_ids,
            "embeddings_count": len(db.embeddings),
            "message_mappings": db.message_mapping,
            "exported_at": time.time()
        }
        return JSONResponse(content=export_data)
    except Exception as e:
        logger.error(f"Error exporting database: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/api/sources")
async def get_sources(username: str = Depends(verify_credentials)):
    """Get list of RSS feeds and Telegram channels"""
    try:
        rss_feeds = [
            {"name": name, "url": url}
            for name, url in config.RSS_FEEDS.items()
        ]
        
        return {
            "rss_feeds": rss_feeds,
            "telegram_channels": config.TELEGRAM_CHANNELS
        }
    except Exception as e:
        logger.error(f"Error getting sources: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/api/logs")
async def get_logs(
    lines: int = 100,
    level: str = "",
    search: str = "",
    username: str = Depends(verify_credentials)
):
    """Get bot logs with filtering"""
    try:
        if not os.path.exists('bot.log'):
            return {"logs": [], "message": "Log file not found"}
        
        with open('bot.log', 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
        
        # Get last N lines
        log_lines = all_lines[-lines:]
        
        # Filter by level
        if level:
            log_lines = [line for line in log_lines if level in line]
        
        # Filter by search term
        if search:
            log_lines = [line for line in log_lines if search.lower() in line.lower()]
        
        # Strip newlines
        log_lines = [line.rstrip() for line in log_lines]
        
        return {"logs": log_lines}
    except Exception as e:
        logger.error(f"Error reading logs: {e}", exc_info=True)
        return {
            "logs": [],
            "error": str(e)
        }


@app.get("/api/config")
async def get_config(username: str = Depends(verify_credentials)):
    """Get current bot configuration"""
    try:
        return {
            "duplicate_threshold": config.DUPLICATE_THRESHOLD,
            "similarity_threshold": config.SIMILARITY_THRESHOLD,
            "poll_interval": config.POLL_INTERVAL,
            "db_retention_hours": config.DB_RETENTION_HOURS,
            "discord_channels": config.DISCORD_CHANNELS,
            "system_prompt": config.SYSTEM_PROMPT,
            "ollama_categorization_model": config.OLLAMA_CATEGORIZATION_MODEL,
            "ollama_embedding_model": config.OLLAMA_EMBEDDING_MODEL
        }
    except Exception as e:
        logger.error(f"Error getting config: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/api/database/search")
async def search_database(q: str, username: str = Depends(verify_credentials)):
    """Search for entry by ID or text content"""
    try:
        # Reload database to get fresh data
        db.processed_ids = db._load_json(db.processed_ids_path, {})
        db.embeddings = db._load_json(db.embeddings_path, {})
        db.message_mapping = db._load_json(db.message_mapping_path, {})
        
        results = []
        query_lower = q.lower()
        
        # Check for exact entry ID match
        if q in db.processed_ids:
            # Try to get source_url from message mapping if available
            mapping_data = db.message_mapping.get(q, {})
            results.append({
                "entry_id": q,
                "timestamp": db.processed_ids[q],
                "match_type": "exact_id",
                "preview": None,
                "source_url": mapping_data.get('source_url')
            })
        
        # Search in processed IDs (partial match)
        if not results:
            for entry_id in db.processed_ids.keys():
                if query_lower in entry_id.lower():
                    # Try to get source_url from message mapping if available
                    mapping_data = db.message_mapping.get(entry_id, {})
                    results.append({
                        "entry_id": entry_id,
                        "timestamp": db.processed_ids[entry_id],
                        "match_type": "partial_id",
                        "preview": None,
                        "source_url": mapping_data.get('source_url')
                    })
        
        # Search in message mapping content
        for entry_id, mapping_data in db.message_mapping.items():
            if 'content' in mapping_data and mapping_data['content']:
                if query_lower in mapping_data['content'].lower():
                    # Only add if not already in results
                    if not any(r['entry_id'] == entry_id for r in results):
                        results.append({
                            "entry_id": entry_id,
                            "timestamp": mapping_data.get('timestamp', 0),
                            "match_type": "content",
                            "preview": mapping_data['content'][:200] + "..." if len(mapping_data['content']) > 200 else mapping_data['content'],
                            "source_url": mapping_data.get('source_url')
                        })
        
        # Search in embeddings preview text
        for hash_key, embedding_data in db.embeddings.items():
            if 'preview' in embedding_data and embedding_data['preview']:
                if query_lower in embedding_data['preview'].lower():
                    preview = embedding_data['preview']
                    stored_entry_id = embedding_data.get('entry_id')
                    
                    # Skip if we already have this entry in results
                    if stored_entry_id and any(r['entry_id'] == stored_entry_id for r in results):
                        continue
                    
                    # Use stored entry_id if available, otherwise use hash
                    display_entry_id = stored_entry_id or f"embedding_{hash_key[:12]}"
                    
                    # Try to get source_url from message_mapping if we have the entry_id
                    source_url = None
                    if stored_entry_id:
                        mapping_data = db.message_mapping.get(stored_entry_id, {})
                        source_url = mapping_data.get('source_url')
                    
                    results.append({
                        "entry_id": display_entry_id,
                        "timestamp": embedding_data.get('timestamp', 0),
                        "match_type": "embedding_preview",
                        "preview": preview,
                        "source_url": source_url
                    })
        
        # Sort by timestamp (most recent first) and limit to 20 results
        results = sorted(results, key=lambda x: x['timestamp'], reverse=True)[:20]
        
        if results:
            return {
                "found": True,
                "count": len(results),
                "results": results
            }
        else:
            return {
                "found": False,
                "query": q
            }
    except Exception as e:
        logger.error(f"Error searching database: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


def find_media_files_for_entry(entry_id: str, source_type: str, telegram_message_id: int = None):
    """
    Find media files for an entry in the temp_media directory
    Note: Videos are not downloaded, only video URLs are stored in message_mapping for Twitter entries
    
    Args:
        entry_id: Full entry ID (e.g., 'twitter_123' or 'telegram_channel_456')
        source_type: Source type ('twitter' or 'telegram')
        telegram_message_id: Telegram message ID if available
    
    Returns:
        dict: {'images': [list of image URLs], 'videos': []}
        Note: videos list is empty here - video URLs come from message_mapping
    """
    from pathlib import Path
    
    media_info = {
        'images': [],
        'videos': []  # Videos are not downloaded, only URLs stored in message_mapping
    }
    
    temp_media_dir = Path("temp_media")
    if not temp_media_dir.exists():
        return media_info
    
    # Image extensions only (videos are not downloaded)
    image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']
    
    # For Twitter entries: temp_media/twitter_{status_id}/
    if source_type == 'twitter':
        # Extract status_id from entry_id (format: twitter_{status_id})
        parts = entry_id.split('_', 1)
        if len(parts) == 2:
            status_id = parts[1]
            media_dir = temp_media_dir / f"twitter_{status_id}"
            
            # Also check nested directories (gallery-dl sometimes creates nested structure)
            if media_dir.exists():
                # Use rglob to recursively find all image files
                for ext in image_extensions:
                    for file_path in media_dir.rglob(f"*{ext}"):
                        if file_path.is_file():
                            # Get relative path from temp_media_dir
                            rel_path = file_path.relative_to(temp_media_dir)
                            url = f"/api/temp-media/{rel_path.as_posix()}"
                            media_info['images'].append(url)
    
    # For Telegram entries: temp_media/telegram_{message_id}/
    elif source_type == 'telegram' and telegram_message_id:
        media_dir = temp_media_dir / f"telegram_{telegram_message_id}"
        
        if media_dir.exists():
            # Video extensions (Telegram videos are downloaded as files)
            video_extensions = ['.mp4', '.mov', '.avi', '.webm', '.mkv']
            
            # Find images
            for ext in image_extensions:
                for file_path in media_dir.glob(f"*{ext}"):
                    # Get relative path from temp_media_dir
                    rel_path = file_path.relative_to(temp_media_dir)
                    url = f"/api/temp-media/{rel_path.as_posix()}"
                    media_info['images'].append(url)
            
            # Find videos (Telegram videos are downloaded as files)
            for ext in video_extensions:
                for file_path in media_dir.glob(f"*{ext}"):
                    # Get relative path from temp_media_dir
                    rel_path = file_path.relative_to(temp_media_dir)
                    url = f"/api/temp-media/{rel_path.as_posix()}"
                    media_info['videos'].append(url)
    
    return media_info


@app.get("/api/entry/{entry_id}")
async def get_entry_details(entry_id: str, username: str = Depends(verify_credentials)):
    """Get full details for a specific entry ID"""
    try:
        # Reload database to get fresh data
        db.processed_ids = db._load_json(db.processed_ids_path, {})
        db.embeddings = db._load_json(db.embeddings_path, {})
        db.message_mapping = db._load_json(db.message_mapping_path, {})
        
        # Initialize result structure
        result = {
            "entry_id": entry_id,
            "found": False,
            "timestamp": None,
            "source_type": None,
            "content": None,
            "source_url": None,
            "discord_channel_id": None,
            "discord_message_id": None,
            "telegram_message_id": None,
            "embedding_info": None,
            "media": {
                "images": [],
                "videos": []
            }
        }
        
        # Check if entry exists in processed_ids
        if entry_id in db.processed_ids:
            result["found"] = True
            result["timestamp"] = db.processed_ids[entry_id]
            
            # Parse source type from entry_id
            parts = entry_id.split('_')
            result["source_type"] = parts[0] if parts else 'unknown'
            
            # Get message mapping data if available
            telegram_message_id = None
            if entry_id in db.message_mapping:
                mapping_data = db.message_mapping[entry_id]
                result["content"] = mapping_data.get('content')
                result["source_url"] = mapping_data.get('source_url')
                result["discord_channel_id"] = mapping_data.get('discord_channel_id')
                result["discord_message_id"] = mapping_data.get('discord_message_id')
                result["telegram_message_id"] = mapping_data.get('telegram_message_id')
                telegram_message_id = mapping_data.get('telegram_message_id')
                # Get video URLs from message mapping (for Twitter entries)
                video_urls = mapping_data.get('video_urls', [])
                if video_urls:
                    result["media"]["videos"] = video_urls
            
            # Check for embedding info
            # Look for embedding with matching entry_id
            for hash_key, embedding_data in db.embeddings.items():
                if embedding_data.get('entry_id') == entry_id:
                    result["embedding_info"] = {
                        "hash": hash_key,
                        "preview": embedding_data.get('preview'),
                        "timestamp": embedding_data.get('timestamp')
                    }
                    break
            
            # If no embedding found by entry_id, check if entry_id is an embedding hash
            if not result["embedding_info"] and entry_id.startswith("embedding_"):
                hash_prefix = entry_id.replace("embedding_", "")
                matching_hashes = [h for h in db.embeddings.keys() if h.startswith(hash_prefix)]
                if matching_hashes:
                    hash_key = matching_hashes[0]
                    embedding_data = db.embeddings[hash_key]
                    result["embedding_info"] = {
                        "hash": hash_key,
                        "preview": embedding_data.get('preview'),
                        "timestamp": embedding_data.get('timestamp')
                    }
                    # Try to get linked entry_id
                    linked_entry_id = embedding_data.get('entry_id')
                    if linked_entry_id and linked_entry_id in db.message_mapping:
                        mapping_data = db.message_mapping[linked_entry_id]
                        result["content"] = mapping_data.get('content')
                        result["source_url"] = mapping_data.get('source_url')
                        result["discord_channel_id"] = mapping_data.get('discord_channel_id')
                        result["discord_message_id"] = mapping_data.get('discord_message_id')
                        result["telegram_message_id"] = mapping_data.get('telegram_message_id')
                        telegram_message_id = mapping_data.get('telegram_message_id')
                        # Get video URLs from message mapping (for Twitter entries)
                        video_urls = mapping_data.get('video_urls', [])
                        if video_urls:
                            result["media"]["videos"] = video_urls
            
            # Find media files (images and videos from temp_media)
            media_info = find_media_files_for_entry(
                entry_id, 
                result["source_type"], 
                telegram_message_id
            )
            
            # Merge media info:
            # - Images always come from temp_media (for both Twitter and Telegram)
            # - Videos: Twitter entries use URLs from message_mapping, Telegram entries use files from temp_media
            result["media"]["images"] = media_info['images']
            
            # For Twitter entries, video URLs come from message_mapping (already set above)
            # For Telegram entries, videos are downloaded files (from temp_media)
            if result["source_type"] == 'telegram':
                # Telegram videos are downloaded files, use from media_info
                result["media"]["videos"] = media_info['videos']
            # For Twitter, videos are already set from message_mapping above, so keep those
        
        if not result["found"]:
            return {
                "found": False,
                "entry_id": entry_id,
                "error": "Entry not found"
            }
        
        return result
        
    except Exception as e:
        logger.error(f"Error getting entry details: {e}", exc_info=True)
        return {
            "found": False,
            "entry_id": entry_id,
            "error": str(e)
        }


# Helper functions for manual URL processing

def parse_url(url: str) -> dict:
    """
    Parse URL to determine type and extract identifiers
    
    Args:
        url: Twitter/X or Telegram URL
    
    Returns:
        dict: {'type': 'twitter'|'telegram', 'data': {...}} or {'error': str}
    """
    url = url.strip()
    
    # Twitter/X URL patterns
    twitter_patterns = [
        r'(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/\w+/status/(\d+)',
        r'(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/\w+/statuses/(\d+)',
    ]
    
    for pattern in twitter_patterns:
        match = re.search(pattern, url)
        if match:
            status_id = match.group(1)
            return {
                'type': 'twitter',
                'data': {
                    'status_id': status_id,
                    'url': url if url.startswith('http') else f'https://twitter.com/i/status/{status_id}'
                }
            }
    
    # Telegram URL pattern: t.me/channel_name/message_id
    telegram_pattern = r'(?:https?://)?t\.me/([^/]+)/(\d+)'
    match = re.search(telegram_pattern, url)
    if match:
        channel_name = match.group(1)
        message_id = int(match.group(2))
        return {
            'type': 'telegram',
            'data': {
                'channel': channel_name,
                'message_id': message_id,
                'url': url if url.startswith('http') else f'https://t.me/{channel_name}/{message_id}'
            }
        }
    
    return {'error': 'Invalid URL format. Please provide a Twitter/X status URL or Telegram message URL (t.me/channel/id)'}


def process_twitter_url(status_id: str, url: str) -> dict:
    """
    Process a Twitter URL through the pipeline (synchronous)
    
    Args:
        status_id: Twitter status ID
        url: Full Twitter URL
    
    Returns:
        dict: Processing results and debug information
    """
    result = {
        'source_type': 'twitter',
        'status_id': status_id,
        'url': url,
        'steps': []
    }
    
    try:
        # Step 1: Create entry structure
        step_start = time.time()
        entry = {
            'id': f'manual_twitter_{status_id}',
            'status_id': status_id,
            'source': 'manual',
            'source_type': 'twitter',
            'link': url,
            'content': ''
        }
        result['steps'].append({
            'name': 'Entry Creation',
            'status': 'success',
            'duration': time.time() - step_start,
            'data': {'entry_id': entry['id']}
        })
        
        # Step 2: Download media and extract text
        step_start = time.time()
        try:
            logger.debug("Calling media_handler.download_twitter_media...")
            entry = media_handler.download_twitter_media(entry)
            logger.debug("media_handler.download_twitter_media returned successfully")
            result['steps'].append({
                'name': 'Media Download & Text Extraction',
                'status': 'success',
                'duration': time.time() - step_start,
                'data': {
                    'text_length': len(entry.get('full_text', '')),
                    'media_count': len(entry.get('media_files', [])),
                    'video_count': len(entry.get('video_urls', [])),
                    'ocr_length': len(entry.get('ocr_text', ''))
                }
            })
            logger.debug("Media download step completed")
        except Exception as e:
            logger.error(f"Media download failed: {e}", exc_info=True)
            result['steps'].append({
                'name': 'Media Download & Text Extraction',
                'status': 'error',
                'duration': time.time() - step_start,
                'error': str(e)
            })
            raise
        
        # Store extracted content
        result['content'] = entry.get('full_text', entry.get('content', ''))
        result['ocr_text'] = entry.get('ocr_text', '')
        result['media_files'] = len(entry.get('media_files', []))
        result['video_urls'] = entry.get('video_urls', [])
        
        # Copy media files to serve directory and create URLs
        media_urls = []
        if entry.get('media_files'):
            # Create a unique directory for this request
            serve_subdir = MEDIA_SERVE_DIR / f"manual_{int(time.time())}_{status_id}"
            serve_subdir.mkdir(exist_ok=True)
            
            for i, media_file in enumerate(entry.get('media_files', [])):
                if os.path.exists(media_file):
                    # Get file extension
                    ext = os.path.splitext(media_file)[1] or '.jpg'
                    # Copy to serve directory with a clean name
                    serve_filename = f"image_{i+1}{ext}"
                    serve_path = serve_subdir / serve_filename
                    shutil.copy2(media_file, serve_path)
                    # Create URL path
                    media_urls.append(f"/api/media/{serve_subdir.name}/{serve_filename}")
            
            result['media_urls'] = media_urls
        else:
            result['media_urls'] = []
        
        # Combine content with OCR for better categorization
        combined_content = result['content']
        if result['ocr_text']:
            combined_content = f"{result['content']}\n\n[Text from images]:\n{result['ocr_text']}"
        
        # Step 3: Generate embedding
        step_start = time.time()
        try:
            logger.debug("Generating embedding...")
            embedding = ollama.generate_embedding(combined_content)
            logger.debug(f"Embedding generated: {len(embedding)} dimensions")
            result['steps'].append({
                'name': 'Generate Embedding',
                'status': 'success',
                'duration': time.time() - step_start,
                'data': {'embedding_length': len(embedding)}
            })
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            result['steps'].append({
                'name': 'Generate Embedding',
                'status': 'error',
                'duration': time.time() - step_start,
                'error': str(e)
            })
            raise
        
        # Step 4: Check for duplicates
        step_start = time.time()
        try:
            logger.debug("Checking for duplicates...")
            is_duplicate, duplicate_similarity, match_preview = db.find_similar(
                embedding, 
                threshold=config.DUPLICATE_THRESHOLD
            )
            logger.debug(f"Duplicate check: {is_duplicate}")
            
            logger.debug("Checking for similar content...")
            is_similar, similar_similarity, similar_preview = db.find_similar(
                embedding,
                threshold=config.SIMILARITY_THRESHOLD
            )
            logger.debug(f"Similar check: {is_similar}")
            
            result['duplicate_check'] = {
                'is_duplicate': is_duplicate,
                'duplicate_similarity': duplicate_similarity,
                'duplicate_match': match_preview if is_duplicate else None,
                'is_similar': is_similar and not is_duplicate,
                'similar_similarity': similar_similarity if is_similar else 0.0,
                'similar_match': similar_preview if is_similar else None
            }
            
            result['steps'].append({
                'name': 'Duplicate Detection',
                'status': 'success',
                'duration': time.time() - step_start,
                'data': result['duplicate_check']
            })
        except Exception as e:
            logger.error(f"Duplicate check failed: {e}")
            result['steps'].append({
                'name': 'Duplicate Detection',
                'status': 'error',
                'duration': time.time() - step_start,
                'error': str(e)
            })
            raise
        
        # Step 5: Categorize content
        step_start = time.time()
        try:
            logger.debug("Categorizing content...")
            category = ollama.categorize(combined_content)
            logger.debug(f"Categorized as: {category}")
            result['category'] = category
            result['steps'].append({
                'name': 'Categorization',
                'status': 'success',
                'duration': time.time() - step_start,
                'data': {'category': category}
            })
        except Exception as e:
            logger.error(f"Categorization failed: {e}")
            result['steps'].append({
                'name': 'Categorization',
                'status': 'error',
                'duration': time.time() - step_start,
                'error': str(e)
            })
            raise
        
        # Don't cleanup media files immediately - keep for 2 days
        # Cleanup will be handled by periodic cleanup task in main.py
        
        result['success'] = True
        return result
        
    except Exception as e:
        result['success'] = False
        result['error'] = str(e)
        return result


async def process_telegram_url(channel: str, message_id: int, url: str) -> dict:
    """
    Process a Telegram URL through the pipeline
    
    Args:
        channel: Telegram channel username
        message_id: Message ID
        url: Full Telegram URL
    
    Returns:
        dict: Processing results and debug information
    """
    global telegram_poller_instance
    
    result = {
        'source_type': 'telegram',
        'channel': channel,
        'message_id': message_id,
        'url': url,
        'steps': []
    }
    
    try:
        # Initialize Telegram client if needed
        if not telegram_poller_instance:
            step_start = time.time()
            try:
                telegram_poller_instance = TelegramPoller()
                await telegram_poller_instance.start()
                # Update media_handler with telegram client
                media_handler.telegram_client = telegram_poller_instance
                result['steps'].append({
                    'name': 'Telegram Client Initialization',
                    'status': 'success',
                    'duration': time.time() - step_start
                })
            except Exception as e:
                result['steps'].append({
                    'name': 'Telegram Client Initialization',
                    'status': 'error',
                    'duration': time.time() - step_start,
                    'error': str(e)
                })
                raise
        
        # Step 1: Fetch message from Telegram
        step_start = time.time()
        try:
            # Get the channel entity
            entity = await telegram_poller_instance.client.get_entity(channel)
            
            # Fetch the specific message
            message = await telegram_poller_instance.client.get_messages(entity, ids=message_id)
            
            if not message:
                raise Exception(f"Message {message_id} not found in channel {channel}")
            
            # Parse the message
            entry = await telegram_poller_instance._parse_message(message, channel)
            
            result['steps'].append({
                'name': 'Fetch Telegram Message',
                'status': 'success',
                'duration': time.time() - step_start,
                'data': {
                    'entry_id': entry['id'],
                    'has_media': entry.get('has_media', False),
                    'text_length': len(entry.get('content', ''))
                }
            })
        except Exception as e:
            result['steps'].append({
                'name': 'Fetch Telegram Message',
                'status': 'error',
                'duration': time.time() - step_start,
                'error': str(e)
            })
            raise
        
        # Step 2: Download media if present
        step_start = time.time()
        try:
            if entry.get('has_media'):
                entry = await media_handler.download_telegram_media(entry)
            
            result['steps'].append({
                'name': 'Media Download & OCR',
                'status': 'success',
                'duration': time.time() - step_start,
                'data': {
                    'media_count': len(entry.get('media_files', [])),
                    'ocr_length': len(entry.get('ocr_text', ''))
                }
            })
        except Exception as e:
            result['steps'].append({
                'name': 'Media Download & OCR',
                'status': 'error',
                'duration': time.time() - step_start,
                'error': str(e)
            })
            raise
        
        # Store extracted content
        result['content'] = entry.get('content', '')
        result['ocr_text'] = entry.get('ocr_text', '')
        result['media_files'] = len(entry.get('media_files', []))
        result['video_urls'] = entry.get('video_urls', [])
        
        # Copy media files to serve directory and create URLs
        media_urls = []
        if entry.get('media_files'):
            # Create a unique directory for this request
            serve_subdir = MEDIA_SERVE_DIR / f"manual_{int(time.time())}_{channel}_{message_id}"
            serve_subdir.mkdir(exist_ok=True)
            
            for i, media_file in enumerate(entry.get('media_files', [])):
                if os.path.exists(media_file):
                    # Get file extension
                    ext = os.path.splitext(media_file)[1] or '.jpg'
                    # Copy to serve directory with a clean name
                    serve_filename = f"image_{i+1}{ext}"
                    serve_path = serve_subdir / serve_filename
                    shutil.copy2(media_file, serve_path)
                    # Create URL path
                    media_urls.append(f"/api/media/{serve_subdir.name}/{serve_filename}")
            
            result['media_urls'] = media_urls
        else:
            result['media_urls'] = []
        
        # Combine content with OCR
        combined_content = result['content']
        if result['ocr_text']:
            combined_content = f"{result['content']}\n\n[Text from images]:\n{result['ocr_text']}"
        
        # Step 3: Generate embedding
        step_start = time.time()
        try:
            embedding = ollama.generate_embedding(combined_content)
            result['steps'].append({
                'name': 'Generate Embedding',
                'status': 'success',
                'duration': time.time() - step_start,
                'data': {'embedding_length': len(embedding)}
            })
        except Exception as e:
            result['steps'].append({
                'name': 'Generate Embedding',
                'status': 'error',
                'duration': time.time() - step_start,
                'error': str(e)
            })
            raise
        
        # Step 4: Check for duplicates
        step_start = time.time()
        try:
            is_duplicate, duplicate_similarity, match_preview = db.find_similar(
                embedding, 
                threshold=config.DUPLICATE_THRESHOLD
            )
            
            is_similar, similar_similarity, similar_preview = db.find_similar(
                embedding,
                threshold=config.SIMILARITY_THRESHOLD
            )
            
            result['duplicate_check'] = {
                'is_duplicate': is_duplicate,
                'duplicate_similarity': duplicate_similarity,
                'duplicate_match': match_preview if is_duplicate else None,
                'is_similar': is_similar and not is_duplicate,
                'similar_similarity': similar_similarity if is_similar else 0.0,
                'similar_match': similar_preview if is_similar else None
            }
            
            result['steps'].append({
                'name': 'Duplicate Detection',
                'status': 'success',
                'duration': time.time() - step_start,
                'data': result['duplicate_check']
            })
        except Exception as e:
            result['steps'].append({
                'name': 'Duplicate Detection',
                'status': 'error',
                'duration': time.time() - step_start,
                'error': str(e)
            })
            raise
        
        # Step 5: Categorize content
        step_start = time.time()
        try:
            category = ollama.categorize(combined_content)
            result['category'] = category
            result['steps'].append({
                'name': 'Categorization',
                'status': 'success',
                'duration': time.time() - step_start,
                'data': {'category': category}
            })
        except Exception as e:
            result['steps'].append({
                'name': 'Categorization',
                'status': 'error',
                'duration': time.time() - step_start,
                'error': str(e)
            })
            raise
        
        # Don't cleanup media files immediately - keep for 2 days
        # Cleanup will be handled by periodic cleanup task in main.py
        
        result['success'] = True
        return result
        
    except Exception as e:
        result['success'] = False
        result['error'] = str(e)
        return result


@app.post("/api/process-url")
async def process_url(
    url: str = Form(...),
    username: str = Depends(verify_credentials)
):
    """
    Process a Twitter or Telegram URL through the full pipeline without posting to Discord
    
    Returns comprehensive debug information including:
    - Extracted content
    - Media download status
    - OCR text
    - Categorization result
    - Duplicate detection results
    - Processing steps and timing
    """
    try:
        logger.info(f"Manual URL processing requested: {url}")
        
        # Parse the URL
        parsed = parse_url(url)
        
        if 'error' in parsed:
            return {
                'success': False,
                'error': parsed['error']
            }
        
        # Process based on URL type
        if parsed['type'] == 'twitter':
            # Run synchronous Twitter processing in thread pool with timeout
            import asyncio
            loop = asyncio.get_event_loop()
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        process_twitter_url,
                        parsed['data']['status_id'],
                        parsed['data']['url']
                    ),
                    timeout=120.0  # 2 minute timeout
                )
            except asyncio.TimeoutError:
                logger.error("Twitter URL processing timed out after 120 seconds")
                return {
                    'success': False,
                    'error': 'Processing timed out after 2 minutes. This usually means Ollama is slow or unresponsive.'
                }
        elif parsed['type'] == 'telegram':
            try:
                result = await asyncio.wait_for(
                    process_telegram_url(
                        parsed['data']['channel'],
                        parsed['data']['message_id'],
                        parsed['data']['url']
                    ),
                    timeout=120.0  # 2 minute timeout
                )
            except asyncio.TimeoutError:
                logger.error("Telegram URL processing timed out after 120 seconds")
                return {
                    'success': False,
                    'error': 'Processing timed out after 2 minutes. This usually means Telegram client is slow or unresponsive.'
                }
        else:
            return {
                'success': False,
                'error': f"Unsupported URL type: {parsed['type']}"
            }
        
        logger.info(f"Manual URL processing completed: {result.get('success', False)}")
        return result
        
    except Exception as e:
        logger.error(f"Error processing URL: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }


@app.get("/api/media/{subdir}/{filename}")
async def serve_media(
    subdir: str,
    filename: str,
    username: str = Depends(verify_credentials)
):
    """
    Serve media files from the temporary serve directory
    """
    try:
        file_path = MEDIA_SERVE_DIR / subdir / filename
        
        # Security check - ensure path is within serve directory
        if not str(file_path.resolve()).startswith(str(MEDIA_SERVE_DIR.resolve())):
            raise HTTPException(status_code=403, detail="Access denied")
        
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        
        return FileResponse(
            path=str(file_path),
            media_type="image/jpeg" if filename.lower().endswith(('.jpg', '.jpeg')) else
                      "image/png" if filename.lower().endswith('.png') else
                      "image/gif" if filename.lower().endswith('.gif') else
                      "image/webp" if filename.lower().endswith('.webp') else
                      "application/octet-stream"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving media file: {e}")
        raise HTTPException(status_code=500, detail="Error serving file")


@app.get("/api/temp-media/{path:path}")
async def serve_temp_media(
    path: str,
    username: str = Depends(verify_credentials)
):
    """
    Serve media files from the temp_media directory
    Path should be relative to temp_media (e.g., twitter_123/image.jpg)
    """
    try:
        from pathlib import Path
        temp_media_dir = Path("temp_media")
        file_path = temp_media_dir / path
        
        # Security check - ensure path is within temp_media directory
        if not str(file_path.resolve()).startswith(str(temp_media_dir.resolve())):
            raise HTTPException(status_code=403, detail="Access denied")
        
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        
        # Determine media type
        filename_lower = file_path.name.lower()
        if filename_lower.endswith(('.jpg', '.jpeg')):
            media_type = "image/jpeg"
        elif filename_lower.endswith('.png'):
            media_type = "image/png"
        elif filename_lower.endswith('.gif'):
            media_type = "image/gif"
        elif filename_lower.endswith('.webp'):
            media_type = "image/webp"
        elif filename_lower.endswith('.mp4'):
            media_type = "video/mp4"
        elif filename_lower.endswith('.webm'):
            media_type = "video/webm"
        elif filename_lower.endswith('.mov'):
            media_type = "video/quicktime"
        else:
            media_type = "application/octet-stream"
        
        return FileResponse(
            path=str(file_path),
            media_type=media_type
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving temp media file: {e}")
        raise HTTPException(status_code=500, detail="Error serving file")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

