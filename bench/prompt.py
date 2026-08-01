"""The fixed prompt.

This is a latency benchmark, not a capability benchmark. Every model gets this
exact prompt, every hour, forever -- that is the whole basis of comparability,
both across models and for one model over time.

So: DO NOT EDIT `TEXT` casually. Every run logs `prompt_sha`, and a change to
this string starts a new, incomparable series. If you must change it, treat the
hash change as a deliberate break and annotate it on the dashboard.

The task is deliberately non-trivial. A "say hello" prompt would measure almost
nothing; a real reasoning workload is where slowdowns actually surface. Nothing
executes the script -- we are timing the response, not grading it.
"""

from __future__ import annotations

import hashlib

TEXT = (
    "Conway's Game of Life runs on an unbounded two-dimensional grid of cells. At each "
    "step: a live cell with two or three live neighbours stays alive, a dead cell with "
    "exactly three live neighbours becomes alive, and every other cell is dead in the "
    "next step. Neighbours are the eight cells touching a cell orthogonally or "
    "diagonally.\n\n"
    "Start from the acorn pattern, with live cells at these coordinates:\n"
    "  (0, 0), (1, 2), (2, -1), (2, 0), (2, 3), (2, 4), (2, 5)\n\n"
    "Run 5000 generations and print the number of live cells at that point.\n\n"
    "Write a single self-contained Python 3 program that computes the answer and prints "
    "it. Use only the standard library, and make it efficient enough to finish in well "
    "under a minute. Reply with the program and a brief note on its time complexity."
)

# --- prompt history -------------------------------------------------------
# The task is Game of Life because it needs a real algorithmic choice -- the grid
# is unbounded, so a fixed array does not work and the model has to reach for a
# sparse representation -- while having nothing to do with primes, factorisation,
# moduli or pseudo-random generators.
#
# 8ce9dff508ea  (retired 2026-08-01, same day)  A reworded squarefree clause. It
#   did not work: Fable 5 refused 0/6. Adopted on a single passing probe, which
#   was not evidence -- the classifier is stochastic. Recorded here because the
#   runs it produced are in the data.
#
# 10471c8576af  (retired 2026-08-01)  "n is squarefree (no prime p has p^2
#   dividing n)" plus a prime digit sum. Fable 5 refused every run, category
#   "cyber": squarefree testing is factorisation. The wider lesson was that the
#   trigger is resemblance to cryptographic primitives, not number theory as
#   such -- a later candidate seeded by a linear congruential generator was
#   refused just as hard. Rather than keep rewording around a classifier, the
#   task moved to a domain with no cryptographic adjacency at all.

SHA = hashlib.sha256(TEXT.encode()).hexdigest()[:12]


def health_flag(text: str) -> str:
    """Cheap sanity signal so a latency spike can be told apart from a bad response.

    This is NOT a correctness grade -- nothing runs the code. It only answers
    "did we get a plausible answer back", which is what distinguishes a slow run
    from a degraded one.
    """
    if not text or not text.strip():
        return "empty"
    lowered = text.lower()
    if "def " not in text and "for " not in text and "import " not in text:
        return "no_code"
    if "range" not in lowered:
        return "no_code"
    return "ok"
