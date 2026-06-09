"""
backend.py – שרת Flask + Playwright לשליפת יתרות מחברות ביטוח
גישה: השרת פותח דפדפן לאתר הנכון → המשתמש מתחבר ידנית → השרת שולף יתרה
מקור נתונים: Google Sheets

הרץ: python backend.py
דורש: pip install flask flask-cors playwright && playwright install chromium
"""

from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
import os
import threading
import uuid
import time
import re
import traceback
import urllib.request
import urllib.parse
import csv
import io

app = Flask(__name__)
CORS(app)

# ─────────────────────────────────────────
# Google Sheets configuration
# ─────────────────────────────────────────
GSHEET_ID   = "1l8oFXiP-aRAyUCi6Iz6q24tpxZKykS73xwmYiX6vSYg"
GSHEET_LINK = f"https://docs.google.com/spreadsheets/d/{GSHEET_ID}/edit"
GSHEETS_CACHE: dict = {}
GSHEETS_CACHE_LOCK = threading.Lock()
GSHEETS_CACHE_TTL  = 120   # seconds

# ─────────────────────────────────────────
# Active sessions
# ─────────────────────────────────────────
sessions: dict = {}
sessions_lock = threading.Lock()

# ─────────────────────────────────────────
# ID → Person mapping
# ─────────────────────────────────────────
PERSON_MAP = {
    "036906618": "אמיר",
    "305013526": "ניצן",
}

# ─────────────────────────────────────────
# Company configurations  (updated login URLs)
# ─────────────────────────────────────────
COMPANIES = {
    "clal": {
        "name": "כלל ביטוח",
        "login_url": "https://www.clalbit.co.il/login/",
        "sel_balance": [
            '[class*="total-savings"]', '[class*="totalSavings"]',
            '[class*="portfolio-value"]', '[class*="portfolioValue"]',
            '[data-testid*="total"]', '[data-testid*="balance"]',
            '[class*="balance"]', '[class*="total-amount"]',
            'h1:has-text("₪")', 'h2:has-text("₪")', 'h3:has-text("₪")',
            '[class*="amount"]', '[class*="sum"]',
        ],
    },
    "phoenix": {
        "name": "פניקס",
        "login_url": "https://my.fnx.co.il/mails",
        "sel_balance": [
            '[class*="total"]', '[class*="balance"]', '[class*="savings"]',
            '[data-testid*="total"]', '[class*="portfolio"]',
            'h1:has-text("₪")', 'h2:has-text("₪")',
            '[class*="amount"]',
        ],
    },
    "altshuler": {
        "name": "אלטשולר שחם",
        "login_url": "https://online.as-invest.co.il/login",
        "sel_balance": [
            '[class*="total"]', '[class*="balance"]', '[class*="portfolio"]',
            '[data-testid*="total"]', '[class*="savings"]',
            'td:has-text("סה") + td', '[class*="amount"]',
            'h1:has-text("₪")', 'h2:has-text("₪")',
        ],
    },
    "migdal": {
        "name": "מגדל",
        "login_url": "https://my.migdal.co.il/mymigdal/process/login",
        "sel_balance": [
            '[class*="total"]', '[class*="balance"]', '[class*="portfolio"]',
            '[data-testid*="total"]', '[class*="savings-amount"]',
            'h1:has-text("₪")', 'h2:has-text("₪")',
            '[class*="amount"]',
        ],
    },
    "harel": {
        "name": "הראל",
        "login_url": (
            "https://www.harel-group.co.il/Pages/login-page/Login.aspx"
            "?LoginOriginatingAction=PersonalInfoPage&isshowlogin=true"
            "&Source=%2fpersonal-info&Type=PortalOTP"
        ),
        "sel_balance": [
            '[class*="total"]', '[class*="balance"]', '[class*="portfolio"]',
            '[data-testid*="total"]', '[class*="savings"]',
            'h1:has-text("₪")', 'h2:has-text("₪")',
            '[class*="amount"]',
        ],
    },
}


# ─────────────────────────────────────────
# Scrape balance from current page
# ─────────────────────────────────────────
def scrape_balance(page, sel_list: list) -> str | None:
    # 1. Try CSS selectors
    for sel in sel_list:
        try:
            el = page.locator(sel).first
            el.wait_for(state="visible", timeout=2000)
            text = el.inner_text().strip()
            if text and ("₪" in text or any(c.isdigit() for c in text)):
                return text
        except Exception:
            continue

    # 2. Fallback – regex scan on full page text for ILS amounts
    try:
        body = page.inner_text("body")
        matches = re.findall(r'₪\s*[\d,]+(?:\.\d+)?|[\d,]+(?:\.\d+)?\s*₪', body)
        if matches:
            def parse_amount(s):
                return float(re.sub(r'[^\d.]', '', s) or '0')
            matches.sort(key=parse_amount, reverse=True)
            return matches[0]
    except Exception:
        pass

    return None


# ─────────────────────────────────────────
# Session worker – runs in a background thread
# ─────────────────────────────────────────
def run_session(session_id: str, company_id: str):
    cfg = COMPANIES[company_id]

    def update(status, **kwargs):
        with sessions_lock:
            sessions[session_id].update({"status": status, **kwargs})

    try:
        update("launching")
        pw = sync_playwright().start()

        browser = pw.chromium.launch(
            headless=False,
            slow_mo=0,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            ignore_default_args=["--enable-automation"],
        )
        ctx = browser.new_context(
            viewport=None,
            locale="he-IL",
            timezone_id="Asia/Jerusalem",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()

        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        with sessions_lock:
            sessions[session_id]["page"]       = page
            sessions[session_id]["browser"]    = browser
            sessions[session_id]["playwright"] = pw

        update("navigating", message=f'פותח את אתר {cfg["name"]}...')
        page.goto(cfg["login_url"], wait_until="domcontentloaded", timeout=40000)

        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except PwTimeout:
            pass

        update(
            "waiting_for_login",
            message=f'הדפדפן פתוח באתר {cfg["name"]}. התחבר ידנית ולחץ "התחברתי" למטה.',
            url=page.url,
        )

        # Wait up to 10 min for the user to signal they've logged in
        for _ in range(600):
            time.sleep(1)
            with sessions_lock:
                if sessions[session_id].get("user_logged_in"):
                    break
        else:
            update("timeout", error="פג הזמן לפני שסימנת התחברות")
            return

        update("scraping", message="שולף יתרה מהעמוד...")
        time.sleep(2)

        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PwTimeout:
            pass

        balance = scrape_balance(page, cfg["sel_balance"])

        if balance:
            update("done", balance=balance, url=page.url, gsheet_link=GSHEET_LINK)
        else:
            try:
                page.screenshot(path=f"/tmp/balance_debug_{session_id}.png")
            except Exception:
                pass
            update(
                "done_no_balance",
                balance=None,
                url=page.url,
                page_title=page.title(),
                gsheet_link=GSHEET_LINK,
                message=(
                    "לא מצאתי יתרה אוטומטית. "
                    "ייתכן שיש לנווט לדף הסיכום הפנסיוני. "
                    f"<a href='{page.url}' target='_blank'>פתח דפדפן</a>"
                ),
            )

    except PwTimeout as e:
        update("error", error=f"Timeout: {e}")
    except Exception as e:
        update("error", error=str(e), trace=traceback.format_exc())
    finally:
        time.sleep(600)
        try:
            browser.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


# ─────────────────────────────────────────
# API Routes
# ─────────────────────────────────────────

@app.route("/api/connect", methods=["POST"])
def api_connect():
    """Open browser to company login page."""
    data      = request.json or {}
    company   = data.get("company", "").lower()
    id_number = str(data.get("id_number", "")).strip().lstrip("0")

    if company not in COMPANIES:
        return jsonify({"error": f"חברה לא מוכרת: {company}"}), 400

    person = None
    for raw_id, name in PERSON_MAP.items():
        if id_number and id_number == raw_id.lstrip("0"):
            person = name
            break

    session_id = str(uuid.uuid4())
    with sessions_lock:
        sessions[session_id] = {
            "status": "init", "company": company,
            "person": person,
            "id_number": id_number,
            "page": None, "browser": None, "playwright": None,
            "user_logged_in": False,
            "balance": None, "error": None,
        }

    t = threading.Thread(target=run_session, args=(session_id, company), daemon=True)
    t.start()

    return jsonify({
        "session_id": session_id,
        "company": COMPANIES[company]["name"],
        "person": person,
    })


@app.route("/api/logged_in", methods=["POST"])
def api_logged_in():
    """User signals they've completed manual login – trigger balance scraping."""
    data       = request.json or {}
    session_id = data.get("session_id", "")

    with sessions_lock:
        if session_id not in sessions:
            return jsonify({"error": "session not found"}), 404
        sessions[session_id]["user_logged_in"] = True

    return jsonify({"ok": True, "message": "מתחיל שליפת יתרה..."})


@app.route("/api/status/<session_id>", methods=["GET"])
def api_status(session_id):
    with sessions_lock:
        s = sessions.get(session_id)
    if not s:
        return jsonify({"error": "session not found"}), 404
    return jsonify({k: v for k, v in s.items()
                    if k not in ("page", "browser", "playwright")})


@app.route("/api/close/<session_id>", methods=["DELETE"])
def api_close(session_id):
    with sessions_lock:
        s = sessions.pop(session_id, None)
    if s:
        try:
            if s.get("browser"):    s["browser"].close()
        except Exception: pass
        try:
            if s.get("playwright"): s["playwright"].stop()
        except Exception: pass
    return jsonify({"ok": True})


@app.route("/api/gsheets/<path:sheet_name>", methods=["GET"])
def api_gsheets(sheet_name):
    """
    Fetch a Google Sheet tab as CSV and return JSON rows.
    The sheet must be shared as 'Anyone with the link can view'.
    Responses are cached for GSHEETS_CACHE_TTL seconds.
    """
    now = time.time()
    with GSHEETS_CACHE_LOCK:
        cached = GSHEETS_CACHE.get(sheet_name)
        if cached and now - cached["ts"] < GSHEETS_CACHE_TTL:
            return jsonify(cached["data"])

    encoded = urllib.parse.quote(sheet_name)
    url = (
        f"https://docs.google.com/spreadsheets/d/{GSHEET_ID}"
        f"/gviz/tq?tqx=out:csv&sheet={encoded}"
    )
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; BudgetMonitor/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8-sig")

        # If Google returns an HTML error page, raise
        if raw.strip().startswith("<!DOCTYPE") or raw.strip().startswith("<html"):
            raise ValueError("הגיליון לא נגיש – ודא שהוא שיתוף 'כל מי שיש לו קישור'")

        rows = list(csv.reader(io.StringIO(raw)))
        result = {"rows": rows, "count": len(rows), "sheet": sheet_name}
        with GSHEETS_CACHE_LOCK:
            GSHEETS_CACHE[sheet_name] = {"data": result, "ts": now}
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e), "rows": [], "sheet": sheet_name}), 500


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "sessions": len(sessions)})


# ─────────────────────────────────────────
# Static file serving (PWA)
# ─────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))

@app.route("/")
def serve_index():
    return send_from_directory(_DIR, "budget-dashboard.html")

@app.route("/manifest.json")
def serve_manifest():
    return send_from_directory(_DIR, "manifest.json")

@app.route("/sw.js")
def serve_sw():
    resp = send_from_directory(_DIR, "sw.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp

@app.route("/icon.svg")
def serve_icon_svg():
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect width="100" height="100" rx="18" fill="#0f1117"/>
  <text x="50" y="68" font-size="54" font-family="Arial" font-weight="bold"
        text-anchor="middle" fill="#6c63ff">₪</text>
</svg>'''
    return Response(svg, mimetype="image/svg+xml")

@app.route("/icon-<int:size>.png")
def serve_icon_png(size):
    """Generate a simple PNG icon on-the-fly using only stdlib."""
    import struct, zlib

    def png(w, h, pixels_rgba):
        def chunk(tag, data):
            c = zlib.crc32(tag + data) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", c)
        raw = b"".join(b"\x00" + bytes(pixels_rgba[y * w * 4:(y + 1) * w * 4]) for y in range(h))
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw))
                + chunk(b"IEND", b""))

    # Background #0f1117, accent circle #6c63ff
    bg = (15, 17, 23)
    ac = (108, 99, 255)
    cx, cy, r = size // 2, size // 2, int(size * 0.38)
    px = []
    for y in range(size):
        for x in range(size):
            dx, dy = x - cx, y - cy
            corner_r = size * 0.18
            in_rect = (dx + cx > corner_r and cx - dx > corner_r and
                       dy + cy > corner_r and cy - dy > corner_r)
            in_circ = dx * dx + dy * dy <= r * r
            if in_circ:
                px += [*ac, 255]
            elif in_rect:
                px += [*bg, 255]
            else:
                px += [0, 0, 0, 0]
    return Response(png(size, size, px), mimetype="image/png")


if __name__ == "__main__":
    print("=" * 55)
    print("  שרת יתרות ביטוח – http://localhost:5050")
    print("  פתח את budget-dashboard.html בדפדפן")
    print("=" * 55)
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
