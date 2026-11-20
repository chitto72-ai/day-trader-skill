---
name: trader-btc5m
description: >-
  BTC 5-Minute Odds. Prices "Bitcoin up or down in the next 5 minutes"
  markets (Bybit and similar) against BTC's own history. Downloads 60 days of
  free 1-minute BTCUSDT bars, splits them into 5-minute UTC windows, and
  measures how often the next window closed up for each state the market can
  be in (RSI on 1-minute closes and the price against its 60-minute EMA).
  Takes the decimal odds you are offered for UP and DOWN, prints the
  break-even, the venue margin, the expected value of each side and a
  quarter-Kelly stake capped at 2 %, and says SKIP when neither side clears
  its break-even. Use when the user says "btc up or down", "5 minute odds",
  "bybit up down", "should I take up or down", "btc next 5 minutes", "is this
  bet +EV", or asks for the odds of a short BTC binary.
argument-hint: "[--up ODDS --down ODDS]"
---

# trader-btc5m

A 5-minute Up or Down market pays a fixed multiple if BTC closes the window
above (or below) where it opened. The only question that matters is
whether the probability of your side is higher than the break-even the odds
imply. This skill answers that from BTC's own last 60 days. It is not a
prediction, and most of the time the honest answer is SKIP: a 5-minute
window is mostly noise and the venue keeps a margin on both sides.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/btc5m.py" odds --up 1.92 --down 1.88              # base rate only
python3 "${CLAUDE_SKILL_DIR}/btc5m.py" signal --up 1.92 --down 1.88            # live state + EV
python3 "${CLAUDE_SKILL_DIR}/btc5m.py" signal --up 1.92 --down 1.88 --bankroll 500
python3 "${CLAUDE_SKILL_DIR}/btc5m.py" watch --up 1.92 --down 1.88             # every 5-minute window
python3 "${CLAUDE_SKILL_DIR}/btc5m.py" backtest                                # the full table
```

Other flags: `--days N` (history, default 60), `--json`, `--offline FILE`
(a saved JSON list of 1-minute klines), `--now "2026-11-20 11:30"` (UTC) to
pin the clock, `--state-dir DIR` (default `trader/state`), `--delay S`
(watch: seconds after each boundary, default 3).

## What it reads and writes

| file | what |
| --- | --- |
| Binance market-data mirror | `data-api.binance.vision` BTCUSDT 1-minute klines, free, no key. The first run pulls 60 days (about 90 requests), later runs only the new bars |
| `trader/state/btc5m-1m.json` | the cached 1-minute history |

Bybit's own API is geo-blocked in many regions, so the history comes from
spot BTCUSDT. It tracks Bybit's index within a few dollars, but the venue
settles on its own price, and over 5 minutes a few dollars can decide a
window.

## How it works

1. **The windows.** Every 5-minute UTC window (:00, :05, ...) with all five
   1-minute bars. Up means the last close is above the first open. A tie
   loses both sides here. Check your venue's rule.
2. **The state.** At the close of each window: RSI(14) on 1-minute closes in
   five bands (<40, 40-47, 47-53, 53-60, >60) and the price against its
   60-minute EMA in three (below -0.1 %, within 0.1 %, above +0.1 %). That
   makes 15 cells.
3. **The probability.** For the cell the market is in now: the share of
   past windows in that cell whose NEXT window closed up, shrunk toward the
   overall rate with a weight of 300 windows, so a thin cell cannot shout.
4. **The money.** With decimal odds Q a side breaks even at 1 / Q. The EV
   per unit is p x Q - 1. The margin on the pair is 1/Q_up + 1/Q_down - 1.
   Positive EV on a side: it prints the side and, with `--bankroll`, a
   quarter-Kelly stake capped at 2 % of the bankroll. Otherwise: SKIP.
5. **The backtest** prints every cell three ways: all 60 days, the first
   two thirds and the last third. A cell is only worth anything if the last
   third agrees with the first two.

## Steps

1. Ask the user for the exact decimal odds they see for UP and DOWN on the
   current window. Without them there is no EV, only a probability.
2. Run `btc5m.py signal --up X --down Y` as close to the window's open as
   possible. If minutes have already passed, the tool says so: the live
   quote already prices that move, and the history does not.
3. Tell the user, in this order: the window, the state (RSI, vs EMA60), the
   cell's history, P(up) and P(down), each side's break-even and EV, the
   margin, then the verdict line exactly as printed.
4. On SKIP, say so plainly. Do not search for a reason to bet.
5. Say once that this is a 60-day history, not a prediction. A regime
   change (a trend reversal, news, a liquidation cascade) can erase any cell.

## Example

20 November 2026, 60 days to 13:15 UTC, 17,278 windows. BTC fell 24 % in
those 60 days and the next window closed up 48.95 % of the time. The one cell
that held in both halves: RSI under 40 and below the EMA60, 53.4 % up (53.6 %
in the first two thirds, 53.1 % in the last third, 918 windows). Every other
cell sat between 46 % and 52 %.

```
Cell RSI 40-47 / below EMA60: 579 of 1188 past windows went up next. Base 48.95 %.
P(up) 48.78 %, P(down) 51.22 % (ties lose both).
  UP    odds 1.920  needs 52.08 %  has 48.78 %  EV  -6.34 % per unit
  DOWN  odds 1.880  needs 53.19 %  has 51.22 %  EV  -3.71 % per unit
  Venue margin on this pair: 5.27 %.
SKIP: neither side clears its break-even at these odds.
```

Even the best cell, at 53.4 %, clears the break-even only at odds of about
1.88 or better. That is what the user should hear first.

## Rules

- Every number comes from the run. Never round 52 % up into an edge.
- The verdict line is printed by the tool. Repeat it, do not rewrite it.
- Nothing here places a bet. It is not financial advice. The user decides,
  and only says yes for a stake they can lose in full.
- Never suggest more than the printed stake, and never martingale (doubling
  after a loss).
- Data is free and unofficial, 1-minute bars, never tick by tick.
- Binary bets on 5-minute moves lose most people money, and the venue's
  margin is the reason. Say it once.
