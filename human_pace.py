#!/usr/bin/env python3
"""Human pacing: log-normal think/action delays + micro-jitter.

Design notes (why log-normal, not uniform):
  Real humans have a right-skewed delay distribution — mostly fast, sometimes
  very slow. uniform(a,b) looks synthetic to behavioural scoring; log-normal
  matches observed human inter-action timings.

Usage:
    from human_pace import pace, jitter_wait, typo_walk
    pace("think")            # 0.9-4.5s skewed
    pace("read_form")        # 1.5-7s
    jitter_wait(2.0)         # 2.0s +/- 35% jitter
    typo_walk(el, "text")    # type with per-char jitter + rare typo+backspace
"""
import math
import random
import time

_rng = random.Random()

# profile: (median_seconds, sigma) — log-normal shape
PROFILES = {
    "micro":      (0.20, 0.35),   # between keystrokes/clicks in one motion
    "think":      (1.30, 0.60),   # pause before deciding
    "read_form":  (3.00, 0.75),   # scan a page before acting
    "page_load":  (2.20, 0.55),   # wait for navigation settle
    "post_submit": (4.50, 0.65),  # after a submit, before next probe
    "bored":      (11.0, 0.80),   # occasional "got distracted" pause
}


def _lognorm(median, sigma):
    # sample log-normal with given median
    return median * math.exp(_rng.gauss(0, sigma))


def delay(kind="think", cap=None, footgun_pause=True):
    """Return a human-ish delay in seconds for the given activity kind."""
    median, sigma = PROFILES.get(kind, PROFILES["think"])
    d = _lognorm(median, sigma)
    # 6% chance of a "bored / distracted" spike (2-4x) — real sessions have these
    if footgun_pause and _rng.random() < 0.06:
        d *= _rng.uniform(2.0, 4.0)
    if cap:
        d = min(d, cap)
    return max(0.05, d)


def pace(kind="think", cap=None):
    """Sleep for a human-ish duration. Returns the seconds slept."""
    d = delay(kind, cap=cap)
    time.sleep(d)
    return d


def jitter_wait(base, pct=0.35):
    """Base sleep with +/- pct multiplicative jitter (min 40ms)."""
    d = base * _rng.uniform(1.0 - pct, 1.0 + pct)
    d = max(0.04, d)
    time.sleep(d)
    return d


def typo_walk(el, text, wpm=None):
    """Type text into DrissionPage element with per-char jitter.

    WPM 35-75 (human range). ~4% chance per char of a mistype that gets
    backspaced, then retyped — a strong humanness signal.
    """
    if wpm is None:
        wpm = _rng.uniform(35, 75)
    per_char = 60.0 / (wpm * 5.0)  # avg chars per word = 5
    for ch in text:
        if _rng.random() < 0.04:
            wrong = _rng.choice("abcdefghijklmnopqrstuvwxyz")
            el.input(wrong)
            time.sleep(_rng.uniform(0.08, 0.22))
            el.input("\b")          # backspace
            time.sleep(_rng.uniform(0.05, 0.15))
        el.input(ch)
        time.sleep(max(0.012, _rng.gauss(per_char, per_char * 0.45)))
    return per_char


if __name__ == "__main__":
    print("sample think delays:", [round(delay("think"), 2) for _ in range(12)])
    print("sample post_submit :", [round(delay("post_submit"), 2) for _ in range(6)])
    print("sample micro       :", [round(delay("micro"), 2) for _ in range(8)])
