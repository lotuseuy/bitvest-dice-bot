"""BitVest Autobet Bot — Standard + Lua Advanced modes."""
import asyncio, json, logging, random, sys, time
from collections import deque
from pathlib import Path
from typing import Any, Optional

import aiohttp
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

sys.path.insert(0, "/root/Documents/Codex/2026-07-21/buatkan-sebuah-bot-autobet-untuk-permainan")
from backend.bitvest import BitvestClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("bitvest-bot")
COOKIE_FILE = Path(".bitvest_cookies.json")
PROGRESS_FILE = Path(".bitvest_bot_progress.json")
SCRIPTS_DIR = Path("scripts")
SCRIPTS_DIR.mkdir(exist_ok=True)
CURRENCY, MIN_BET = "tok", 1.0

# ── Default configs ──
STANDARD_DEFAULTS = {
    "mode": "standard",
    "dice_chance": 17.0,
    "dice_bet_pct": 0.003,
    "dice_prog_win": 0.943,
    "dice_prog_loss": 1.035,
    "dice_tp_pct": 0.03,
    "dice_stage_sl_pct": 0.20,
    "profit_lock_pct": 0.30,
    "max_consecutive_sl": 3,
    "max_session_rolls": 500,
    "bet_delay": 0.1,
    "global_target": 1_000_000.0,
}

state: dict[str, Any] = {
    "running": False, "connected": False, "dry_run": False,
    "balance": 0., "start_balance": 0., "peak": 0., "locked_profit": 0.,
    "total_bets": 0, "wins": 0, "losses": 0, "total_profit": 0., "max_dd": 0.,
    "cycle": 0, "phase": "recovery", "status": "idle",
    "last_error": "", "config": STANDARD_DEFAULTS.copy(),
    "session_start": 0., "session_profit": 0., "session_wins": 0, "session_losses": 0,
    "session_count": 0, "session_base_bet": 0.,
    "dice_next_bet": 0., "stage_start": 0.,
    "session_rolls": 0, "consecutive_sl": 0, "seed_resets": 0,
    "max_session_rolls": 500,
    "active_script": "",
    "lua_log": [],
}

bets: deque = deque(maxlen=300)
ws_clients: list = []
bot_task = None
client_ref = None


# ═══════════════════════════════════════════
# COOKIES & PROGRESS
# ═══════════════════════════════════════════
def load_cookies():
    raw = json.loads(COOKIE_FILE.read_text())
    return {x["name"]: x["value"] for x in raw if x.get("name")}

def save_progress():
    data = dict(state)
    data["updated_at"] = time.time()
    PROGRESS_FILE.write_text(json.dumps(data, indent=2, default=str))

def load_progress():
    if not PROGRESS_FILE.exists(): return
    try:
        saved = json.loads(PROGRESS_FILE.read_text())
        saved.pop("config", None)
        state.update({k: v for k, v in saved.items() if k in state})
    except: pass


# ═══════════════════════════════════════════
# LUA ENGINE
# ═══════════════════════════════════════════
class LuaEngine:
    def __init__(self):
        self.lua = None
        self.script_name = ""
        self.globals = {}
        try:
            from lupa import LuaRuntime
            self.lua = LuaRuntime(unpack_returned_tuples=True)
            log.info("Lua engine initialized")
        except Exception as e:
            log.error("Lua engine init failed: %s", e)

    def load_script(self, name: str) -> bool:
        path = SCRIPTS_DIR / f"{name}.lua"
        if not path.exists():
            log.error("Script not found: %s", path)
            return False
        try:
            code = path.read_text()
            self.lua.execute(code)
            self.script_name = name
            log.info("Loaded Lua script: %s", name)
            return True
        except Exception as e:
            log.error("Lua load error: %s", e)
            state["last_error"] = f"Lua error: {e}"
            return False

    def call(self, func_name: str, *args):
        if not self.lua: return None
        try:
            func = self.lua.globals().get(func_name)
            if func is None: return None
            return func(*args)
        except Exception as e:
            log.error("Lua call %s error: %s", func_name, e)
            return None

    def reset(self):
        if not self.lua: return
        try:
            from lupa import LuaRuntime
            self.lua = LuaRuntime(unpack_returned_tuples=True)
            if self.script_name:
                self.load_script(self.script_name)
        except: pass

lua_engine = LuaEngine()


# ═══════════════════════════════════════════
# BITVEST CLIENT
# ═══════════════════════════════════════════
class DiceClient(BitvestClient):
    async def action(self, payload):
        before = self.balance
        data = {"token": self._session_token or "", "secret": self._secret or "",
                "user_seed": self._user_seed or "", "currency": self._normalize_currency(CURRENCY), "v": "101"}
        for key, value in list(payload.items()):
            payload.pop(key)
            if isinstance(value, dict):
                for nk, nv in value.items(): payload[f"{key}[{nk}]"] = str(nv)
            else: payload[key] = str(value)
        data.update(payload)
        async with self.session.post(f"{self.BASE_URL}/action.php", data=data,
                                     timeout=aiohttp.ClientTimeout(total=20)) as resp:
            raw = await resp.json()
        if not isinstance(raw, dict) or not raw.get("success"):
            return None
        if isinstance(raw.get("session_token"), str) and raw["session_token"]:
            self._session_token = raw["session_token"]
        nested = raw.get("data")
        if isinstance(nested, dict): self._extract_balance(nested)
        if self.balance == before and isinstance(nested, dict):
            result = nested.get("game_result") or {}
            def num(v):
                if isinstance(v, list): return sum(num(x) for x in v)
                try: return float(v)
                except: return 0.
            self.balance = num(result.get("balance")) or self.balance
        return raw

async def execute_dice(client, amount, chance, direction="over"):
    try:
        target = 100 - chance if direction == "over" else chance
        condition = "gt" if direction == "over" else "lt"
        result = await client.place_bet(amount, target, condition=condition, currency="tok")
        if not result: return None
        return {"game": "dice", "stake": round(amount, 8), "payout": round(result.get("payout", 0), 8),
                "profit": round(result.get("profit", 0), 8), "win": result.get("win", False),
                "detail": {"roll": round(result.get("roll", 0), 3), "target": target, "direction": direction},
                "proof": {}}
    except Exception as exc:
        log.error("Execute error: %s", exc)
        return None


# ═══════════════════════════════════════════
# CORE HELPERS
# ═══════════════════════════════════════════
def phase_for(balance, start):
    if not start: return "recovery"
    gain = (balance - start) / start
    return "recovery" if gain < .05 else "attack" if gain < .20 else "hunt"

def planned_bet():
    cfg = state["config"]
    return max(MIN_BET, state["balance"] * cfg.get("dice_bet_pct", 0.003))

def check_profit_lock():
    cfg = state["config"]
    sb = state["start_balance"]
    if sb <= 0: return
    gain_pct = (state["balance"] - sb) / sb
    if gain_pct >= cfg.get("profit_lock_pct", 0.30):
        excess = state["balance"] - sb * (1 + cfg.get("profit_lock_pct", 0.30))
        if excess > 0:
            lock = excess * 0.5
            state["locked_profit"] += lock
            state["balance"] -= lock
            state["start_balance"] = state["balance"]
            log.info("PROFIT LOCK: %.2f → total %.2f | new start=%.2f", lock, state["locked_profit"], state["start_balance"])

async def broadcast(message):
    dead = []
    for ws in ws_clients:
        try: await ws.send_json(message)
        except: dead.append(ws)
    for ws in dead: ws_clients.remove(ws)

def record(result, planned_amount):
    s = state
    s["cycle"] += 1; s["total_bets"] += 1
    s["wins"] += result["profit"] > 0; s["losses"] += result["profit"] <= 0
    s["session_wins"] += result["profit"] > 0; s["session_losses"] += result["profit"] <= 0
    bets.appendleft({"number": s["total_bets"], **result, "planned": planned_amount,
                     "balance": s["balance"], "phase": s["phase"], "timestamp": time.time()})
    save_progress()

def _reset_session(reason):
    log.info("SESSION RESET: %s | bal=%.2f | locked=%.2f", reason, state["balance"], state["locked_profit"])
    state["session_count"] += 1
    state.update(
        session_start=state["balance"], session_profit=0.,
        session_wins=0, session_losses=0,
        stage_start=state["balance"], dice_next_bet=0.,
        session_base_bet=0., consecutive_sl=0, session_rolls=0,
    )
    state["dice_next_bet"] = planned_bet()

async def reset_seed(client):
    log.info("SEED RESET: reconnecting...")
    state["seed_resets"] += 1
    try:
        if client: await client.disconnect()
        await asyncio.sleep(2)
        new_client = DiceClient(cookies=load_cookies(), currency=CURRENCY)
        if await new_client.connect():
            state["balance"] = new_client.balance
            state["connected"] = True
            log.info("SEED RESET: OK, balance=%.2f", new_client.balance)
            return new_client
    except Exception as e:
        log.error("SEED RESET error: %s", e)
    return client


# ═══════════════════════════════════════════
# STANDARD MODE — after_bet
# ═══════════════════════════════════════════
async def standard_after_bet(result, client):
    cfg = state["config"]
    state["session_rolls"] = state.get("session_rolls", 0) + 1
    progress = cfg.get("dice_prog_loss", 1.035) if not result["win"] else cfg.get("dice_prog_win", 0.943)
    state["dice_next_bet"] = max(MIN_BET, state["dice_next_bet"] * progress)

    gain = (state["balance"] - state["stage_start"]) / state["stage_start"] if state["stage_start"] else 0.
    sl = cfg.get("dice_stage_sl_pct", 0.20)
    tp = cfg.get("dice_tp_pct", 0.03)

    sb = state.get("session_base_bet", 0.)
    cbet = state["dice_next_bet"]
    bet_doubled = sb > 0 and cbet >= sb * 2
    log.info("CHECK: win=%s gain=%.3f%% sl=%.1f%% tp=%.1f%% bal=%.2f stage=%.2f rolls=%d bet=%.4f",
             result["win"], gain*100, sl*100, tp*100, state["balance"], state["stage_start"], state.get("session_rolls",0), cbet)

    if bet_doubled:
        if gain > 0:
            _reset_session(f"safety profit cut (bet {cbet:.2f} >= 2x base {sb:.2f})")
            state["status"] = f"safety profit cut → {state['balance']:.2f}"
            await broadcast({"type": "status", **public_state()})
            return client
        else:
            tp = 0.

    max_rolls = cfg.get("max_session_rolls", 500)
    if state.get("session_rolls", 0) >= max_rolls:
        tp = 0.

    if gain >= tp:
        _reset_session(f"TP {gain:.1%}")
        state["status"] = f"sesi TP {gain:.1%} → {state['balance']:.2f}"
        await broadcast({"type": "status", **public_state()})
    elif gain <= -sl:
        state["consecutive_sl"] += 1
        log.info("SL HIT #%d (gain=%.1f%%)", state["consecutive_sl"], gain * 100)
        if state["consecutive_sl"] >= cfg.get("max_consecutive_sl", 3):
            client = await reset_seed(client)
            _reset_session(f"seed reset after {cfg['max_consecutive_sl']} SL")
        else:
            _reset_session(f"SL {gain:.1%} (#{state['consecutive_sl']}/{cfg['max_consecutive_sl']})")
        state["status"] = f"SL → {state['balance']:.2f}"
        await broadcast({"type": "status", **public_state()})

    check_profit_lock()
    return client


# ═══════════════════════════════════════════
# LUA MODE — after_bet
# ═══════════════════════════════════════════
async def lua_after_bet(result, client):
    """Call lua on_bet() after each bet, let script control everything."""
    ret = lua_engine.call("on_bet", {
        "win": result["win"],
        "profit": result["profit"],
        "balance": state["balance"],
        "start_balance": state["start_balance"],
        "total_bets": state["total_bets"],
        "session_count": state["session_count"],
        "roll": result["detail"]["roll"],
    })
    if ret is not None:
        try:
            if isinstance(ret, dict):
                action = ret.get("action", "continue")
                if action == "reset_seed":
                    client = await reset_seed(client)
                elif action == "reset_session":
                    _reset_session(f"lua: {ret.get('reason', 'script')}")
                elif action == "stop":
                    state["running"] = False
                    state["status"] = "stopped by lua script"
                if "bet" in ret:
                    state["dice_next_bet"] = max(MIN_BET, float(ret["bet"]))
                if "chance" in ret:
                    state["config"]["dice_chance"] = float(ret["chance"])
                if "direction" in ret:
                    state["config"]["dice_direction"] = ret["direction"]
        except Exception as e:
            log.error("Lua return parse error: %s", e)
    check_profit_lock()
    return client


# ═══════════════════════════════════════════
# MAIN BOT LOOP
# ═══════════════════════════════════════════
async def run_bot(client, dry_run):
    cfg = state["config"]
    mode = cfg.get("mode", "standard")
    state.update(running=True, dry_run=dry_run, status="running", last_error="")
    await broadcast({"type": "status", **public_state()})

    # Initialize lua if needed
    if mode == "lua" and cfg.get("active_script"):
        if not lua_engine.load_script(cfg["active_script"]):
            state["status"] = "error: lua script load failed"
            state["running"] = False
            return
        lua_engine.call("init", {
            "balance": state["balance"],
            "start_balance": state["start_balance"],
        })

    failures = 0
    try:
        while state["running"]:
            if not dry_run and client and client.balance and client.balance > 0:
                state["balance"] = client.balance
            state["total_profit"] = state["balance"] - state["start_balance"]
            state["phase"] = phase_for(state["balance"], state["start_balance"])
            state["session_profit"] = state["balance"] - state["session_start"]
            state["max_dd"] = max(state["max_dd"], (state["start_balance"] - state["balance"]) / state["start_balance"] * 100 if state["start_balance"] else 0)
            state["peak"] = max(state["peak"], state["balance"])

            if state["balance"] < MIN_BET: state["status"] = "stopped: minimum balance"; break
            if state["balance"] >= state["config"].get("global_target", 1e6): state["status"] = "stopped: target reached"; break

            # Determine bet params
            if mode == "lua":
                bet_info = lua_engine.call("get_bet", {
                    "balance": state["balance"],
                    "total_bets": state["total_bets"],
                    "session_count": state["session_count"],
                })
                if bet_info and isinstance(bet_info, dict):
                    amount = max(MIN_BET, float(bet_info.get("amount", MIN_BET)))
                    chance = float(bet_info.get("chance", state["config"]["dice_chance"]))
                    direction = bet_info.get("direction", "over")
                else:
                    amount = state["dice_next_bet"] if state["dice_next_bet"] >= MIN_BET else planned_bet()
                    chance = state["config"]["dice_chance"]
                    direction = state["config"].get("dice_direction", "over")
            else:
                amount = state["dice_next_bet"] if state["dice_next_bet"] >= MIN_BET else planned_bet()
                chance = state["config"]["dice_chance"]
                direction = "over"

            if mode == "standard" and state.get("session_base_bet", 0.) == 0. and amount >= MIN_BET:
                state["session_base_bet"] = amount

            state["status"] = f"#{state['cycle']+1} {amount:.2f} [{mode}] | locked={state['locked_profit']:.0f}"
            await broadcast({"type": "status", **public_state()})

            result, local_f = None, 0
            while state["running"] and local_f < 5:
                if dry_run:
                    roll = random.uniform(0, 99.999)
                    c = chance / 100
                    win = roll > (100 - chance) if direction == "over" else roll < chance
                    payout = round(amount * (0.99 / c), 8) if win else 0
                    result = {"game": "dice", "stake": amount, "payout": payout,
                              "profit": round(payout - amount, 8), "win": win,
                              "detail": {"roll": round(roll, 3), "target": 100 - chance if direction == "over" else chance, "direction": direction}, "proof": {}}
                    break
                result = await execute_dice(client, amount, chance, direction)
                if result: break
                local_f += 1; await asyncio.sleep(1 + local_f)

            if not result:
                failures += 1; state["last_error"] = "5 consecutive failures"; break
            failures = 0

            if not dry_run:
                server_bal = getattr(client, "balance", 0) or 0
                if server_bal > 0: state["balance"] = server_bal
            else:
                state["balance"] += result["profit"]
            state["total_profit"] = state["balance"] - state["start_balance"]
            state["session_profit"] = state["balance"] - state["session_start"]
            record(result, amount)

            if mode == "standard":
                client = await standard_after_bet(result, client)
            else:
                client = await lua_after_bet(result, client)

            await broadcast({"type": "bet", **(bets[0] if bets and not dry_run else {})})
            await asyncio.sleep(state["config"].get("bet_delay", 0.1))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.exception("campaign error"); state.update(last_error=str(exc), status="error")
    finally:
        state.update(running=False, connected=False)
        if state["status"] in ("running", "idle"): state["status"] = "stopped"
        save_progress(); await broadcast({"type": "status", **public_state()})


def public_state():
    keys = (
        "running", "connected", "dry_run", "balance", "start_balance",
        "peak", "locked_profit", "total_bets", "wins", "losses", "total_profit", "max_dd",
        "cycle", "phase", "status", "last_error", "config",
        "session_start", "session_profit", "session_wins", "session_losses", "session_count",
        "dice_next_bet", "stage_start", "session_base_bet",
        "session_rolls", "max_session_rolls", "consecutive_sl", "seed_resets", "active_script", "lua_log",
    )
    return {k: state.get(k) for k in keys}


load_progress()

# ═══════════════════════════════════════════
# FASTAPI
# ═══════════════════════════════════════════
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/")
async def index():
    return HTMLResponse(Path("bitvest_bot_dashboard.html").read_text())

@app.get("/api/stats")
async def stats(): return public_state()

@app.get("/api/balance")
async def fetch_balance():
    try:
        c = DiceClient(cookies=load_cookies(), currency=CURRENCY)
        if await c.connect():
            state["balance"] = c.balance; state["connected"] = True
            save_progress(); await c.disconnect()
            return {"balance": state["balance"], "ok": True}
        await c.disconnect()
    except Exception as e: log.error("Balance fetch failed: %s", e)
    return {"balance": state["balance"], "ok": False}

@app.post("/api/cookies")
async def update_cookies(payload: dict = {}):
    raw = payload.get("cookies", "").strip()
    if not raw:
        return {"error": "empty cookies"}
    try:
        # Accept JSON array format (browser export) or simple key=value format
        if raw.startswith("["):
            cookie_list = json.loads(raw)
            COOKIE_FILE.write_text(json.dumps(cookie_list, indent=2))
        else:
            # Simple format: key1=value1; key2=value2
            cookie_list = []
            for part in raw.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    cookie_list.append({"name": k.strip(), "value": v.strip(),
                                        "domain": "bitvest.io", "path": "/"})
            COOKIE_FILE.write_text(json.dumps(cookie_list, indent=2))
        return {"ok": True, "count": len(json.loads(COOKIE_FILE.read_text()))}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/cookies")
async def get_cookies():
    try:
        raw = json.loads(COOKIE_FILE.read_text())
        # Return as simple key=value string for display
        pairs = [f'{x["name"]}={x["value"]}' for x in raw if x.get("name") and x.get("value")]
        return {"ok": True, "text": "; ".join(pairs), "count": len(raw)}
    except:
        return {"ok": False, "text": "", "count": 0}

@app.post("/api/connect")
async def connect():
    if state["running"]:
        return {"error": "stop bot first"}
    try:
        c = DiceClient(cookies=load_cookies(), currency=CURRENCY)
        ok = await c.connect()
        if ok:
            state["balance"] = c.balance
            state["connected"] = True
            save_progress()
            info = {"ok": True, "balance": c.balance, "connected": True}
            await c.disconnect()
            return info
        await c.disconnect()
        return {"ok": False, "connected": False, "error": "connection failed - check cookies"}
    except Exception as e:
        return {"ok": False, "connected": False, "error": str(e)}

@app.get("/api/bets")
async def recent(): return list(bets)[:100]

@app.get("/api/scripts")
async def list_scripts():
    scripts = []
    for f in SCRIPTS_DIR.glob("*.lua"):
        scripts.append({"name": f.stem, "size": f.stat().st_size, "modified": f.stat().st_mtime})
    return scripts

@app.get("/api/scripts/{name}")
async def get_script(name: str):
    path = SCRIPTS_DIR / f"{name}.lua"
    if not path.exists(): return JSONResponse({"error": "not found"}, 404)
    return {"name": name, "code": path.read_text()}

@app.post("/api/scripts/{name}")
async def save_script(name: str, payload: dict = {}):
    code = payload.get("code", "")
    path = SCRIPTS_DIR / f"{name}.lua"
    path.write_text(code)
    return {"ok": True, "name": name}

@app.post("/api/scripts/{name}/delete")
async def delete_script(name: str):
    path = SCRIPTS_DIR / f"{name}.lua"
    if path.exists(): path.unlink()
    return {"ok": True}

@app.post("/api/reset")
async def reset():
    if state["running"]: return {"error": "stop first"}
    state["start_balance"] = 0.; state["locked_profit"] = 0.
    state["total_bets"] = 0; state["wins"] = 0; state["losses"] = 0
    state["total_profit"] = 0.; state["max_dd"] = 0.; state["cycle"] = 0
    state["session_count"] = 0; state["consecutive_sl"] = 0; state["seed_resets"] = 0
    state["locked_profit"] = 0.; state["peak"] = 0.
    state["session_start"] = 0.; state["session_profit"] = 0.
    state["session_wins"] = 0; state["session_losses"] = 0
    state["session_base_bet"] = 0.; state["dice_next_bet"] = 0.
    state["session_rolls"] = 0; state["stage_start"] = 0.; bets.clear()
    save_progress()
    return {"ok": True, "state": public_state()}

@app.post("/api/config")
async def config_api(payload: dict = {}):
    for key, value in payload.items():
        if key in state["config"]: state["config"][key] = type(state["config"][key])(value)
    save_progress()
    return {"ok": True, "config": state["config"]}

@app.post("/api/start")
async def start(payload: dict = {}):
    global bot_task, client_ref
    if state["running"]: return {"error": "already running"}
    for key, value in payload.items():
        if key in state["config"]: state["config"][key] = type(state["config"][key])(value)
    dry_run = bool(payload.get("dry_run", False))
    client_ref = DiceClient(cookies=load_cookies(), currency=CURRENCY)
    if dry_run:
        state["balance"] = float(payload.get("simulated_balance", state["balance"] or 1000.))
        state["connected"] = False
    else:
        if not await client_ref.connect(): return {"error": "connection failed"}
        if not getattr(client_ref, "_session_token", None):
            await client_ref.disconnect(); return {"error": "missing CSRF token"}
        state["balance"] = client_ref.balance; state["connected"] = True
    state.update(
        start_balance=state["balance"] if not state.get("start_balance") else state["start_balance"],
        peak=state["balance"], session_start=state["balance"],
        stage_start=state["balance"], dice_next_bet=planned_bet(),
    )
    save_progress()
    bot_task = asyncio.create_task(run_bot(client_ref, dry_run))
    return {"ok": True, "state": public_state()}

@app.post("/api/stop")
async def stop():
    global bot_task
    state["running"] = False
    if bot_task and not bot_task.done(): bot_task.cancel()
    if client_ref: await client_ref.disconnect()
    state.update(connected=False, status="stopped"); save_progress()
    return {"ok": True}

@app.websocket("/ws")
async def ws_handler(websocket: WebSocket):
    await websocket.accept(); ws_clients.append(websocket)
    try:
        await websocket.send_json({"type": "status", **public_state(), "bets": list(bets)[:30]})
        while True: await websocket.receive_text()
    except WebSocketDisconnect: pass
    finally:
        if websocket in ws_clients: ws_clients.remove(websocket)

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    print(f"BitVest Autobet Bot: http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
