# Reddit — Member A

Self-post body in Reddit markdown. If the sub allows link posts, submit the article URL as
the post and use this body as the first comment.

**Title**

```
A missing function argument made our incident agent cite the right runbook but never name the cause
```

**Body**

---

We built an on-call incident agent that recalls similar past incidents and validated runbooks from
[Hindsight](https://github.com/vectorize-io/hindsight) before proposing a diagnosis. Then we measured
whether remembering actually helped, and **Root Cause Hit@1 sat at 0.00** while every test passed and
nothing logged an error.

The bug was a helper that annotates recalled runbook memories with the root cause each runbook
treats. To do that it needs the runbook catalog. The parameter was optional:

```python
def _render_hits(hits: list[RecallHit], catalog: Catalog | None = None) -> str:
```

`lookup_runbook` passed it. `recall_similar_incidents` — the path that runs on **every** incident,
before anything else — called `_render_hits(hits)`. So the agent retrieved the right fix, cited it
correctly, and had no cause to name, because it was never given one. No exception: skipping the
annotation was a legitimate branch.

### What actually found it

A second metric. Validated Fix Hit@1 was climbing normally while Root Cause Hit@1 stayed flat.

A model that can't reason fails both. A model holding the fix and no cause was never told the cause.
That asymmetry was the whole diagnosis — we'd spent a while tuning prompts before noticing it.

### The fix

```python
def _render_hits(hits: list[RecallHit], catalog: Catalog) -> str:
```

and deleting the `if catalog is not None` guard, so the omission becomes a `TypeError` at the first
call instead of a silently thinner prompt.

The default value **was** the bug. `Catalog | None = None` meant "there is a legitimate way to call
this without a catalog", and there wasn't. There was one caller that forgot.

### Before / after

Teach-then-replay, on an incident deliberately absent from the seeded history:

```
before teach   named root cause: none      Hit@1: false   cited: 0
after teach    named root cause: correct   Hit@1: true    cited: 2
```

Both replays are in the log, one line apart. The only difference between them is that one function
signature.

### Honest limits

- The evaluation is on **synthetic** incident history we generated. It measures whether the agent can
  recover a planted pattern, not whether it helps on real incidents.
- One pattern, `n = 1` per cutoff. It's a ladder, not a rate. Averaging the rows would be a
  meaningless number dressed up as a benchmark.
- No memory expiry. An operator who confirms a wrong cause in a hurry creates a wrong precedent that
  stays.

Full write-up with the code and the measured table:
https://medium.com/@sujalsuhaas2007/our-hindsight-agent-cited-the-fix-but-never-named-the-cause-252c67697d06

Code: https://github.com/sujal-suhaas/HWH-Sept28-26

---
