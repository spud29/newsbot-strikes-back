# Dashboard Quick Start Guide

## What Was Created

A complete web dashboard for your NewsBot with:

### Core Files
- ✅ `dashboard.py` - FastAPI application with authentication and API endpoints
- ✅ `templates/` - 6 HTML pages (base, index, sources, logs, manual, config, database)
- ✅ `static/` - CSS and JavaScript for styling and interactivity
- ✅ `DASHBOARD.md` - Comprehensive documentation

### Features Implemented
- ✅ Dashboard home with real-time stats
- ✅ Source monitoring (RSS feeds & Telegram channels)
- ✅ Error log viewer with filtering
- ✅ Manual categorization testing
- ✅ Configuration viewer
- ✅ Database tools (search, export, cleanup, reset)
- ✅ HTTP Basic Authentication
- ✅ 12 API endpoints
- ✅ Responsive Bootstrap UI

## Quick Setup (3 Steps)

### 1. Install Dependencies

```bash
pip install fastapi uvicorn jinja2 python-multipart
```

### 2. Add to .env File

Add these two lines to your `.env` file:

```env
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=YourSecurePasswordHere
```

**Important:** Replace `YourSecurePasswordHere` with a strong password!

### 3. Run Dashboard

Open a **second terminal** (keep bot running in first):

```bash
cd "C:\Users\spud9\OneDrive\Documents\newsbot strikes back"
uvicorn dashboard:app --reload --port 8000
```

## Access Dashboard

Open your browser and go to:

```
http://localhost:8000
```

Login with:
- **Username:** admin (or whatever you set in .env)
- **Password:** your password from .env

## File Structure Created

```
newsbot strikes back/
├── dashboard.py                 # ✅ NEW - Main dashboard app
├── templates/                   # ✅ NEW - HTML templates
│   ├── base.html
│   ├── index.html
│   ├── sources.html
│   ├── logs.html
│   ├── manual.html
│   ├── config.html
│   └── database.html
├── static/                      # ✅ NEW - CSS/JS assets
│   ├── style.css
│   └── script.js
├── DASHBOARD.md                 # ✅ NEW - Full documentation
├── DASHBOARD_QUICKSTART.md      # ✅ NEW - This file
└── requirements.txt             # ✅ UPDATED - Added FastAPI deps
```

## Dashboard Pages Overview

### 🏠 Home (/)
- Bot & Ollama status
- 24h processing stats
- Recent activity feed
- Database metrics

### 📡 Sources (/sources)
- View all RSS feeds
- View all Telegram channels
- Source status monitoring

### 📝 Logs (/logs)
- View bot.log entries
- Filter by level (ERROR, WARNING, INFO)
- Search logs
- Last 50-500 lines

### ▶️ Manual (/manual)
- **Test categorization** of any text
- See assigned category
- Check duplicate detection
- View similarity scores

### ⚙️ Config (/config)
- View thresholds
- View intervals
- View category mappings
- View system prompt

### 💾 Database (/database)
- View database stats
- Search entries by ID
- Cleanup old entries
- Reset specific entries for reprocessing
- Export database as JSON

## Typical Workflow

### Daily Monitoring
1. Open dashboard at http://localhost:8000
2. Check home page - is bot active? Ollama online?
3. Review recent activity - entries being processed?
4. Check logs if errors are showing

### When Something Goes Wrong
1. Go to Logs page
2. Filter by ERROR level
3. Search for specific error messages
4. Review stack traces
5. Check bot.log file for full context

### Testing New Content
1. Go to Manual page
2. Paste text content
3. Click "Test Category"
4. Review assigned category
5. Check if content is duplicate/similar
6. Use results to tune system prompt if needed

### Database Management
1. Go to Database page
2. View current stats
3. Search for specific entries
4. Export database for backup
5. Reset entries if needed for reprocessing
6. Run cleanup to remove old entries

## Running Both Bot and Dashboard

### Recommended: Two Terminals

**Terminal 1 (Bot):**
```powershell
cd "C:\Users\spud9\OneDrive\Documents\newsbot strikes back"
python main.py
```

**Terminal 2 (Dashboard):**
```powershell
cd "C:\Users\spud9\OneDrive\Documents\newsbot strikes back"
uvicorn dashboard:app --reload --port 8000
```

### Alternative: VS Code or Cursor Split Terminal
- Use split terminal feature
- Run bot in one pane
- Run dashboard in other pane

## Troubleshooting

### "ModuleNotFoundError: No module named 'fastapi'"
```bash
pip install fastapi uvicorn jinja2 python-multipart
```

### "DASHBOARD_PASSWORD not set"
Add to `.env` file:
```env
DASHBOARD_PASSWORD=your_password_here
```

### Dashboard shows "Bot: Inactive"
- Make sure `python main.py` is running
- Bot is considered inactive if bot.log hasn't been modified in 10 minutes

### Dashboard shows "Ollama: Down"
- Start Ollama service
- Verify: `curl http://localhost:11434/api/tags`

### Can't login to dashboard
- Check username/password in `.env` file
- Clear browser cache
- Try incognito/private window

## Next Steps

1. ✅ **Setup Complete** - Follow the 3 steps above
2. 📖 **Read Full Docs** - Check `DASHBOARD.md` for detailed info
3. 🚀 **Start Using** - Open http://localhost:8000 and explore
4. 📊 **Monitor** - Keep dashboard open while bot runs
5. 🔧 **Customize** - Edit templates/static files if needed

## API Integration

If you want to build custom integrations, all endpoints are documented in `DASHBOARD.md`.

Example API usage:
```bash
# Get stats (with authentication)
curl -u admin:yourpassword http://localhost:8000/api/stats

# Health check
curl -u admin:yourpassword http://localhost:8000/api/health

# Export database
curl -u admin:yourpassword http://localhost:8000/api/database/export > backup.json
```

## Future Enhancements

You can extend the dashboard by:
- Adding real-time WebSocket updates
- Implementing full URL processing
- Adding charts and graphs
- Creating email/webhook alerts
- Building a mobile-responsive UI
- Adding more database filters

## Questions?

Refer to `DASHBOARD.md` for comprehensive documentation including:
- Detailed feature descriptions
- All API endpoints
- Security considerations
- Production deployment guide
- Tips and best practices

---

**You're all set!** 🎉

Run the 3 setup steps and start monitoring your bot through the web dashboard.

