# Remote Access Setup Guide

## Overview

This guide helps you access your NewsBot Dashboard from outside your home network (work, mobile, etc.).

## Recommended Solution: Tailscale

### Why Tailscale?
- ✅ Most secure (encrypted private network)
- ✅ Easiest setup (10 minutes)
- ✅ Free for personal use
- ✅ No port forwarding needed
- ✅ Works from anywhere (work, mobile, etc.)
- ✅ No router configuration required

### Tailscale Setup (Step-by-Step)

#### 1. Install on Home Computer (Where Bot Runs)

**Windows:**
1. Download: https://tailscale.com/download/windows
2. Run installer
3. Click "Sign in with..."
4. Choose Google, Microsoft, or GitHub
5. Approve the login

**After login:**
- Look for Tailscale icon in system tray
- Right-click → "Copy IPv4 address"
- Write it down (looks like `100.x.x.x`)

#### 2. Install on Work Computer/Phone

**Same process:**
1. Install Tailscale
2. Sign in with same account
3. Both devices now on private network!

#### 3. Access Dashboard

**From work computer:**
```
http://100.x.x.x:8000
```
(Replace `100.x.x.x` with your home PC's Tailscale IP)

**From phone:**
- Install Tailscale app
- Sign in
- Open browser to `http://100.x.x.x:8000`

### Tailscale Tips

**Make IP Static:**
1. Go to https://login.tailscale.com/admin/machines
2. Click on your home computer
3. Enable "Disable key expiry"
4. Note the IP - it won't change!

**Access from Phone:**
- iOS: https://apps.apple.com/app/tailscale/id1470499037
- Android: https://play.google.com/store/apps/details?id=com.tailscale.ipn

---

## Alternative: Cloudflare Tunnel

### Why Cloudflare Tunnel?
- ✅ Free forever
- ✅ HTTPS included
- ✅ No port forwarding
- ✅ Custom domain support
- ⚠️ Requires domain name

### Setup

#### 1. Prerequisites
- Cloudflare account (free)
- Domain name (can get free from Freenom or use existing)

#### 2. Install cloudflared

**Windows:**
1. Download: https://github.com/cloudflare/cloudflared/releases
2. Extract to `C:\cloudflared\`
3. Add to PATH

#### 3. Authenticate

```powershell
cd C:\cloudflared
.\cloudflared tunnel login
```

Browser will open - login to Cloudflare and select domain.

#### 4. Create Tunnel

```powershell
# Create tunnel
.\cloudflared tunnel create newsbot

# Create config file
notepad config.yml
```

**config.yml:**
```yaml
tunnel: newsbot
credentials-file: C:\cloudflared\.cloudflared\[tunnel-id].json

ingress:
  - hostname: dashboard.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404
```

#### 5. Setup DNS

```powershell
.\cloudflared tunnel route dns newsbot dashboard.yourdomain.com
```

#### 6. Run Tunnel

```powershell
.\cloudflared tunnel run newsbot
```

#### 7. Access

Open: `https://dashboard.yourdomain.com`

**Run Automatically on Startup:**
```powershell
.\cloudflared service install
```

---

## Alternative: ngrok (Quick & Easy)

### Why ngrok?
- ✅ Instant setup (1 minute)
- ✅ Great for testing
- ⚠️ Free tier: URL changes on restart
- ⚠️ Limited to 40 connections/minute (free)

### Setup

#### 1. Install

**Windows:**
1. Download: https://ngrok.com/download
2. Extract to folder
3. Sign up for free account
4. Get authtoken from dashboard

#### 2. Authenticate

```bash
ngrok config add-authtoken YOUR_TOKEN_HERE
```

#### 3. Start Tunnel

```bash
ngrok http 8000
```

#### 4. Access

ngrok displays URLs like:
```
Forwarding: https://abc123.ngrok-free.app -> http://localhost:8000
```

Use that HTTPS URL from anywhere!

**Keep Running:**
- Leave ngrok window open
- Or use paid plan for persistent URLs

---

## Alternative: Port Forwarding (Advanced)

### Why Port Forwarding?
- ✅ Complete control
- ✅ No third-party services
- ⚠️ Requires router access
- ⚠️ Need to setup HTTPS
- ⚠️ Security considerations

### Setup Overview

#### 1. Enable HTTPS First

You'll need SSL certificates. Install with:

```bash
pip install uvicorn[standard]
```

**Get free SSL certificates:**
- Use Let's Encrypt (requires domain)
- Or create self-signed (browser warnings)

#### 2. Run Dashboard with HTTPS

```bash
uvicorn dashboard:app --host 0.0.0.0 --port 8000 --ssl-keyfile=key.pem --ssl-certfile=cert.pem
```

#### 3. Configure Router

1. Log into router (usually `192.168.1.1`)
2. Find "Port Forwarding" or "Virtual Server"
3. Forward external port 443 → internal `your-pc-ip:8000`

#### 4. Get Your Public IP

- Visit: https://whatismyipaddress.com/
- Or use Dynamic DNS service (recommended)

#### 5. Access

From anywhere:
```
https://your-public-ip
```

### Dynamic DNS (Recommended for Port Forwarding)

Free services:
- No-IP: https://www.noip.com/
- DuckDNS: https://www.duckdns.org/
- Dynu: https://www.dynu.com/

Gives you a domain like: `newsbot.ddns.net`

---

## Security Enhancements for Remote Access

### 1. Strong Password

Update `.env`:
```env
DASHBOARD_PASSWORD=VeryLongAndComplexPassword123!@#
```

### 2. Consider Token Authentication

For enhanced security, I can help you add:
- JWT tokens instead of Basic Auth
- Session management
- IP whitelisting
- Rate limiting

### 3. HTTPS Only

For port forwarding method, always use HTTPS in production.

### 4. Firewall Rules

If using port forwarding, consider:
- IP whitelisting (only allow your work IP)
- Fail2ban for brute force protection
- Rate limiting

---

## Comparison Table

| Method | Setup Time | Security | Cost | Persistence | Complexity |
|--------|-----------|----------|------|-------------|------------|
| **Tailscale** | 10 min | ⭐⭐⭐⭐⭐ | Free | ✅ Permanent | Easy |
| **Cloudflare** | 30 min | ⭐⭐⭐⭐⭐ | Free | ✅ Permanent | Medium |
| **ngrok** | 2 min | ⭐⭐⭐⭐ | Free/Paid | ⚠️ Changes URL | Very Easy |
| **Port Forward** | 1-2 hours | ⭐⭐⭐ | Free | ✅ Permanent | Hard |

## My Recommendation

**For you:**

1. **Start with Tailscale** (10 minutes)
   - Install on both computers
   - Access via Tailscale IP
   - Most secure and easiest

2. **Or try ngrok first** (2 minutes)
   - Test if remote access works for you
   - Upgrade to Tailscale for permanent solution

3. **Avoid port forwarding** unless you:
   - Have technical experience
   - Understand security implications
   - Need to share with others outside your account

---

## Quick Start: Tailscale (Recommended)

```bash
# 1. On home PC: Install Tailscale
# Download: https://tailscale.com/download

# 2. Sign in with Google/GitHub/Microsoft

# 3. Note your Tailscale IP (100.x.x.x)

# 4. On work PC: Install Tailscale, sign in with same account

# 5. Access from work:
# http://100.x.x.x:8000

# Done! ✅
```

---

## Troubleshooting

### Tailscale Not Working
- Check both devices are signed into same account
- Try pinging: `ping 100.x.x.x`
- Restart Tailscale on both devices

### ngrok Connection Issues
- Verify ngrok is running
- Check authtoken is configured
- Free tier has connection limits

### Can't Access from Work
- Some corporate networks block VPNs/tunnels
- Try from phone with cellular data
- Ask IT if Tailscale is allowed

---

## Need Help?

Let me know which method you choose and I can provide:
- Detailed step-by-step instructions
- Security hardening
- Custom configurations
- Troubleshooting help

## What's Next?

Once you choose a method:
1. I can help set it up
2. Add security enhancements if needed
3. Configure auto-start on boot
4. Setup monitoring/alerts

Choose your preferred method and I'll walk you through it! 🚀

