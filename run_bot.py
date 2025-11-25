"""
Bot Launcher Script

Run this script to start the Discord News Aggregator Bot.
The dashboard should be run separately using run_dashboard.py
"""
import asyncio
import sys
from utils import logger, setup_logging
from main import main

if __name__ == "__main__":
    setup_logging()
    logger.info("=" * 80)
    logger.info("Starting Discord News Aggregator Bot (Standalone)")
    logger.info("=" * 80)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\nBot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

