# Separate Bot and Dashboard Processes Guide

## Overview

The bot and dashboard have been separated into independent processes. This means:

✅ **Dashboard stays online even if the bot crashes**
✅ **You can restart the bot from the dashboard**
✅ **Better fault isolation and reliability**

## Quick Start

### 1. Start the Dashboard (First)

The dashboard should be started first and left running:

```bash
python run_dashboard.py
```

This will:
- Start the web dashboard on http://0.0.0.0:8000
- Start ngrok tunnel for remote access (optional)
- Keep running until you press Ctrl+C

**Important:** Keep this terminal window open! The dashboard should always be running.

### 2. Start the Bot (Second)

In a **separate terminal window**, start the bot:

```bash
python run_bot.py
```

This will:
- Start the bot process
- Create a PID file at `data/bot.pid` for tracking
- Run the normal bot operations (polling, processing, posting)

## Emergency Bot Restart

If the bot crashes or becomes unresponsive:

1. Open the dashboard in your browser: http://localhost:8000
2. Look for the **"Emergency Bot Controls"** section (red card at the top)
3. Check the **Bot Process Status** to see if it's running
4. Click the **"Restart Bot"** button
5. Confirm the restart in the dialog
6. Wait a few seconds for the bot to restart

The dashboard will show you:
- ✅ Current bot status (Running/Stopped)
- ✅ Process ID (PID)
- ✅ Success/error messages
- ✅ Real-time status updates

## Process Management

### Check Bot Status

The dashboard shows real-time bot status:
- **🟢 Running** - Bot is active and processing
- **🔴 Stopped** - Bot is not running
- **⚠️ Unknown** - Cannot determine status

### Stopping Processes

**To stop the bot:**
- Press `Ctrl+C` in the bot terminal, OR
- Use the restart button in the dashboard (it will stop if it fails to restart)

**To stop the dashboard:**
- Press `Ctrl+C` in the dashboard terminal
- This will also stop the ngrok tunnel

### Restarting After System Reboot

After a system reboot or if you want to start fresh:

1. Start dashboard: `python run_dashboard.py`
2. Wait for it to fully start
3. Start bot: `python run_bot.py`

## Files Created

### New Files
- `run_bot.py` - Bot launcher script
- `run_dashboard.py` - Dashboard launcher script
- `data/bot.pid` - Bot process ID file (auto-created/deleted)

### Modified Files
- `main.py` - Removed dashboard startup code, added PID file management
- `dashboard.py` - Added `/api/bot/status` and `/api/bot/restart` endpoints
- `templates/index.html` - Added emergency bot controls UI

## Technical Details

### PID File Management

The bot writes its process ID to `data/bot.pid` on startup and removes it on clean shutdown. The dashboard uses this file to:
- Check if the bot is running
- Find the bot process to restart it
- Display the current process ID

### Process Restart Flow

1. Dashboard reads PID from `data/bot.pid`
2. Terminates the old bot process
3. Starts a new bot process using `run_bot.py`
4. Waits for the new process to initialize
5. Returns the new PID to the UI

### Platform Compatibility

The restart functionality works on:
- ✅ Windows (using taskkill and CREATE_NEW_PROCESS_GROUP)
- ✅ Linux/Unix (using SIGTERM and start_new_session)
- ✅ macOS (using SIGTERM and start_new_session)

Optional: Install `psutil` for better process management:
```bash
pip install psutil
```

## Troubleshooting

### Bot Won't Restart

**Symptoms:** Restart button fails or bot doesn't start
**Solutions:**
1. Check that `run_bot.py` exists in the working directory
2. Check bot logs for startup errors
3. Manually start the bot: `python run_bot.py`
4. Check if Python is accessible: `python --version`

### Stale PID File

**Symptoms:** Dashboard shows bot as running but it's not
**Solutions:**
1. Delete `data/bot.pid` manually
2. Refresh the dashboard
3. Start the bot: `python run_bot.py`

### Dashboard Can't Find Bot

**Symptoms:** Bot status shows "Unknown" or "Stopped" when bot is running
**Solutions:**
1. Make sure bot was started with `run_bot.py` (not `main.py`)
2. Check that `data/bot.pid` exists
3. Restart both dashboard and bot

### Permission Errors

**Symptoms:** "Permission denied" when trying to restart
**Solutions:**
1. Check file permissions on `data/` directory
2. On Unix/Linux, you may need appropriate permissions to kill processes
3. Try running with appropriate privileges

## Old Method (Deprecated)

The old method of starting both together is no longer recommended:

```bash
# OLD METHOD - DON'T USE
python main.py
```

This started both the bot and dashboard as coupled processes. Use the new separate launchers instead.

## Benefits of Separation

1. **Fault Isolation** - Dashboard crash doesn't affect bot, and vice versa
2. **Remote Management** - Restart bot remotely through dashboard
3. **Debugging** - Easier to debug each component separately
4. **Monitoring** - Dashboard always accessible for monitoring
5. **Flexibility** - Can restart bot without losing dashboard connection

## Support

If you encounter any issues:
1. Check the bot logs: `bot.log`
2. Check the dashboard terminal output
3. Verify both processes are running in separate terminals
4. Try restarting both processes from scratch

