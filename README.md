# Budget Monitor 💰

A personal financial portfolio dashboard for tracking Israeli insurance and pension fund balances (כלל, פניקס, אלטשולר שחם, מגדל, הראל).

## How it works

The app has two parts:

- **Frontend** (`budget-dashboard.html`) – a dark-themed PWA dashboard built with Chart.js. It reads budget and transaction data from a Google Sheet and displays portfolio summaries, charts, and history.
- **Backend** (`backend.py`) – a Flask server that uses Playwright to open a real browser to each insurance company's login page. You log in manually, then the server scrapes your balance from the page and can write it back to the Google Sheet.

The manual login approach avoids dealing with 2FA and anti-bot measures on each company's site.

## Setup

**Requirements:** Python 3.11+, Node (optional)

```bash
# Install Python dependencies
pip install flask flask-cors playwright

# Install Playwright browser
playwright install chromium

# Run the server
python backend.py
```

The server starts at `http://localhost:5050`. Open that URL in your browser to see the dashboard.

## Google Sheets

The app reads data from a Google Sheet. To use your own sheet:

1. Create a Google Sheet and share it as **"Anyone with the link can view"**
2. Update `GSHEET_ID` in `backend.py` with your sheet's ID
3. The sheet tabs should match the names expected by the dashboard

## Usage

1. Start the backend: `python backend.py`
2. Open `http://localhost:5050`
3. Select a company and enter your ID number
4. The app opens a browser window to the company's login page
5. Log in manually (OTP, password, etc.)
6. Click "התחברתי" — the server scrapes your balance and saves it to Google Sheets

## Supported Companies

| Company | Hebrew |
|---------|--------|
| Clal Bituch | כלל ביטוח |
| Phoenix | פניקס |
| Altshuler Shaham | אלטשולר שחם |
| Migdal | מגדל |
| Harel | הראל |

## Deployment (Vercel)

The frontend can be deployed as a static site on [Vercel](https://vercel.com):

1. Connect this repository to a Vercel project
2. No build command is needed — Vercel serves the static files directly
3. The `vercel.json` config rewrites `/` to `budget-dashboard.html`

The backend must be run separately (locally or on a server that supports Python/Playwright).

## Tech Stack

- **Backend:** Python, Flask, Playwright
- **Frontend:** Vanilla HTML/CSS/JS, Chart.js
- **Data:** Google Sheets (CSV export API)
- **Hosting:** Vercel (static frontend)
- **PWA:** installable on mobile via manifest + service worker
