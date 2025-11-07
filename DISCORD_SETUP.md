# Discord Bot Setup Instructions

## The Issue You're Seeing

When you run the bot, you see:
```
Inaccessible channels (11/11):
  ✗ crypto: ID 1317592423962251275 (NOT FOUND)
  ✗ news/politics: ID 1317592486927007784 (NOT FOUND)
  ... etc
```

This is **NOT an error** - it means the bot isn't in your Discord server yet, or the channel IDs are incorrect.

---

## Solution: Invite Your Bot to Discord

### Step 1: Generate OAuth2 Invite URL

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Select your application: **"News Reporter"** (ID: 1317153117518696588)
3. Click **"OAuth2"** in the left sidebar
4. Click **"URL Generator"**

### Step 2: Select Scopes and Permissions

**Scopes** (check these boxes):
- ✅ `bot`
- ✅ `applications.commands` (optional, for slash commands later)

**Bot Permissions** (check these boxes):
- ✅ View Channels
- ✅ Send Messages
- ✅ Attach Files
- ✅ Embed Links
- ✅ Read Message History (optional but recommended)

This will generate a permissions integer. You need at least: **51200** (basic permissions)

### Step 3: Copy and Use the Generated URL

At the bottom of the page, you'll see a **"Generated URL"**. It will look like:
```
https://discord.com/oauth2/authorize?client_id=1317153117518696588&permissions=51200&scope=bot
```

1. **Copy this URL**
2. **Paste it into your web browser**
3. **Select the server** you want to add the bot to
4. **Click "Authorize"**
5. **Complete the CAPTCHA**

---

## Step 4: Get Your Channel IDs

After inviting the bot, you need to update `config.py` with your actual channel IDs.

### How to Get Channel IDs:

1. **Enable Developer Mode in Discord:**
   - Open Discord → User Settings (gear icon) → Advanced → Enable "Developer Mode"

2. **Right-click on each channel** in your server and select **"Copy Channel ID"**

3. **Update `config.py`:**
   ```python
   DISCORD_CHANNELS = {
       "crypto": YOUR_CRYPTO_CHANNEL_ID,
       "news/politics": YOUR_NEWS_CHANNEL_ID,
       "stocks": YOUR_STOCKS_CHANNEL_ID,
       "artificial intelligence": YOUR_AI_CHANNEL_ID,
       "video games": YOUR_GAMES_CHANNEL_ID,
       "sports": YOUR_SPORTS_CHANNEL_ID,
       "food": YOUR_FOOD_CHANNEL_ID,
       "technology": YOUR_TECH_CHANNEL_ID,
       "music": YOUR_MUSIC_CHANNEL_ID,
       "fashion": YOUR_FASHION_CHANNEL_ID,
       "ignore": YOUR_IGNORE_CHANNEL_ID
   }
   ```

---

## Step 5: Verify Setup

After inviting the bot and updating channel IDs, restart the bot. You should now see:

```
Discord client connected and ready!
Verifying Discord channel access...
Accessible channels (11/11):
  ✓ crypto: #crypto-news (1234567890)
  ✓ news/politics: #politics (1234567891)
  ... etc
```

The bot will also tell you:
- Which server(s) it's in
- All channels it can see
- Whether it can post to each configured channel

---

## Troubleshooting

### "All channels still show as NOT FOUND"

**Possible causes:**
1. **Bot not invited yet** → Follow Step 3 above
2. **Wrong server** → Make sure you invited the bot to the correct Discord server
3. **Channel IDs are wrong** → Double-check you copied the right IDs from the right server
4. **Bot lacks permissions** → Make sure it has "View Channels" permission

### "Bot is in server but can't see some channels"

**Solution:** Check channel permissions:
1. Right-click the channel → Edit Channel → Permissions
2. Make sure the bot's role has:
   - ✅ View Channel
   - ✅ Send Messages
   - ✅ Attach Files

### "Bot can see channels but can't post"

**Solution:** 
1. Check the bot has "Send Messages" permission
2. Check the bot has "Attach Files" permission (for images/videos)
3. Check the channel isn't locked to specific roles

---

## Quick Invite Link

If you want to skip the manual setup, use this direct link (replace with your actual bot ID):

```
https://discord.com/oauth2/authorize?client_id=1317153117518696588&permissions=51200&scope=bot
```

Just paste it in your browser and select your server!

---

## What Happens After Setup

Once the bot is properly configured:
1. It will poll RSS feeds and Telegram channels every 5 minutes
2. It will categorize each news item using AI
3. It will post to the appropriate Discord channel
4. It will avoid posting duplicates
5. It will include images/videos when available

Your Discord server will become an automated news aggregation hub! 🎉

