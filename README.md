# EU Institutions Watch

Monitors RSS feeds from **EUR-Lex**, the **Court of Justice of the EU (Curia)**,
the **Council of the EU**, and the **European Commission** press corner, and
sends a **Telegram** alert whenever a new item matches one of your keywords
(default: Morocco / Western Sahara related terms).

## How it works

- `monitor.py` fetches each feed in `config.yaml`, checks new entries against
  your keyword list, and sends a Telegram message for each match.
- `state/seen.json` tracks which items have already been processed, so you
  never get duplicate alerts. GitHub Actions commits this file back to the
  repo after every run.
- Retry/backoff logic handles transient network issues and rate limiting
  without failing the whole run.

## Setup

### 1. Create a Telegram bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`,
   follow the prompts. You'll get a **bot token**.
2. Send any message to your new bot, then visit:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   and find your **chat id** in the JSON response (`message.chat.id`).

### 2. Add GitHub secrets

In your repo: **Settings -> Secrets and variables -> Actions -> New repository secret**

| Name                  | Value                     |
|------------------------|---------------------------|
| `TELEGRAM_BOT_TOKEN`  | token from BotFather       |
| `TELEGRAM_CHAT_ID`    | your chat id               |

### 3. Configure feeds & keywords

Edit `config.yaml`:

- `keywords`: add/remove terms (case-insensitive, matched against title +
  summary).
- `feeds`: CJEU, Council, and Commission feeds are pre-filled and confirmed
  working. For **EUR-Lex**, personalised/keyword RSS alerts require a free
  [EU Login](https://eur-lex.europa.eu) account:
  1. Run an Advanced search for your terms on EUR-Lex.
  2. Click **"Create in my RSS alerts"** on the results page.
  3. Paste the resulting URL into `config.yaml` under the EUR-Lex feed entry.

### 4. Enable and test

- Push this repo to GitHub.
- Go to the **Actions** tab, select "EU Institutions Watch", click
  **Run workflow** to trigger it manually and confirm it works.
- It then runs automatically every 20 minutes (`cron` schedule in
  `.github/workflows/monitor.yml` - adjust as needed).

## Local testing

```bash
pip install -r requirements.txt
DRY_RUN=1 python monitor.py   # logs matches without sending Telegram messages
```

To actually send messages locally:

```bash
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
python monitor.py
```

## Notes

- Council and Commission feeds cover all press releases (not EU-institution-wide
  legislation), so you'll only be alerted on items that also mention your
  keywords - not everything they publish.
- If a feed changes its URL or format upstream, check the Actions run logs
  first: the script logs which feed failed and why.
