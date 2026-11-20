import io
import json
import os
import random
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import load

b5 = load("trader-btc5m", "btc5m")
T0 = 1790000000000 - 1790000000000 % 300000  # a 5-minute boundary


def synthetic(n_min, seed=7):
    rnd = random.Random(seed)
    rows, p = [], 60000.0
    for i in range(n_min):
        o = p
        p = p * (1 + rnd.gauss(0, 0.0005))
        v = 10.0
        rows.append([T0 + i * 60000, o, max(o, p) + 1, min(o, p) - 1, p, v, v * rnd.random()])
    return rows


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = b5.main(args)
    return code, out.getvalue(), err.getvalue()


class Money(unittest.TestCase):
    def test_break_even_and_ev(self):
        self.assertAlmostEqual(b5.ev(0.5, 2.0), 0.0)
        self.assertAlmostEqual(b5.ev(0.53, 1.9), 0.007)
        self.assertLess(b5.ev(0.52, 1.9), 0)

    def test_kelly_is_zero_without_an_edge(self):
        self.assertEqual(b5.kelly(0.5, 1.9), 0.0)
        self.assertAlmostEqual(b5.kelly(0.6, 2.0), 0.2)

    def test_skip_when_both_sides_are_negative(self):
        _, verdict, best = b5.money_lines(0.49, 1.9, 1.9, 1000)
        self.assertIsNone(best)
        self.assertTrue(verdict.startswith("SKIP"))

    def test_stake_is_capped_at_two_percent(self):
        lines, verdict, best = b5.money_lines(0.7, 2.0, 1.5, 1000)
        self.assertEqual(best[0], "UP")
        self.assertIn("20.00 of 1000.00", " ".join(lines))


class Windows(unittest.TestCase):
    def test_bands(self):
        self.assertEqual([b5.rsi_band(x) for x in (10, 40, 50, 59.9, 60)], [0, 1, 2, 3, 4])
        self.assertEqual([b5.ema_band(x) for x in (-0.002, 0, 0.002)], [0, 1, 2])

    def test_windows_are_aligned_and_skip_the_live_one(self):
        rows = synthetic(23)
        ws = b5.windows(rows, T0 + 23 * 60000)
        self.assertEqual(len(ws), 4)  # the 5th window has only 3 bars and is still live
        self.assertEqual(ws[1]["start"] - ws[0]["start"], 300000)
        self.assertEqual(ws[0]["open"], rows[0][1])
        self.assertEqual(ws[0]["close"], rows[4][4])
        self.assertEqual(ws[0]["up"], 1 if rows[4][4] > rows[0][1] else 0)

    def test_shrinkage_pulls_thin_cells_to_the_base(self):
        p, u, n = b5.prob(0.5, {(0, 0): [10, 10]}, (0, 0))
        self.assertAlmostEqual(p, 160 / 310.0)
        p, _, n = b5.prob(0.5, {}, (1, 1))
        self.assertEqual((p, n), (0.5, 0))


class Commands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.file = os.path.join(self.tmp, "k.json")
        with open(self.file, "w") as fh:
            json.dump(synthetic(12000), fh)
        self.now = "2026-09-21 00:00"
        self.base = ["--offline", self.file, "--state-dir", self.tmp]

    def test_signal_json_is_repeatable(self):
        args = ["signal", "--up", "1.8", "--down", "1.8", "--json"] + self.base
        code, out, _ = run(args + ["--now", "2026-09-30 00:00"])
        self.assertEqual(code, 0)
        s = json.loads(out)
        self.assertAlmostEqual(s["p_up"], 0.5, delta=0.05)
        self.assertTrue(s["verdict"].startswith("SKIP"))  # a random walk at 1.8 has no edge
        self.assertEqual(run(args + ["--now", "2026-09-30 00:00"])[1], out)

    def test_backtest_lists_fifteen_cells(self):
        code, out, _ = run(["backtest", "--json", "--now", "2026-09-30 00:00"] + self.base)
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["cells"]), 15)

    def test_refuses_thin_history(self):
        with open(self.file, "w") as fh:
            json.dump(synthetic(500), fh)
        with self.assertRaises(SystemExit):
            run(["odds", "--up", "1.9", "--down", "1.9", "--now", "2026-09-30 00:00"] + self.base)

    def test_odds_must_be_decimal(self):
        with self.assertRaises(SystemExit):
            run(["odds", "--up", "0.9"] + self.base)


if __name__ == "__main__":
    unittest.main()
