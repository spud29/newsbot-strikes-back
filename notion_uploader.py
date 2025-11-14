"""
Notion API client for uploading bot statistics
"""
from notion_client import Client
from datetime import datetime
from utils import logger
import config

class NotionStatsUploader:
    """Handles uploading statistics to Notion"""
    
    def __init__(self):
        """Initialize Notion client"""
        self.enabled = bool(config.NOTION_TOKEN and config.NOTION_PAGE_ID)
        
        if self.enabled:
            try:
                self.client = Client(auth=config.NOTION_TOKEN)
                self.page_id = config.NOTION_PAGE_ID
                logger.info("Notion client initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize Notion client: {e}")
                self.enabled = False
        else:
            logger.warning("Notion integration disabled - missing token or page ID")
    
    def upload_stats(self, stats_data):
        """
        Upload statistics to Notion page
        
        Args:
            stats_data: Dictionary containing all statistics
        
        Returns:
            bool: True if successful
        """
        if not self.enabled:
            logger.warning("Notion upload skipped - integration not enabled")
            return False
        
        try:
            all_time = stats_data.get('all_time', {})
            hourly = stats_data.get('hourly', [])
            daily = stats_data.get('daily', [])
            last_updated = stats_data.get('last_updated', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            
            # Calculate last 24h totals
            last_24h = {
                'processed': 0,
                'duplicates': 0,
                'errors': 0,
                'images': 0,
                'videos': 0,
                'ocr_extractions': 0
            }
            
            for hourly_stat in hourly[-24:]:
                last_24h['processed'] += hourly_stat.get('processed', 0)
                last_24h['duplicates'] += hourly_stat.get('duplicates', 0)
                last_24h['errors'] += hourly_stat.get('errors', 0)
                media = hourly_stat.get('media', {})
                last_24h['images'] += media.get('images', 0)
                last_24h['videos'] += media.get('videos', 0)
                last_24h['ocr_extractions'] += media.get('ocr_extractions', 0)
            
            # Build content blocks for the Notion page
            blocks = self._build_notion_blocks(all_time, last_24h, hourly, daily, last_updated)
            
            # Clear existing page content and add new content
            self._update_page_content(blocks)
            
            logger.info(f"Successfully uploaded statistics to Notion (Page ID: {self.page_id})")
            return True
            
        except Exception as e:
            logger.error(f"Error uploading statistics to Notion: {e}", exc_info=True)
            return False
    
    def _build_notion_blocks(self, all_time, last_24h, hourly, daily, last_updated):
        """Build Notion blocks from statistics data"""
        blocks = []
        
        # Title
        blocks.append({
            "object": "block",
            "type": "heading_1",
            "heading_1": {
                "rich_text": [{"type": "text", "text": {"content": "📊 Bot Statistics Dashboard"}}]
            }
        })
        
        # Last updated
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"type": "text", "text": {"content": f"Last Updated: {last_updated}", "link": None}}
                ]
            }
        })
        
        # Divider
        blocks.append({"object": "block", "type": "divider", "divider": {}})
        
        # All-Time Summary
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": "🌟 All-Time Summary"}}]
            }
        })
        
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": f"Total Processed: {all_time.get('processed', 0):,}"}}]
            }
        })
        
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": f"Duplicates Detected: {all_time.get('duplicates', 0):,}"}}]
            }
        })
        
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": f"Errors: {all_time.get('errors', 0):,}"}}]
            }
        })
        
        media = all_time.get('media', {})
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": f"Images Downloaded: {media.get('images', 0):,}"}}]
            }
        })
        
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": f"Videos Downloaded: {media.get('videos', 0):,}"}}]
            }
        })
        
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": f"OCR Extractions: {media.get('ocr_extractions', 0):,}"}}]
            }
        })
        
        perf = all_time.get('performance', {})
        avg_time = perf.get('avg_processing_time', 0)
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": f"Average Processing Time: {avg_time:.2f}s per entry"}}]
            }
        })
        
        # Divider
        blocks.append({"object": "block", "type": "divider", "divider": {}})
        
        # Last 24 Hours
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": "📅 Last 24 Hours"}}]
            }
        })
        
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": f"Processed: {last_24h['processed']:,}"}}]
            }
        })
        
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": f"Duplicates: {last_24h['duplicates']:,}"}}]
            }
        })
        
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": f"Errors: {last_24h['errors']:,}"}}]
            }
        })
        
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": f"Images: {last_24h['images']:,}"}}]
            }
        })
        
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": f"Videos: {last_24h['videos']:,}"}}]
            }
        })
        
        # Divider
        blocks.append({"object": "block", "type": "divider", "divider": {}})
        
        # Top Categories
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": "📂 Top Categories"}}]
            }
        })
        
        by_category = all_time.get('by_category', {})
        sorted_categories = sorted(by_category.items(), key=lambda x: x[1], reverse=True)[:10]
        
        if sorted_categories:
            for category, count in sorted_categories:
                blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"type": "text", "text": {"content": f"{category}: {count:,}"}}]
                    }
                })
        else:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": "No data yet"}}]
                }
            })
        
        # Divider
        blocks.append({"object": "block", "type": "divider", "divider": {}})
        
        # Top Sources
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": "📡 Top Sources"}}]
            }
        })
        
        # RSS Sources
        blocks.append({
            "object": "block",
            "type": "heading_3",
            "heading_3": {
                "rich_text": [{"type": "text", "text": {"content": "RSS Feeds"}}]
            }
        })
        
        by_source = all_time.get('by_source', {})
        rss_sources = by_source.get('rss', {})
        sorted_rss = sorted(rss_sources.items(), key=lambda x: x[1], reverse=True)
        
        if sorted_rss:
            for source, count in sorted_rss:
                blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"type": "text", "text": {"content": f"{source}: {count:,}"}}]
                    }
                })
        else:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": "No data yet"}}]
                }
            })
        
        # Telegram Sources
        blocks.append({
            "object": "block",
            "type": "heading_3",
            "heading_3": {
                "rich_text": [{"type": "text", "text": {"content": "Telegram Channels"}}]
            }
        })
        
        telegram_sources = by_source.get('telegram', {})
        sorted_telegram = sorted(telegram_sources.items(), key=lambda x: x[1], reverse=True)
        
        if sorted_telegram:
            for source, count in sorted_telegram:
                blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"type": "text", "text": {"content": f"{source}: {count:,}"}}]
                    }
                })
        else:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": "No data yet"}}]
                }
            })
        
        # Divider
        blocks.append({"object": "block", "type": "divider", "divider": {}})
        
        # Recent Daily Stats
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": "📊 Last 7 Days"}}]
            }
        })
        
        recent_daily = daily[-7:] if len(daily) > 7 else daily
        
        if recent_daily:
            for day_stat in recent_daily:
                date = day_stat.get('date', 'Unknown')
                processed = day_stat.get('processed', 0)
                duplicates = day_stat.get('duplicates', 0)
                errors = day_stat.get('errors', 0)
                
                blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"type": "text", "text": {"content": f"{date}: {processed} processed, {duplicates} duplicates, {errors} errors"}}]
                    }
                })
        else:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": "No daily data yet"}}]
                }
            })
        
        return blocks
    
    def _update_page_content(self, blocks):
        """Update Notion page content with new blocks"""
        # First, get all existing blocks in the page
        existing_blocks = []
        try:
            response = self.client.blocks.children.list(block_id=self.page_id)
            existing_blocks = response.get('results', [])
        except Exception as e:
            logger.warning(f"Could not retrieve existing blocks: {e}")
        
        # Delete existing blocks
        for block in existing_blocks:
            try:
                self.client.blocks.delete(block_id=block['id'])
            except Exception as e:
                logger.warning(f"Could not delete block {block['id']}: {e}")
        
        # Add new blocks in batches (Notion has a limit of 100 blocks per request)
        batch_size = 100
        for i in range(0, len(blocks), batch_size):
            batch = blocks[i:i + batch_size]
            try:
                self.client.blocks.children.append(
                    block_id=self.page_id,
                    children=batch
                )
            except Exception as e:
                logger.error(f"Error appending blocks batch {i // batch_size + 1}: {e}")
                raise

