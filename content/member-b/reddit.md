# Reddit link post — Member B

Link post to the article. One community, subject to its current rules.

**Title**

```
I wrote 52 fake incidents so my agent would have something worth remembering
```

**Body**

```
A memory demo is easy to fake. Seed an incident, ask about that incident, watch the agent remember
it. That proves storage — the answer was in the history because you put it there.

To show memory changes an answer, the history has to be one where the answer is genuinely not in the
alert. So I generated 52 incidents across 8 weeks, 7 services, 9 root causes, 9 runbooks, with a
per-pattern occurrence counter:

    nth == 1  ->  outcome = "inconclusive"   runbook_id = None
    nth == 2  ->  outcome = "rejected"       runbook_id = None
    nth >= 3  ->  outcome = "confirmed"      runbook_id = pattern["runbook_id"]

First time a pattern appears nobody knows what it was. Second time someone has a theory that doesn't
hold. From the third there's a confirmed cause and a promoted runbook. The alert text never contains
the cause — it describes symptoms. The cause lives in the diagnosis and resolution events, which is
where memory is supposed to help.

The evaluation also carries hidden labels in a separate file, so the expected answer never reaches
the prompt.

Then my first evaluation leaked. Scoring the fix meant looking the runbook up in memory — where the
evaluated incident's own resolution was sitting. The answer key was in the box the agent searched,
so every run scored well and the measurement was circular. Fix: only memories that existed *before*
the incident being evaluated, `RUNBOOK_ENTRY` events only, never a resolution or correction. There's
now a test asserting that invariant directly.

Teach-then-replay on an incident absent from history: Root Cause Hit@1 false -> true after one
operator confirmation.

Honest limits: 52 incidents is small enough to reason about by hand and far too small to say
anything about scale. The labels are only as good as the generator — hidden labels stop the agent
seeing the answer, they don't make the answer true.

Full write-up: {{ARTICLE_URL}}

Hindsight is the memory layer: https://github.com/vectorize-io/hindsight
```

**Flair / notes**

- Link post, so the article URL is the submission URL; the body above is the self-text.
- If the community only accepts self-posts, submit the body above with the link in the first line.
