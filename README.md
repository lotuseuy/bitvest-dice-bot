# BitVest Autobet Bot

A fully-featured autobet bot for [BitVest.io](https://bitvest.io) with a mobile-friendly web dashboard. Supports both standard mode (configurable via UI) and advanced mode (Lua scripting).

## Features

- **Standard Mode** — Configure chance, progression, TP/SL, and safety nets directly from the dashboard
- **Lua Script Mode** — Write custom strategies in Lua for full control over bet logic
- **Mobile Dashboard** — Monitor and control the bot from your phone
- **Session Management** — Automatic TP/SL per session with configurable thresholds
- **Safety Nets** — Bet cap protection, long session safety, profit lock, seed reset on consecutive SL
- **Live Stats** — Real-time balance, session PnL, win rate, max drawdown, locked profit

## Quick Start

### 1. Install Python 3.10+ and dependencies

```bash
pip install fastapi uvicorn aiohttp lupa aiofiles
```

### 2. Export your BitVest cookies

In your browser, open the BitVest.io dice page. Open DevTools → Application → Cookies. Export cookies as JSON (use a browser extension like "EditThisCookie") or copy them as `key=value; key=value` format.

### 3. Save cookies

Create a file called `.bitvest_cookies.json` in the same directory as the bot:

```json
[
  {"name": "PHPSESSID", "value": "YOUR_SESSION_ID", "domain": "bitvest.io", "path": "/"},
  {"name": "remember", "value": "YOUR_REMEMBER_TOKEN", "domain": "bitvest.io", "path": "/"}
]
```

> You can also paste cookies directly from the dashboard's **Connection** section.

### 4. Run the bot

```bash
python bitvest_bot.py 8090
```

Then open **http://localhost:8090** in your browser.

## Dashboard Guide

### Connection Panel
- **CONNECT** — Tests your cookies against BitVest and displays your current balance
- **Cookie Input** — Paste new cookies (JSON array or `key=value` format)
- **SAVE COOKIES** — Saves cookies to server
- **LOAD** — Loads existing cookies into the input field

### Stats Overview
| Card | Description |
|------|-------------|
| **Balance** | Current account balance + total PnL vs target |
| **BETS** | Total bets placed + win rate % |
| **SESSIONS** | Number of completed sessions + current TP/SL config |
| **SESSION PnL** | Current session profit/loss |
| **LOCKED** | Profit already secured (removed from playable balance) |
| **Max DD** | Maximum drawdown percentage |
| **SL Streak** | Consecutive SL hits before seed reset |

### Standard Mode Config

| Parameter | Default | Description |
|-----------|---------|-------------|
| **Chance** | 17% | Dice win chance |
| **Bet %** | 0.3% | Base bet as % of balance |
| **Prog Win** | 0.943 | Multiply bet by this on win |
| **Prog Loss** | 1.035 | Multiply bet by this on loss |
| **TP** | 3% | Take profit per session (% of session start balance) |
| **SL** | 20% | Stop loss per session (% of session start balance) |
| **Profit Lock** | 30% | Lock 50% of excess profit above this threshold |
| **Max SL → Seed Reset** | 3 | Reset seed after N consecutive session SLs |
| **Max Roll / Session** | 500 | Force break-even target after N rolls |
| **Global Target** | 1,000,000 | Bot stops when balance reaches this |

### Safety Features

1. **Bet Cap (2x Base Bet)** — If bet reaches 2× the session's base bet:
   - In profit → session resets (profit secured)
   - In loss → TP target drops to 0% (break even)

2. **Long Session Safety** — After `max_session_rolls` rolls without TP/SL, TP target drops to 0% (break even)

3. **Profit Lock** — When balance exceeds `start_balance × (1 + profit_lock%)`, 50% of excess is locked. The locked amount is withdrawn from playable balance.

4. **Seed Reset** — After `max_consecutive_sl` session stop-losses, the bot resets the seed pair (disconnect + reconnect) and starts fresh.

### Lua Script Mode

Write custom strategies in Lua. Available callbacks:

```lua
function init(state)
  -- state.balance, state.start_balance
  -- Called once when bot starts
end

function get_bet(state)
  -- state.balance, state.total_bets, state.session_count
  -- Return: {amount=N, chance=N, direction="over"|"under"}
end

function on_bet(result)
  -- result.win (bool), result.profit, result.balance, result.roll
  -- Return: {action="reset_session"|"reset_seed"|"stop", bet=N}
end
```

Place scripts in the `scripts/` directory. Select and manage them from the Lua tab in the dashboard.

## Architecture

```
bitvest_bot.py              # FastAPI server + BitVest client + bot logic
bitvest_bot_dashboard.html  # Mobile-friendly web UI
scripts/                    # Lua strategy scripts
.bitvest_cookies.json       # Auth cookies (auto-generated)
.bitvest_bot_progress.json  # Saved bot state (auto-generated)
```

## Safety Notes

- **Always start with small bets** and monitor before scaling up
- The bot saves progress to `.bitvest_bot_progress.json` — it resumes from last state on restart
- Cookies expire — if connection fails, re-export fresh cookies from your browser
- Lock profit regularly — the profit lock feature helps secure gains
- This bot is for educational purposes. Gamble responsibly.

## Donations

If you find this useful, consider a small donation:

| Coin | Address |
|------|---------|
| **BTC** | `bc1qq602hqt06me4xktucpyg2g2gqtplygtg7k26qg` |
| **ETH/EVM** | `0x64CC70c681cE00D996fa0611AC5137b753630EF8` |
| **SOL** | `6ciDUePURpSVnucBpkvKJ2BGNpFidhZtBQMCadFCSCuG` |
| **LTC** | `ltc1ql38dx0psy86nm2s6n0j03svpewzr26g0s087xk` |
| **BCH** | `qqs4apfx5eyyjtckljqzxnmryvslzh76cs2tr9yr87` |
| **DOGE** | `DKtvPaqiwACBptoEBunSzMfTUHeJUp5K27` |

## License

MIT
