# NewsBot Dashboard Documentation

## Overview

The NewsBot Dashboard is a web-based interface for monitoring and managing your Discord News Aggregator Bot. It provides real-time statistics, error logs, manual processing capabilities, and administrative tools.

## Features

- **Real-time Monitoring**: View bot status, Ollama health, and processing statistics
- **Source Management**: Monitor RSS feeds and Telegram channels
- **Error Logs**: View and filter bot logs for debugging
- **Manual Processing**: Test categorization and process content manually
- **Configuration Viewer**: Review current bot settings
- **Database Tools**: Search, export, and manage database entries

## Installation

### Prerequisites

- Python 3.9 or higher
- All bot dependencies already installed
- Bot running and operational

### Install Dashboard Dependencies

```bash
pip install fastapi uvicorn jinja2 python-multipart
```

Or update all dependencies:

```bash
pip install -r requirements.txt
```

### Configure Authentication

Add dashboard credentials to your `.env` file:

```env
# Existing credentials
DISCORD_TOKEN=your_discord_bot_token
TELEGRAM_API_ID=your_telegram_api_id
TELEGRAM_API_HASH=your_telegram_api_hash

# Dashboard credentials (add these)
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=your_secure_password_here
```

**Important**: Choose a strong password for `DASHBOARD_PASSWORD`. The dashboard will not start without this.

## Running the Dashboard

### Option 1: Separate Terminal (Recommended)

Run the bot and dashboard in separate terminals:

```bash
# Terminal 1 - Run the bot
python main.py

# Terminal 2 - Run the dashboard
uvicorn dashboard:app --reload --port 8000
```

### Option 2: Using PowerShell Jobs (Windows)

```powershell
# Start bot in background
Start-Job -ScriptBlock { python main.py }

# Start dashboard in foreground
uvicorn dashboard:app --reload --port 8000
```

### Option 3: Production Mode

For production, use uvicorn without reload:

```bash
uvicorn dashboard:app --host 0.0.0.0 --port 8000
```

## Accessing the Dashboard

Once running, open your browser and navigate to:

```
http://localhost:8000
```

You'll be prompted for authentication. Use the credentials from your `.env` file:
- **Username**: Value of `DASHBOARD_USERNAME` (default: "admin")
- **Password**: Value of `DASHBOARD_PASSWORD`

## Dashboard Pages

### 1. Dashboard Home (`/`)

**Overview Statistics:**
- Ollama service status (online/offline)
- Bot activity status (active/inactive)
- Processed entries in last 24 hours
- Database size (IDs and embeddings)
- Active RSS feeds count
- Active Telegram channels count

**Recent Activity:**
- Last 20 processed entries
- Entry IDs, sources, and timestamps
- Real-time updates on refresh

**How to Use:**
- Click "Refresh" to update statistics
- Monitor bot health at a glance
- Track processing activity

### 2. Sources (`/sources`)

**RSS Feeds:**
- List of all configured RSS feeds
- Feed names and URLs
- Current status

**Telegram Channels:**
- List of monitored channels
- Channel names and status

**How to Use:**
- View all active sources
- Verify feeds are configured correctly
- Click "Refresh" to update

**Note**: Adding/removing sources requires editing `config.py` and restarting the bot.

### 3. Error Logs (`/logs`)

**Log Viewer:**
- Recent log entries from `bot.log`
- Color-coded by severity (ERROR, WARNING, INFO)
- Searchable and filterable

**Filters:**
- **Level**: Filter by ERROR, WARNING, INFO, or show all
- **Lines**: Display last 50, 100, 200, or 500 lines
- **Search**: Find specific text in logs

**How to Use:**
1. Select desired filters
2. Click "Filter" to apply
3. Scroll through logs
4. Use search to find specific errors

### 4. Manual Processing (`/manual`)

**Test Categorization:**
- Paste any text content
- See which category would be assigned
- Check for duplicate/similar content
- View similarity scores

**Results Shown:**
- Assigned category
- Duplicate status (DUPLICATE/SIMILAR/UNIQUE)
- Similarity percentage
- Matching content preview (if duplicate)

**How to Use:**
1. Paste text into the text area
2. Click "Test Category"
3. Review results
4. Use this to tune your system prompt if needed

**Note**: Full URL processing (downloading media, posting to Discord) is not yet implemented. Currently, only text categorization testing is available.

### 5. Configuration (`/config`)

**View Settings:**
- **Thresholds**: Duplicate and similarity detection thresholds
- **Intervals**: Poll interval and database retention period
- **Categories**: All category to Discord channel mappings
- **System Prompt**: The AI categorization prompt

**How to Use:**
- Review current configuration
- Click "Refresh" to reload settings
- To modify settings, edit `config.py` and restart the bot

**Note**: Direct configuration editing through the dashboard is planned for a future update.

### 6. Database Tools (`/database`)

**Statistics:**
- Total processed IDs
- Total embeddings stored
- Total message mappings (Telegram ↔ Discord)

**Search:**
- Find entries by ID
- View when they were processed
- Verify if an entry exists in the database

**Actions:**
- **Cleanup Old Entries**: Manually trigger database cleanup (removes entries older than retention period)
- **Reset Entry**: Remove a specific entry to allow reprocessing
- **Export**: Download entire database as JSON file

**How to Use:**

**To search for an entry:**
1. Enter entry ID in search box
2. Click "Search"
3. View results (found/not found with timestamp)

**To cleanup database:**
1. Click "Run Cleanup" button
2. Confirm action
3. Old entries (beyond retention period) will be removed

**To reset an entry:**
1. Enter the full entry ID (e.g., `twitter_1234567890`)
2. Click "Reset"
3. Confirm action
4. Entry will be removed from processed IDs and can be reprocessed

**To export database:**
1. Click "Export" button
2. JSON file will be downloaded with timestamp
3. Contains all processed IDs, mappings, and metadata

## API Endpoints

The dashboard provides these REST API endpoints:

### Health & Stats
- `GET /api/health` - Bot and Ollama health status
- `GET /api/stats` - Current statistics and recent entries

### Sources
- `GET /api/sources` - List all RSS feeds and Telegram channels

### Logs
- `GET /api/logs?lines=100&level=ERROR&search=text` - Get filtered logs

### Manual Processing
- `POST /api/test-category` (form data: `text`) - Test categorization

### Configuration
- `GET /api/config` - Get current configuration

### Database
- `GET /api/database/search?q=entry_id` - Search for entry
- `POST /api/database/clear` - Cleanup old entries
- `DELETE /api/database/reset/{entry_id}` - Reset specific entry
- `GET /api/database/export` - Export database as JSON

All endpoints require HTTP Basic Authentication.

## Troubleshooting

### Dashboard Won't Start

**Error: "DASHBOARD_PASSWORD not set"**
- Add `DASHBOARD_PASSWORD=your_password` to `.env` file
- Make sure `.env` is in the same directory as `dashboard.py`

**Error: "No module named 'fastapi'"**
- Run: `pip install fastapi uvicorn jinja2 python-multipart`

**Error: "Address already in use"**
- Another service is using port 8000
- Use a different port: `uvicorn dashboard:app --port 8001`

### Can't Access Dashboard

**Browser shows "Unable to connect"**
- Verify dashboard is running: check terminal for "Uvicorn running on..."
- Try: `http://127.0.0.1:8000` instead of `http://localhost:8000`
- Check firewall settings

**Authentication keeps failing**
- Verify credentials in `.env` file
- Check for spaces or extra characters
- Try closing browser and reopening (clears cached credentials)

### Dashboard Shows No Data

**"Log file not found"**
- Bot hasn't started yet or `bot.log` doesn't exist
- Start the bot first, then check dashboard

**"Bot: Inactive"**
- Bot might not be running
- Check if `main.py` is running in another terminal
- Bot is considered inactive if log file hasn't been modified in 10 minutes

**"Ollama: Down"**
- Ollama service is not running
- Start Ollama: Check if it's running on http://localhost:11434
- Verify with: `curl http://localhost:11434/api/tags`

### Recent Activity is Empty

- Bot hasn't processed any entries yet
- Wait for next polling cycle (default: 5 minutes)
- Or there might be no new content from sources

## Security Considerations

### Authentication
- Uses HTTP Basic Authentication
- Username and password from `.env` file
- Browser will remember credentials during session

### Access Control
- **Local Only**: By default, dashboard listens on `0.0.0.0` (all interfaces)
- **Recommendation**: For local use only, use `--host 127.0.0.1`
- **Remote Access**: If exposing remotely, use HTTPS and strong passwords

### Production Deployment

If deploying to a server:

```bash
# Install with production dependencies
pip install uvicorn[standard]

# Run with specific host
uvicorn dashboard:app --host 127.0.0.1 --port 8000

# Or use a process manager like systemd or supervisor
```

For production with HTTPS, use a reverse proxy (nginx, Apache) or:

```bash
# With SSL certificates
uvicorn dashboard:app --host 0.0.0.0 --port 443 --ssl-keyfile=/path/to/key.pem --ssl-certfile=/path/to/cert.pem
```

## Tips & Best Practices

1. **Keep Dashboard Running**: The dashboard doesn't affect bot performance, so you can keep it running while working on other tasks.

2. **Regular Monitoring**: Check the dashboard periodically to ensure bot is processing entries correctly.

3. **Log Review**: Review error logs regularly to catch issues early.

4. **Test Before Editing Config**: Use manual processing to test categorization before modifying the system prompt.

5. **Export Regularly**: Export database periodically as backup.

6. **Reset Carefully**: Only reset entries if you're sure you want to reprocess them. This can lead to duplicate posts if not careful.

## Keyboard Shortcuts

- **Ctrl+R / Cmd+R**: Refresh page (standard browser refresh)
- **Escape**: Close any open modals/dialogs

## Browser Compatibility

The dashboard is tested and works with:
- Chrome/Edge (Chromium) - Recommended
- Firefox
- Safari
- Any modern browser with JavaScript enabled

## Future Enhancements

Planned features for future updates:
- Real-time updates (WebSocket support)
- Configuration editing through UI
- Full manual URL processing (download media, post to Discord)
- Processing queue management
- Category statistics and charts
- Email/webhook alerts
- Source enable/disable toggles
- Bulk database operations

## Support

If you encounter issues:

1. Check this documentation
2. Review `bot.log` for errors
3. Verify all dependencies are installed
4. Ensure bot is running properly
5. Check dashboard terminal for error messages

## File Structure

```
newsbot strikes back/
├── dashboard.py              # Main dashboard application
├── templates/                # HTML templates
│   ├── base.html            # Base template with navigation
│   ├── index.html           # Dashboard home
│   ├── sources.html         # Source monitor
│   ├── logs.html            # Error logs viewer
│   ├── manual.html          # Manual processing
│   ├── config.html          # Configuration viewer
│   └── database.html        # Database tools
├── static/                   # Static assets
│   ├── style.css            # Custom styles
│   └── script.js            # JavaScript utilities
└── .env                     # Credentials (DASHBOARD_PASSWORD)
```

## License

This dashboard is part of the Discord News Aggregator Bot project and is provided as-is for personal use.

---

**Version**: 1.0.0  
**Last Updated**: November 2025

