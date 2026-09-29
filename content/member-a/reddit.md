# Reddit link post — Member A

Link post to the article. One community, subject to its current rules.

**Title**

```
A missing function argument made our incident agent cite the right runbook but never name the cause
```

**Body**

```
We built an on-call incident agent that recalls similar past incidents and validated runbooks from
Hindsight before proposing a diagnosis. Then we measured whether remembering helped, and Root Cause
Hit@1 sat at 0.00 while every test passed and nothing logged an error.

The bug was a helper that annotates recalled runbook memories with the root cause each runbook
treats. To do that it needs the runbook catalog. The parameter was optional:

    def _render_hits(hits, catalog: Catalog | None = None) -> str:

`lookup_runbook` passed it. `recall_similar_incidents` — the path that runs on every incident,
before anything else — called `_render_hits(hits)`. So the agent retrieved the right fix, cited it
correctly, and had no cause to name, because it was never given one. No exception: skipping the
annotation was a legitimate branch.

What actually found it was a second metric. Validated Fix Hit@1 was climbing normally while Root
Cause Hit@1 stayed flat. A model that can't reason fails both; a model holding the fix and no cause
was never told the cause.

Fix was making the argument required and deleting the None path, so the omission is a TypeError at
the first call instead of a silently thinner prompt.

Before/after on a teach-then-replay case, incident deliberately absent from history:

    before teach   named root cause: none      Hit@1: false   cited: 0
    after teach    named root cause: correct   Hit@1: true    cited: 2

Both replays are in the log one line apart. The only difference between them is that one function
signature.

Honest limits: the evaluation is on synthetic incident history we generated, one pattern, n=1 per
cutoff — a ladder, not a rate. There's no memory expiry, so an operator who confirms a wrong cause
in a hurry creates a wrong precedent that stays.

Full write-up with the code and the measured table: {{ARTICLE_URL}}

Hindsight is the memory layer: https://github.com/vectorize-io/hindsight
```

**Flair / notes**

- Link post, so the article URL is the submission URL; the body above is the self-text.
- If the community only accepts self-posts, submit the body above with the link in the first line.
