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
    "Let S be the set of integers n with 1 <= n <= 8000000 such that BOTH of the "
    "following hold:\n"
    "  (a) n is not divisible by any perfect square greater than 1, and\n"
    "  (b) the sum of the digits of n^3, when n^3 is written in base 9, is a prime "
    "number.\n\n"
    "Write a single self-contained Python 3 program that computes |S| and prints it. "
    "Use only the standard library, and make it efficient enough to finish in well "
    "under a minute. Reply with the program and a brief note on its time complexity."
)

# --- prompt history -------------------------------------------------------
# 10471c8576af  (retired 2026-08-01)  clause (a) read "n is squarefree (no prime p
#   has p^2 dividing n)". Claude Fable 5 refused it on every run with
#   stop_reason=refusal, category "cyber" -- explicit prime-divisibility reads as
#   factorisation. Probing isolated the trigger: keeping "is a prime number" in
#   clause (b) passes, and only the "no prime p has p^2 dividing n" phrasing fails.
#   Clause (a) was reworded to the equivalent "not divisible by any perfect square
#   greater than 1". The set S, the answer, and the algorithm are unchanged; only
#   the wording differs. Fable 5 now solves it (30s, 2279 output tokens).
#   Runs before that date carry the old hash and are a separate series.

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
