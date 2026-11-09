# S&P 500 trading bot (paper trading)

Trades SPY and SSO on Alpaca's paper (fake-money) account and texts you twice a day:

- **~8:40am ET, premarket:** what the bot sees and which orders it queued for the open.
- **~4:25pm ET, after the close:** today's fills, account P&L, and what tomorrow looks like.

## The strategy

| Half | Rule | How often it trades |
| --- | --- | --- |
| Trend (50%) | At each month-end, hold SPY if it closed above its 200-day average; otherwise hold T-bills (BIL). | ~1–2 times a year |
| IBS (50%) | IBS = where SPY closed within the day's range (0 = at the low, 1 = at the high). After a SPY close with IBS below 0.2, buy **SSO** (a fund that moves 2x the S&P 500 each day); sell after a close with IBS above 0.8. Idle money sits in BIL. | ~28 round trips a year |

All orders are **market-on-open** orders placed before 9:28am, acting on the previous day's close. Backtested on real SSO prices from mid-2006 to Oct 2026, that earned about **13.7% a year with a worst drop of 27.5%**, versus 10.2% and 56% for buying and holding SPY. Past results don't guarantee future ones.

The leverage cuts both ways: the IBS half by itself once lost 47% in three weeks (Sep–Oct 2008). The trend half is what kept the whole account's worst drop near 27%. To run the IBS half without leverage (about 9.8% a year, worst drop 17%), add a repository variable `IBS_SYMBOL` set to `SPY`.

## One-time setup (about 30 minutes)

### 1. Alpaca paper account
1. Sign up at alpaca.markets. A paper-only account is free and needs no money.
2. In the dashboard, make sure you're on the **Paper** account, then generate API keys.
3. Copy the **API Key ID** and **Secret Key**. You'll paste them into GitHub in step 3, never into a chat or a file.

### 2. GitHub repository
1. Create a free account at github.com.
2. Create a new **private** repository, for example `trading-bot`.
3. Upload every file and folder from this project, keeping the structure, including the hidden `.github` folder. The simplest route is GitHub's "uploading an existing file" link, dragging in the unzipped folder's contents.

### 3. Add your keys as secrets
In the repository: **Settings → Secrets and variables → Actions**.

**Secrets** tab, "New repository secret" for each:

| Name | Value |
| --- | --- |
| `ALPACA_API_KEY` | your Alpaca paper API Key ID |
| `ALPACA_SECRET_KEY` | your Alpaca paper Secret Key |

**Variables** tab:

| Name | Value |
| --- | --- |
| `START_EQUITY` | your paper account's starting balance, e.g. `100000` |
| `NOTIFY` | `twilio` for SMS, `telegram` for Telegram, or `console` to only log |

### 4. Texts

**Option A: real SMS through Twilio**
1. Sign up at twilio.com. The free trial includes credit and can text your own verified phone number. Trial texts start with a short "Sent from your Twilio trial account" note.
2. Get a Twilio phone number from the console.
3. Add these secrets: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` (the Twilio number, like `+15551234567`), and `TEXT_TO_NUMBER` (your cell, same format).
4. Set the `NOTIFY` variable to `twilio`.

US carriers require registration for business texting. If you upgrade from the trial, Twilio walks you through it; texting only yourself on the trial avoids it.

**Option B: Telegram (free, no registration)**
1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token. Save it as the `TELEGRAM_BOT_TOKEN` secret.
2. Send your new bot any message, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy the number after `"chat":{"id":`. Save it as the `TELEGRAM_CHAT_ID` secret.
3. Set the `NOTIFY` variable to `telegram`.

### 5. Turn it on and test
1. Open the **Actions** tab and enable workflows if GitHub asks.
2. Click **trading-bot → Run workflow**, choose `recap`, and run it. Within a minute or two you should get a text. If not, open the run to read the error.
3. From then on it runs by itself every weekday and skips market holidays.

A manual `premarket` run places real paper orders. Only run it before 9:28am ET or after the close; Alpaca rejects market-on-open orders during the trading day.

## Good to know

- **No memory needed.** Each run recomputes the right position from price history, so a missed run fixes itself the next morning.
- **Re-runs are safe.** The premarket job cancels any unfilled orders before placing new ones.
- **Timing.** GitHub's scheduler can start jobs 10–30 minutes late. The premarket window runs to 9:20am, so a late start still beats the 9:28am cutoff.
- **Testing without Alpaca:** `python -m bot.main premarket --dry-run --csv data/spy_us_d.csv --asof 2026-10-15`
- **Backtests:** run from this folder: `python backtest/backtest.py` (dual momentum), `python backtest/daily_strategies.py` (daily strategies), `python backtest/sso_synthetic.py` and `python backtest/sso_real.py` (the 2x versions). Price data is in `data/` (Stooq, through Oct 15, 2026).
- **Buying power.** Sells and buys go in at the same open, so the bot relies on the paper account's default margin to cover the moment between them. It never borrows beyond that: targets always add up to 98% of the account.
- **Going live** later means setting `ALPACA_PAPER` to `false` in the workflow and using live keys. Don't do this until months of paper results look like the backtest.
