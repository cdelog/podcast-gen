# /// script
# requires-python = "==3.12.*"
# dependencies = ["numpy==2.5.4"]
# ///
"""Offline self-check for podcast.py (no TTS, no network): cue parsing + turn-taking placement.
Run: uv run test_podcast.py"""
import random

import numpy as np

import podcast as p

SCRIPT = """---
title: T
speakers:
  ANA: Ryan, warm and wry
  BO:
---
ANA: (excited, Skeptical) So the scan cycle [pause 0.5] reads inputs, then writes outputs--
**BO:** --right, every few milliseconds?
ANA: Exactly. (beat) It never skips a beat.
BO: (over) Mm-hmm.
ANA: (laughs) [sighs] Okay.
"""

title, lines = p.parse(SCRIPT)
a, b, c, d, e = lines
assert title == "T" and len(lines) == 5
assert (a.voice, a.style, a.gain, a.cut) == ("ryan", "warm and wry", 1, True)
assert a.instruct == f"{p.BASE_STYLE} Speaker: warm and wry. Sound excited and energetic. Delivery: Skeptical."
assert a.parts == [("say", "So the scan cycle"), ("pause", 0.5), ("say", "reads inputs, then writes outputs")]
assert b.voice == "serena", "undeclared voice gets the first free default"
assert b.latch and not b.cut and b.parts == [("say", "right, every few milliseconds?")]
assert c.parts == [("say", "Exactly. It never skips a beat.")], "inline (beat) is a silent stage direction"
assert c.instruct == p.BASE_STYLE + " Speaker: warm and wry."
assert d.over and "listener reaction" in d.instruct
assert e.parts == [("say", "Ha ha!"), ("say", "Okay.")], "unknown [sighs] dropped"

for bad in (SCRIPT + "CY: hi\n",                         # not in the declared cast
            "---\nspeakers:\n  A: en-US-AvaNeural\n---\nA: hi\n",   # not a Qwen voice
            "A: x\nB: y\nC: z\nD: four\n",               # 4th speaker
            "A: fine\nno speaker here\n"):
    try:
        p.parse(bad)
    except ValueError:
        continue
    raise AssertionError(f"expected ValueError for {bad!r}")


def tone(sec, gap_at=None):
    x = 0.1 * np.sin(2 * np.pi * 220 * np.arange(int(sec * p.SR)) / p.SR).astype(np.float32)
    if gap_at:
        x[int(gap_at * p.SR):int((gap_at + 0.25) * p.SR)] = 0
    return x


clips = [tone(3.0), tone(1.0), tone(4.0, gap_at=2.4), tone(0.4), tone(0.5)]
s = p.timeline(lines, clips, random.Random(1))
assert s[0] == 0.4
assert s[0] + 3.0 - 0.25 <= s[1] < s[0] + 3.0, "BO barges into ANA's cut-off tail"
assert 0.10 <= s[2] - (s[1] + 1.0) <= 0.28, "quick answer after a question"
assert abs(s[3] - (s[2] + 2.45)) < 1e-6, "backchannel lands in the phrase gap, under ANA"
assert s[4] >= s[2] + 4.0 + 0.35, "ANA resumes after her own turn, with a same-speaker gap"

long_bc = clips[:3] + [tone(2.0), clips[4]]              # BO's "Mm-hmm" now outlasts ANA's turn
t = p.timeline(lines, long_bc, random.Random(1))
assert 0.15 <= t[4] - (t[3] + 2.0) <= 0.40, "ANA answers the backchannel with a normal turn gap"

mix = p.mixdown(s, [c * 20 for c in clips])              # force clipping -> limiter
assert np.abs(mix).max() <= 0.95 + 1e-6 and len(mix) >= (s[4] + 0.5) * p.SR
assert p.max_seconds("Ha ha!") < 5 < p.max_seconds("one two three four five six seven eight")

# drafting: a fenced, invalid first reply gets one retry with the format error fed back
calls = []
p.chat = lambda messages, model: calls.append([*messages]) or ("not a script" if len(calls) == 1 else "```\nA: Hi.\nB: (over) Hey.\n```")
assert p.draft([str(p.HERE / "README.md")], 2, 5, None, "x/y") == "A: Hi.\nB: (over) Hey."
assert len(calls) == 2 and "fails the format check" in calls[1][-1]["content"]
assert "# Format" in calls[0][0]["content"] and "750 spoken words" in calls[0][1]["content"]
print("ok")
