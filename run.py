"""
run.py — ProTrader Terminal v7
Vercel / production entrypoint.

Streamlit is not natively WSGI, so this shim starts it as a subprocess and
proxies all HTTP traffic through a minimal WSGI wrapper.

For local dev:  streamlit run app.py
For Vercel:     this file is the lambda handler.

NOTE: For best Vercel results use the community buildpack approach below.
      If you need a simpler deploy, push to Streamlit Community Cloud instead.
"""

import os
import sys
import subprocess
import threading

# ── Streamlit Community Cloud / Railway / Render deploy ──────────────────────
# These platforms run `streamlit run app.py` directly.
# This file is only needed for custom WSGI hosts (Vercel / AWS Lambda).

def _start_streamlit():
    """Start Streamlit server in background thread."""
    port = int(os.environ.get("STREAMLIT_SERVER_PORT", 8501))
    subprocess.Popen([
        sys.executable, "-m", "streamlit", "run", "app.py",
        "--server.port", str(port),
        "--server.headless", "true",
        "--server.enableCORS", "false",
        "--server.enableXsrfProtection", "false",
        "--browser.gatherUsageStats", "false",
    ])


_started = False


def handler(request, context=None):
    """
    Vercel serverless handler — proxies to local Streamlit.
    For a production-grade deploy, use:
      - Streamlit Community Cloud (free, best for this app)
      - Railway.app (1-click Dockerfile deploy)
      - Render.com (free tier with Dockerfile)
    """
    global _started
    if not _started:
        t = threading.Thread(target=_start_streamlit, daemon=True)
        t.start()
        import time; time.sleep(3)
        _started = True

    import urllib.request
    port = int(os.environ.get("STREAMLIT_SERVER_PORT", 8501))
    url  = f"http://localhost:{port}{request.get('path','/')}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read()
            return {
                "statusCode": resp.status,
                "headers": {"Content-Type": resp.headers.get("Content-Type","text/html")},
                "body": body.decode("utf-8", errors="replace"),
            }
    except Exception as exc:
        return {
            "statusCode": 502,
            "body": f"ProTrader starting... please refresh in 5 seconds. ({exc})",
        }


# ─── Recommended Deployment Instructions ─────────────────────────────────────
"""
DEPLOYMENT GUIDE — ProTrader Terminal v7
=========================================

OPTION 1 — Streamlit Community Cloud (RECOMMENDED, FREE)
  1. Push all files to a public/private GitHub repo
  2. Go to https://share.streamlit.io → "New app"
  3. Select repo, branch, main file: app.py
  4. Add secrets in the Streamlit secrets panel:
       DATABASE_URL = "postgresql://..."
       ANGEL_API_KEY = "..."
       ANGEL_CLIENT_CODE = "..."
       ANGEL_PASSWORD = "..."
       ANGEL_TOTP_SECRET = "..."
  5. Click Deploy → done

OPTION 2 — Railway.app (FREE TIER)
  1. Create Dockerfile (below) or use Nixpacks auto-detection
  2. railway new → connect GitHub repo
  3. Add environment variables (same as above)
  4. Deploy

OPTION 3 — Render.com
  1. New Web Service → connect GitHub
  2. Runtime: Python 3.11
  3. Build command: pip install -r requirements.txt
  4. Start command: streamlit run app.py --server.port $PORT --server.headless true
  5. Add environment variables

OPTION 4 — Docker (any VPS/cloud)
  Use the Dockerfile below.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Dockerfile:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

CMD ["streamlit", "run", "app.py",
     "--server.port=8501",
     "--server.headless=true",
     "--server.enableCORS=false",
     "--server.enableXsrfProtection=false"]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SECRETS REQUIRED:
  DATABASE_URL          — Neon/Supabase/Railway PostgreSQL connection string
  ANGEL_API_KEY         — Angel One API key
  ANGEL_CLIENT_CODE     — Angel One client code
  ANGEL_PASSWORD        — Angel One login password
  ANGEL_TOTP_SECRET     — TOTP secret (or set ANGEL_TOTP for fixed OTP)
"""

if __name__ == "__main__":
    # Local development shortcut
    os.execv(sys.executable, [sys.executable, "-m", "streamlit", "run", "app.py"] + sys.argv[1:])
