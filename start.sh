#!/bin/bash
# ────────────────────────────────────────────────────────
#  start.sh – הפעלת שרת יתרות ביטוח
#  לחץ פעמיים על הקובץ, או הרץ:  bash start.sh
# ────────────────────────────────────────────────────────

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================"
echo "  מתחיל התקנה והפעלה של שרת היתרות..."
echo "========================================"

# Check Python
if ! command -v python3 &>/dev/null; then
  echo "❌  Python 3 לא מותקן. הורד מ: https://www.python.org/downloads/"
  read -rp "לחץ Enter לסגירה..."
  exit 1
fi

echo "✅  Python $(python3 --version)"

# Create venv if needed
if [ ! -d ".venv" ]; then
  echo "⚙️   יוצר סביבה וירטואלית..."
  python3 -m venv .venv
fi

source .venv/bin/activate

# Install deps
echo "📦  מתקין תלויות Python..."
pip install --quiet --upgrade pip
pip install --quiet flask flask-cors playwright openpyxl

# Install Playwright browsers
echo "🌐  מתקין דפדפן Chromium (חד-פעמי, ~130MB)..."
python3 -m playwright install chromium --with-deps 2>/dev/null || python3 -m playwright install chromium

echo ""
echo "========================================"
echo "  השרת עולה על http://localhost:5050"

# Show local IP for mobile access on home WiFi
LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")
if [ -n "$LOCAL_IP" ]; then
  echo "  📱 רשת ביתית:  http://$LOCAL_IP:5050"
fi
echo "  Ctrl+C לסגירה"
echo "========================================"
echo ""

# Start Cloudflare Tunnel if installed (free, public URL from anywhere)
TUNNEL_PID=""
if command -v cloudflared &>/dev/null; then
  echo "🌐 מפעיל Cloudflare Tunnel..."
  cloudflared tunnel --url http://localhost:5050 --no-autoupdate 2>&1 | \
    grep -E "(trycloudflare|ERR|https)" &
  TUNNEL_PID=$!
  echo "   ⏳ ממתין לכתובת הציבורית (כ-5 שניות)..."
  sleep 5
else
  echo "💡 טיפ: להתקנת גישה מכל מקום (חינמי):"
  echo "   brew install cloudflare/cloudflare/cloudflared"
  echo "   ואז הפעל מחדש את start.sh"
  echo ""
fi

# Trap Ctrl+C to also kill tunnel
cleanup(){ [ -n "$TUNNEL_PID" ] && kill $TUNNEL_PID 2>/dev/null; exit 0; }
trap cleanup INT TERM

python3 backend.py
