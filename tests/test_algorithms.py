"""Brute-force checks for the streaming algorithms.  Run: python -m unittest -v"""
import random
import unittest

from trendpulse.decaying import DecayingWindow
from trendpulse.dgim import DGIM
from trendpulse.sliding import ExactWindowCount
from trendpulse.text import tokenize


def random_docs(n, vocab=60, seed=0):
    rng = random.Random(seed)
    words = [f"w{i}" for i in range(vocab)]
    # Zipf-like popularity so that some keywords are frequent and many are rare
    weights = [1.0 / (i + 1) for i in range(vocab)]
    return [set(rng.choices(words, weights, k=rng.randint(1, 6))) for _ in range(n)]


class DecayingWindowTest(unittest.TestCase):
    def brute(self, docs, c):
        scores = {}
        for d in docs:
            for w in scores:
                scores[w] *= (1 - c)
            for w in d:
                scores[w] = scores.get(w, 0.0) + 1.0
        return scores

    def test_baseline_matches_brute_force(self):
        docs = random_docs(3000)
        c = 0.01
        dw = DecayingWindow(c)
        for d in docs:
            dw.add(d)
        expected = self.brute(docs, c)
        self.assertEqual(len(dw), len(expected))
        for w, s in expected.items():
            self.assertAlmostEqual(dw.score(w), s, places=6)

    def test_rebase_keeps_scores(self):
        # c large enough that the lazy factor overflows the rebase limit many times
        docs = random_docs(20000, seed=3)
        c = 0.05
        dw = DecayingWindow(c)
        for d in docs:
            dw.add(d)
        expected = self.brute(docs[-2000:], c)   # older mass is ~e^-100, negligible
        for w, s in expected.items():
            self.assertAlmostEqual(dw.score(w), s, places=6)

    def test_pruning_matches_textbook_and_error_bound(self):
        docs = random_docs(4000, vocab=300, seed=1)
        c, theta = 0.01, 0.5
        pruned, exact = DecayingWindow(c, theta), DecayingWindow(c)
        # textbook per-step pruning, done naively
        ref = {}
        for d in docs:
            pruned.add(d)
            exact.add(d)
            for w in list(ref):
                ref[w] *= (1 - c)
            for w in d:
                ref[w] = ref.get(w, 0.0) + 1.0
            for w in [w for w, s in ref.items() if s < theta]:
                del ref[w]
        self.assertEqual(set(ref), {w for w, _ in pruned.items()})
        for w, s in ref.items():
            self.assertAlmostEqual(pruned.score(w), s, places=6)
        self.assertLess(len(pruned), len(exact))
        bound = pruned.error_bound()
        for w, s in exact.items():
            diff = s - pruned.score(w)
            self.assertGreaterEqual(diff, -1e-9)
            self.assertLess(diff, bound + 1e-9)

    def test_steps_match_brute_force(self):
        # per-day decay: several documents share a day (steps=0), gaps of several days
        rng = random.Random(7)
        docs = random_docs(2000, vocab=120, seed=7)
        steps = [rng.choice([0, 0, 0, 1, 1, 2, 5]) for _ in docs]
        c, theta = 0.05, 0.3
        exact, pruned = DecayingWindow(c), DecayingWindow(c, theta)
        ref, ref_p = {}, {}
        for d, k in zip(docs, steps):
            exact.add(d, steps=k)
            pruned.add(d, steps=k)
            for table in (ref, ref_p):
                for w in list(table):
                    table[w] *= (1 - c) ** k
                for w in d:
                    table[w] = table.get(w, 0.0) + 1.0
            for w in [w for w, s in ref_p.items() if s < theta]:
                del ref_p[w]
        for w, s in ref.items():
            self.assertAlmostEqual(exact.score(w), s, places=6)
        self.assertEqual(set(ref_p), {w for w, _ in pruned.items()})
        with self.assertRaises(ValueError):
            exact.add(["x"], steps=-1)

    def test_rejects_bad_threshold(self):
        with self.assertRaises(ValueError):
            DecayingWindow(0.01, 1.0)


class DGIMTest(unittest.TestCase):
    def run_stream(self, N, r, p, n=6000, seed=0, estimator="midpoint"):
        rng = random.Random(seed)
        dg, ex = DGIM(N, r, estimator), ExactWindowCount(N)
        bits = []
        worst = 0.0
        for t in range(n):
            bit = rng.random() < p
            bits.append(bit)
            if bit:
                dg.add(t)
                ex.add(t)
            truth = sum(bits[max(0, t - N + 1): t + 1])
            self.assertEqual(ex.count(t), truth)
            est = dg.estimate(t)
            if truth:
                worst = max(worst, abs(est - truth) / truth)
            else:
                self.assertEqual(est, 0.0)
            self.check_invariants(dg)
        return worst, dg

    def check_invariants(self, dg):
        sizes = [s for _, s in dg._buckets]
        self.assertEqual(sizes, sorted(sizes))
        for s in set(sizes):
            self.assertEqual(s & (s - 1), 0, "bucket size must be a power of two")
            self.assertLessEqual(sizes.count(s), dg.r)

    def test_error_within_bound(self):
        for r in (2, 3, 5):
            for estimator in ("midpoint", "textbook"):
                worst, dg = self.run_stream(N=500, r=r, p=0.3, seed=r, estimator=estimator)
                # The textbook "half of the oldest bucket" rule is off by 50% when the
                # oldest bucket has size 1 and truth is 1, so its bound is max(.., 0.5).
                bound = dg.error_bound() if estimator == "midpoint" else max(dg.error_bound(), 0.5)
                self.assertLessEqual(worst, bound + 1e-9, (r, estimator, worst))

    def test_bucket_count_is_logarithmic(self):
        _, dg = self.run_stream(N=1024, r=2, p=0.9, n=5000)
        self.assertLessEqual(dg.num_buckets, 2 * 11 + 1)

    def test_small_counts(self):
        dg = DGIM(N=10, r=2)
        dg.add(0)
        self.assertEqual(dg.estimate(0), 1)      # a single size-1 bucket is exact
        dg.add(1)
        dg.add(2)                                # buckets: size 1 (t=2), size 2 (t=1)
        self.assertEqual(dg.estimate(2), 2.5)    # truth 3, true count in [2, 3]
        self.assertEqual(dg.estimate(11), 1)     # window (1, 11]: size-2 bucket (t=1) expired



class TokenizerTest(unittest.TestCase):
    def test_tokenize(self):
        toks = tokenize("Over 4 Million Americans Roll Up Sleeves For Omicron-Targeted COVID-19 Boosters")
        self.assertEqual(toks, ["million", "americans", "roll", "sleeves", "omicron", "targeted",
                                "covid", "boosters"])

    def test_possessive_and_stopwords(self):
        self.assertEqual(tokenize("Trump's Rally: What Biden’s Team Says"), ["trump", "rally", "biden", "team"])

    def test_acronyms_kept_case_sensitively(self):
        self.assertEqual(tokenize("AI Rules In UK, EU And U.S. Debated At UN"),
                         ["ai", "rules", "u.k.", "eu", "u.s.", "debated", "un"])
        self.assertEqual(tokenize("Let Us Visit New York"), ["visit", "york"])   # pronoun "Us"
        self.assertEqual(tokenize("WATCH US OPEN FINAL"), ["open", "final"])     # all-caps headline

    def test_document_frequency(self):
        self.assertEqual(tokenize("Taxes, taxes and more TAXES"), ["taxes"])


if __name__ == "__main__":
    unittest.main()
