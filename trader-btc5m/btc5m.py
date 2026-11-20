#!/usr/bin/env python3
"""BTC 5-minute Up or Down odds for trader-btc5m. Standard library only.

For "BTC up or down in the next 5 minutes" markets (Bybit and the like):
prices each side from the history of BTC's own 5-minute windows, compares it
with the odds you are offered, and says whether either side has a positive
expected value. It never bets for you.

    python3 btc5m.py odds --up 1.92 --down 1.88          # EV of each side with the base rate
    python3 btc5m.py signal --up 1.92 --down 1.88        # live state + conditional odds + EV
    python3 btc5m.py signal --up 1.92 --down 1.88 --bankroll 500
    python3 btc5m.py watch --up 1.92 --down 1.88         # re-run signal at every 5-minute window
    python3 btc5m.py backtest                            # the table, in-sample and out-of-sample
    python3 btc5m.py signal --json

How a window is priced. A window runs from one 5-minute boundary (UTC) to
the next: "up" means the close at the end is above the open at the start. A
tie counts as a loss for both sides here; check your venue's rule. The
features are taken at the close of the window before, from 1-minute bars:

  - RSI(14) on 1-minute closes, in five bands: <40, 40-47, 47-53, 53-60, >60
  - the price against its 60-minute EMA, in three bands: below -0.1 %,
    within 0.1 %, above +0.1 %

Every past window falls into one of the 15 cells. The probability of "up"
for the cell the market is in now is the cell's own up rate, shrunk toward
the overall rate with a prior weight of 300 windows, so a thin cell cannot
shout. The backtest also prints the same cells measured only on the last
third of the history, fitted on the first two thirds.

The money side. With decimal odds Q (stake included) a side breaks even at
1/Q. EV per 1 unit staked is p * Q - 1. Below the venue's margin there is
nothing to take. With --bankroll it also prints a quarter-Kelly stake,
capped at 2 % of the bankroll, only when EV is positive.

Data: Binance's public market-data mirror (data-api.binance.vision),
BTCUSDT 1-minute klines, no key. Bybit's API is geo-blocked in many places;
spot BTCUSDT tracks Bybit's index within a few dollars, but your venue
settles on its own price. History is cached in trader/state/btc5m-1m.json
and topped up on each run (the first run downloads --days, default 60, about
90 requests). Offline: --offline FILE reads a JSON list of klines
[open_ms, open, high, low, close, volume, taker_buy_volume].

Nothing here is a prediction or financial advice. A 5-minute window is
mostly noise: the best cells in 60 days were about 53 %, which is below the
break-even of most quotes.
"""

import argparse
import datetime as _dt
import json
import math
import os
import sys
import time
import urllib.request

KLINES = "https://data-api.binance.vision/api/v3/klines?symbol=%s&interval=1m&limit=1000%s"
MIN = 60000
WIN = 5 * MIN
RSI_EDGES = [40.0, 47.0, 53.0, 60.0]
RSI_NAMES = ["<40", "40-47", "47-53", "53-60", ">60"]
EMA_EDGE = 0.001
EMA_NAMES = ["below", "near", "above"]
PRIOR = 300.0
KELLY_FRACTION = 0.25
KELLY_CAP = 0.02
UA = "day-trader-skill/btc5m"


# ---------------------------------------------------------------- data

def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _row(r):
    return [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[9])]


def fetch(symbol, start_ms, now_ms):
    out = []
    t = start_ms
    while t < now_ms:
        d = _get(KLINES % (symbol, "&startTime=%d" % t))
        if not d:
            break
        out.extend(_row(r) for r in d)
        nxt = int(d[-1][0]) + MIN
        if nxt <= t:
            break
        t = nxt
        if len(d) < 1000:
            break
    return out


def load_history(args, now_ms):
    if args.offline:
        with open(args.offline) as fh:
            rows = [list(r) for r in json.load(fh)]
        return rows
    path = os.path.join(args.state_dir, "btc5m-1m.json")
    rows = []
    if os.path.exists(path):
        try:
            with open(path) as fh:
                rows = json.load(fh)
        except (ValueError, OSError):
            rows = []
    start = now_ms - args.days * 86400 * 1000
    rows = [r for r in rows if r[0] >= start]
    since = rows[-1][0] + MIN if rows else start
    try:
        new = fetch(args.symbol, since, now_ms)
    except Exception as e:  # network down: use the cache if there is one
        if not rows:
            sys.exit("Could not reach %s (%s) and there is no cache yet." % ("data-api.binance.vision", e))
        print("Warning: could not top up the history (%s); using the cache." % e, file=sys.stderr)
        new = []
    seen = set(r[0] for r in rows)
    rows.extend(r for r in new if r[0] not in seen)
    rows.sort(key=lambda r: r[0])
    os.makedirs(args.state_dir, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(rows, fh, separators=(",", ":"))
    return rows


# ---------------------------------------------------------------- features

def indicators(rows):
    """Per 1-minute bar: RSI(14) (Wilder) and close / EMA60 - 1."""
    rsi, dema = [], []
    au = ad = None
    ema = None
    a_rsi = 1.0 / 14
    a_ema = 2.0 / 61
    prev = None
    for r in rows:
        c = r[4]
        if prev is None:
            au, ad = 0.0, 0.0
            ema = c
        else:
            ch = c - prev
            au = au + a_rsi * (max(ch, 0.0) - au)
            ad = ad + a_rsi * (max(-ch, 0.0) - ad)
            ema = ema + a_ema * (c - ema)
        prev = c
        rsi.append(100.0 if ad == 0 else 100.0 - 100.0 / (1.0 + au / ad))
        dema.append(c / ema - 1.0)
    return rsi, dema


def rsi_band(x):
    for i, e in enumerate(RSI_EDGES):
        if x < e:
            return i
    return len(RSI_EDGES)


def ema_band(x):
    if x < -EMA_EDGE:
        return 0
    if x > EMA_EDGE:
        return 2
    return 1


def windows(rows, now_ms):
    """Closed 5-minute windows: list of dicts with start, open, close, up and
    the features at the close of that window."""
    rsi, dema = indicators(rows)
    out = []
    cur = None
    for i, r in enumerate(rows):
        start = r[0] - r[0] % WIN
        if start + WIN > now_ms:
            break  # the live window is never used as history
        if cur is None or cur["start"] != start:
            if cur is not None and cur["n"] == 5:
                out.append(cur)
            cur = {"start": start, "open": r[1], "n": 0, "v": 0.0, "tb": 0.0}
        cur["n"] += 1
        cur["close"] = r[4]
        cur["v"] += r[5]
        cur["tb"] += r[6]
        cur["rsi"] = rsi[i]
        cur["dema"] = dema[i]
    if cur is not None and cur["n"] == 5 and cur["start"] + WIN <= now_ms:
        out.append(cur)
    for w in out:
        w["up"] = 1 if w["close"] > w["open"] else 0
        w["cell"] = (rsi_band(w["rsi"]), ema_band(w["dema"]))
    return out


def pairs(ws):
    """(features of window k, outcome of window k+1), only when k+1 follows k."""
    return [(a["cell"], b["up"]) for a, b in zip(ws, ws[1:]) if b["start"] - a["start"] == WIN]


def table(pr):
    cells = {}
    ups = 0
    for cell, up in pr:
        c = cells.setdefault(cell, [0, 0])
        c[0] += up
        c[1] += 1
        ups += up
    base = ups / float(len(pr)) if pr else 0.5
    return base, cells


def prob(base, cells, cell):
    u, n = cells.get(cell, [0, 0])
    return (u + PRIOR * base) / (n + PRIOR), u, n


# ---------------------------------------------------------------- money

def ev(p, q):
    return p * q - 1.0


def kelly(p, q):
    b = q - 1.0
    if b <= 0:
        return 0.0
    return max(0.0, (p * b - (1.0 - p)) / b)


def money_lines(p_up, up_q, down_q, bankroll):
    lines = []
    best = None
    for side, p, q in (("UP", p_up, up_q), ("DOWN", 1.0 - p_up, down_q)):
        if q is None:
            continue
        e = ev(p, q)
        lines.append("  %-4s  odds %.3f  needs %5.2f %%  has %5.2f %%  EV %+6.2f %% per unit"
                     % (side, q, 100.0 / q, p * 100, e * 100))
        if e > 0 and (best is None or e > best[2]):
            best = (side, p, e, q)
    if up_q and down_q:
        margin = 1.0 / up_q + 1.0 / down_q - 1.0
        lines.append("  Venue margin on this pair: %.2f %%." % (margin * 100))
    if best is None:
        verdict = "SKIP: neither side clears its break-even at these odds."
    else:
        verdict = "%s has a positive EV of %+.2f %% at these odds. Thin; the history is not a promise." % (
            best[0], best[2] * 100)
        if bankroll:
            f = min(KELLY_CAP, KELLY_FRACTION * kelly(best[1], best[3]))
            lines.append("  Quarter-Kelly stake, capped at %d %%: %.2f of %.2f." % (
                int(KELLY_CAP * 100), f * bankroll, bankroll))
    return lines, verdict, best


# ---------------------------------------------------------------- commands

def utc(ms):
    return _dt.datetime.fromtimestamp(ms / 1000.0, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def now_ms_of(args):
    if args.now:
        t = _dt.datetime.strptime(args.now, "%Y-%m-%d %H:%M").replace(tzinfo=_dt.timezone.utc)
        return int(t.timestamp() * 1000)
    return int(time.time() * 1000)


def need_history(ws):
    if len(ws) < 2000:
        sys.exit("Only %d closed 5-minute windows in the history; need at least 2,000 (about 7 days)." % len(ws))


def cmd_backtest(args):
    now = now_ms_of(args)
    ws = windows(load_history(args, now), now)
    need_history(ws)
    pr = pairs(ws)
    base, cells = table(pr)
    cut = int(len(pr) * 2 / 3)
    _, fit = table(pr[:cut])
    tbase, test = table(pr[cut:])
    res = {"from": utc(ws[0]["start"]), "to": utc(ws[-1]["start"] + WIN), "windows": len(pr),
           "base_up": base, "test_base_up": tbase, "cells": []}
    for i in range(len(RSI_NAMES)):
        for j in range(len(EMA_NAMES)):
            u, n = cells.get((i, j), [0, 0])
            fu, fn = fit.get((i, j), [0, 0])
            tu, tn = test.get((i, j), [0, 0])
            res["cells"].append({"rsi": RSI_NAMES[i], "vs_ema60": EMA_NAMES[j], "n": n,
                                 "up": u / float(n) if n else None,
                                 "fit_up": fu / float(fn) if fn else None,
                                 "test_up": tu / float(tn) if tn else None, "test_n": tn})
    if args.json:
        print(json.dumps(res, indent=2))
        return
    print("BTCUSDT 5-minute windows %s to %s: %d windows, up %.2f %% (last third %.2f %%)."
          % (res["from"], res["to"], len(pr), base * 100, tbase * 100))
    print("Next window up rate by the state at the close of the window before:")
    print("  %-6s %-6s %7s %8s %9s %9s %7s" % ("RSI1m", "EMA60", "n", "all", "first 2/3", "last 1/3", "n test"))
    for c in res["cells"]:
        f = lambda x: "    -" if x is None else "%5.1f %%" % (x * 100)
        print("  %-6s %-6s %7d %8s %9s %9s %7d" % (c["rsi"], c["vs_ema60"], c["n"], f(c["up"]),
                                                  f(c["fit_up"]), f(c["test_up"]), c["test_n"]))
    print("A cell only means something if the last third agrees with the first two thirds.")


def snapshot(args):
    now = now_ms_of(args)
    rows = load_history(args, now)
    ws = windows(rows, now)
    need_history(ws)
    base, cells = table(pairs(ws))
    last = ws[-1]
    p, u, n = prob(base, cells, last["cell"])
    nxt = last["start"] + WIN
    live = [r for r in rows if r[0] >= nxt]
    return {
        "now": utc(now), "window_start": utc(nxt), "window_end": utc(nxt + WIN),
        "window_open": live[0][1] if live else last["close"],
        "last_price": rows[-1][4], "minutes_in": len(live),
        "rsi_1m": round(last["rsi"], 1), "vs_ema60_pct": round(last["dema"] * 100, 3),
        "taker_buy_ratio_5m": round(last["tb"] / last["v"], 3) if last["v"] else None,
        "last_window_pct": round((last["close"] / last["open"] - 1) * 100, 3),
        "cell": {"rsi": RSI_NAMES[last["cell"][0]], "vs_ema60": EMA_NAMES[last["cell"][1]]},
        "cell_history": {"up": u, "n": n}, "base_up": round(base, 4), "p_up": round(p, 4),
        "history_windows": len(ws),
    }


def print_signal(s, args):
    lines, verdict, best = money_lines(s["p_up"], args.up, args.down, args.bankroll)
    if args.json:
        s = dict(s, verdict=verdict, best_side=best[0] if best else None)
        print(json.dumps(s, indent=2))
        return
    print("%s  BTCUSDT %.2f" % (s["now"], s["last_price"]))
    print("Window %s to %s, open %.2f%s." % (s["window_start"][11:16], s["window_end"][11:16], s["window_open"],
          (", %d min already gone" % s["minutes_in"]) if s["minutes_in"] else ""))
    print("State at the window before: RSI1m %.1f, vs EMA60 %+.3f %%, last 5m %+.3f %%, taker buy %.2f."
          % (s["rsi_1m"], s["vs_ema60_pct"], s["last_window_pct"], s["taker_buy_ratio_5m"] or 0))
    print("Cell RSI %s / %s EMA60: %d of %d past windows went up next. Base %.2f %%."
          % (s["cell"]["rsi"], s["cell"]["vs_ema60"], s["cell_history"]["up"], s["cell_history"]["n"],
             s["base_up"] * 100))
    print("P(up) %.2f %%, P(down) %.2f %% (ties lose both)." % (s["p_up"] * 100, (1 - s["p_up"]) * 100))
    if s["minutes_in"]:
        print("Note: the odds above are for the window from its open; %d minute(s) have passed, "
              "so the live quote already prices that move." % s["minutes_in"])
    if lines:
        print("\n".join(lines))
        print(verdict)
    else:
        print("Pass --up and --down (decimal odds) for the EV.")


def cmd_signal(args):
    print_signal(snapshot(args), args)


def cmd_watch(args):
    while True:
        now = time.time()
        wait = WIN / 1000.0 - (now % (WIN / 1000.0)) + args.delay
        time.sleep(wait)
        try:
            print_signal(snapshot(args), args)
        except SystemExit as e:
            print(e, file=sys.stderr)
        print("-" * 60)
        sys.stdout.flush()


def cmd_odds(args):
    now = now_ms_of(args)
    ws = windows(load_history(args, now), now)
    need_history(ws)
    base, _ = table(pairs(ws))
    print("Base rate %s to %s: up %.2f %% of %d windows." % (utc(ws[0]["start"]), utc(ws[-1]["start"] + WIN),
                                                          base * 100, len(ws)))
    lines, verdict, _ = money_lines(base, args.up, args.down, args.bankroll)
    print("\n".join(lines))
    print(verdict)


def main(argv=None):
    ap = argparse.ArgumentParser(description="BTC 5-minute Up or Down odds (trader-btc5m).")
    sub = ap.add_subparsers(dest="cmd")
    for name, fn in (("odds", cmd_odds), ("signal", cmd_signal), ("watch", cmd_watch), ("backtest", cmd_backtest)):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        p.add_argument("--symbol", default="BTCUSDT")
        p.add_argument("--days", type=int, default=60)
        p.add_argument("--state-dir", default=os.path.join("trader", "state"))
        p.add_argument("--offline", help="JSON list of 1m klines instead of the network")
        p.add_argument("--now", help='pin the clock, "YYYY-MM-DD HH:MM" UTC')
        p.add_argument("--json", action="store_true")
        if name != "backtest":
            p.add_argument("--up", type=float, help="decimal odds offered for UP (stake included)")
            p.add_argument("--down", type=float, help="decimal odds offered for DOWN")
            p.add_argument("--bankroll", type=float)
        if name == "watch":
            p.add_argument("--delay", type=float, default=3.0, help="seconds after the boundary")
    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help()
        return 2
    for k in ("up", "down"):
        v = getattr(args, k, None)
        if v is not None and v <= 1.0:
            ap.error("--%s is decimal odds and must be above 1.0" % k)
    if args.cmd == "odds" and not (args.up or args.down):
        ap.error("odds needs --up and/or --down")
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
