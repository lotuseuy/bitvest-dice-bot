-- ╔══════════════════════════════════════════════════════════════╗
-- ║  EDGE HUNTER v2 — For BitVest Autobet Bot Lua Mode          ║
-- ║  Flat betting, tight stops, compound gains                   ║
-- ╚══════════════════════════════════════════════════════════════╝

-- CONFIG
local BASE_BET_PCT     = 0.0015
local MAX_BET_PCT      = 0.05
local MIN_BET          = 1
local TARGET           = 49.5

local SESSION_TP       = 0.05
local SESSION_SL       = 0.03
local MAX_BETS         = 5

local LOSS_REDUCE      = 0.95
local WIN_INCREASE     = 1.05

-- STATE
session_num    = 0
total_profit   = 0
compound_pct   = 1.0
session_bets   = 0
session_bal    = 0
base_bet       = 0

function init(state)
    session_num = 0
    total_profit = 0
    compound_pct = 1.0
    session_bets = 0
    session_bal = state.balance
    base_bet = math.max(MIN_BET, state.balance * BASE_BET_PCT)
end

function get_bet(state)
    local bet = base_bet * compound_pct
    bet = math.max(MIN_BET, math.min(bet, state.balance * MAX_BET_PCT))
    return {amount = bet, chance = 50, direction = "over"}
end

function on_bet(result)
    session_bets = session_bets + 1
    total_profit = total_profit + result.profit

    local gain = (result.balance - session_bal) / session_bal

    -- TP hit
    if gain >= SESSION_TP then
        compound_pct = compound_pct * WIN_INCREASE
        session_num = session_num + 1
        session_bets = 0
        session_bal = result.balance
        return {action = "reset_session", reason = "edge_hunter_tp"}
    end

    -- SL hit
    if gain <= -SESSION_SL then
        compound_pct = compound_pct * LOSS_REDUCE
        session_num = session_num + 1
        session_bets = 0
        session_bal = result.balance
        return {action = "reset_session", reason = "edge_hunter_sl"}
    end

    -- Max bets per session
    if session_bets >= MAX_BETS then
        session_num = session_num + 1
        session_bets = 0
        session_bal = result.balance
        return {action = "reset_session", reason = "edge_hunter_maxbets"}
    end

    return nil
end
