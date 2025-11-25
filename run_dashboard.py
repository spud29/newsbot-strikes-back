"""
Dashboard Launcher Script

Run this script to start the web dashboard for monitoring and managing the bot.
The bot itself should be run separately using run_bot.py
"""
import subprocess
import sys
import os
import signal
import time
from utils import logger, setup_logging

# Process references
uvicorn_process = None
ngrok_process = None

def start_uvicorn():
    """Start uvicorn server for dashboard"""
    global uvicorn_process
    try:
        logger.info("Starting uvicorn dashboard server...")
        # Start uvicorn as a subprocess
        # Don't capture stdout/stderr so logs appear in terminal
        uvicorn_process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "dashboard:app", "--host", "0.0.0.0", "--port", "8000"],
            stdout=None,  # Let it go to terminal
            stderr=None,  # Let it go to terminal
            cwd=os.getcwd()
        )
        logger.info("✓ Dashboard server started on http://0.0.0.0:8000")
        return True
    except Exception as e:
        logger.error(f"Failed to start uvicorn: {e}")
        return False

def start_ngrok():
    """Start ngrok tunnel for remote access"""
    global ngrok_process
    try:
        logger.info("Starting ngrok tunnel...")
        # Start ngrok as a subprocess
        ngrok_process = subprocess.Popen(
            ["ngrok", "http", "8000"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        logger.info("✓ Ngrok tunnel started (check http://127.0.0.1:4040 for public URL)")
        return True
    except Exception as e:
        logger.error(f"Failed to start ngrok: {e}")
        logger.info("Note: Make sure ngrok is installed and in your PATH")
        logger.info("You can skip ngrok if you only need local access")
        return False

def stop_uvicorn():
    """Stop uvicorn server"""
    global uvicorn_process
    if uvicorn_process:
        try:
            logger.info("Stopping uvicorn dashboard server...")
            uvicorn_process.terminate()
            uvicorn_process.wait(timeout=5)
            logger.info("✓ Dashboard server stopped")
        except subprocess.TimeoutExpired:
            logger.warning("Uvicorn didn't stop gracefully, killing process...")
            uvicorn_process.kill()
        except Exception as e:
            logger.error(f"Error stopping uvicorn: {e}")

def stop_ngrok():
    """Stop ngrok tunnel"""
    global ngrok_process
    if ngrok_process:
        try:
            logger.info("Stopping ngrok tunnel...")
            ngrok_process.terminate()
            ngrok_process.wait(timeout=5)
            logger.info("✓ Ngrok tunnel stopped")
        except subprocess.TimeoutExpired:
            logger.warning("Ngrok didn't stop gracefully, killing process...")
            ngrok_process.kill()
        except Exception as e:
            logger.error(f"Error stopping ngrok: {e}")

def signal_handler(sig, frame):
    """Handle shutdown signals"""
    logger.info("\nShutdown signal received")
    stop_uvicorn()
    stop_ngrok()
    sys.exit(0)

if __name__ == "__main__":
    setup_logging()
    logger.info("=" * 80)
    logger.info("Starting Dashboard Server (Standalone)")
    logger.info("=" * 80)
    
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start uvicorn
    if not start_uvicorn():
        logger.error("Failed to start dashboard server. Exiting.")
        sys.exit(1)
    
    # Start ngrok (optional)
    start_ngrok()
    
    logger.info("\n" + "=" * 80)
    logger.info("Dashboard is running!")
    logger.info("Local access: http://localhost:8000")
    logger.info("Remote access: Check http://127.0.0.1:4040 for ngrok URL")
    logger.info("Press Ctrl+C to stop")
    logger.info("=" * 80 + "\n")
    
    # Keep the script running
    try:
        while True:
            # Check if uvicorn is still running
            if uvicorn_process and uvicorn_process.poll() is not None:
                logger.error("Uvicorn process died unexpectedly!")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\nStopping dashboard...")
    finally:
        stop_uvicorn()
        stop_ngrok()
        logger.info("Dashboard stopped")

