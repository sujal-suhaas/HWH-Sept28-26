# DejaOps — pitch sheet

Everything here is either measured or explicitly marked as not. Numbers come from
[`docs/architecture.md`](architecture.md) §10.7 and `data/store/learning-eval.jsonl`.

**Do not read a number off this page without the caveat next to it.** The caveats are not hedging —
they are what makes the rest of the numbers believable.

---

## The one-liner

> DejaOps is an on-call incident agent that remembers every incident it has ever handled — and gets
> measurably better at the next one because an operator confirmed the last one.

---

## 1. The hook (30 seconds, say it before touching the laptop)

> It's 3am. A pager goes off: *"checkout is slow."*
>
> An engineer who has been here three years says *"that's the Kafka thing again — scale the consumer
> group, replay the partition."* Ten minutes.
>
> A new engineer stares at the same alert. The runbook is eight months stale. Nobody is awake. Forty
> minutes gone — solving something someone already solved three weeks ago.
>
> The veteran is not smarter. They remember. **We gave the system that memory — and we made it
> provable rather than plausible.**

Pause there. Let them look at the screen.

---

## 2. The problem, stated properly (20 seconds)

On-call engineers lose the first 30–45 minutes of every incident rediscovering a solution the
organisation already had. It lives in one person's head, in a stale runbook, or in a Slack thread
nobody can find.

The knowledge exists. It just is not **retrievable at the moment it is needed**.

---

## 3. What we built (20 seconds)

A console for on-call engineers at a fictional payments company, NimbusPay.

You give it an alert. It recalls similar past incidents, prior root causes and validated fixes from a
persistent memory layer, and proposes a grounded diagnosis — **showing the exact memory IDs it used
as evidence.**

Then a human confirms or corrects it, and that confirmation becomes memory the next incident can
recall.

**Memory is not a feature here. It is the product.** The demo's whole point is a live A/B that makes
the effect of memory visible instead of asserted.

---

## 4. The live demo

### Before you start

```bash
set -a && . ./.env && set +a
uv run uvicorn src.api.app:app --host 127.0.0.1 --port 8000
# in frontend/: npx vite --host 127.0.0.1 --port 5173 --strictPort
```

Open `http://127.0.0.1:5173`. Check the header badge is green `live api` — if it says `mock`, you are
on fixtures and must not present it as a live run.

### Beat 1 — memory OFF first (2 min)

Pick `P1 · checkout-api · checkout-api p99 latency above 2s`. Select **memory off**. Click **Open
incident**.

While it runs, say:

> Same alert. Same model. Same prompt. The only difference from what you're about to see is that
> Hindsight is not called at all.

Result: confidence **low**, **0** memories cited, **no** proposed fix. The Memory Inspector says
*"Hindsight was not called for this run… this is not a failed recall."*

**Point at that sentence.** It is doing real work: *"no precedent exists"* and *"I could not check"*
are different claims, and most systems blur them.

### Beat 2 — memory ON, same alert (2 min)

Click **Re-run with memory on**. It replays the identical alert.

Result: confidence **high**, **2** memories cited with real IDs, a proposed runbook.

> This is the comparison. One click, same question both times.

### Beat 3 — the operator boundary (1 min)

In the feedback form choose `RESOLUTION_CONFIRMED` and **select the runbook** from the dropdown.
Submit.

> Only this writes durable memory. The AI can propose a diagnosis. It cannot declare an incident
> solved.

### Beat 4 — the incident that has never happened (2 min)

Pick `P1 · webhook-dispatcher · webhook signature verification failing for in-flight deliveries`.
Memory on. Run it.

> Six prior webhook incidents exist in memory. All six are retry storms. This one is not.

The agent says it found nothing applicable and proposes nothing. **That is the correct answer**, and
it is the one a pattern-matching system gets wrong.

Now teach it, in the UI:
1. **operator correction** — cause: *signing key rotated with no dual-key overlap window*
2. **resolution confirmed** — and **select `RB-051`** in the runbook dropdown

> Selecting the runbook is the step that matters. The correction text is free text and carries no
> cause ID. The runbook does. Confirming it is what promotes the fix to durable memory.

Re-run the identical alert. **Measured, on the shipped model:**

| | before teach | after teach |
| --- | --- | --- |
| named root cause | `None` | **`RC-009`** |
| Root Cause Hit@1 | `False` | **`True`** |
| Validated Fix Hit@1 | `False` | **`True`** |
| grounded response | `False` | **`True`** |
| memories cited | 0 | 2 |

> Nothing was retrained. A human confirmed an outcome, and that confirmation became memory the next
> incident could use. **That is the entire thesis.**

If it does *not* name `RC-009` on the day, **say so and move on**. Do not assert the table. The
failure to check for is the agent citing the fix correctly and never naming the cause — see
§10.8.

---

## 5. The evidence, with its scope attached

**The learning curve.** One pattern (`RC-007`, fraud-scorer), cutoffs 2–5, on the shipped primary
model, all four runs completed.

| history seen | Root Cause Hit@1 | Validated Fix Hit@1 |
| --- | --- | --- |
| 1 prior occurrence | 0.00 | 0.00 |
| 2 prior occurrences | **1.00** | 0.00 |
| 3 prior occurrences | 1.00 | **1.00** |
| 4 prior occurrences | 1.00 | 1.00 |

**Say the caveat in the same breath as the number:** *n = 1* per cutoff, one pattern out of six. It
illustrates the mechanism — later occurrences become diagnosable because confirmed outcomes
accumulated. It is not a rate. No target was set in advance.

**The seeded estate.** 145 durable memories built from 52 incidents across 8 weeks: 52 alert
snapshots, 52 diagnoses, 26 confirmed resolutions, 8 postmortems, 7 promoted runbooks. 7 services,
9 root causes, 9 runbooks.

**Engineering.** 377 backend tests, 62 frontend tests, a Playwright happy path, four CI jobs green.
No secrets in the tree.

---

## 6. Why this is technically hard (the part judges probe)

**1. Hindsight is behind exactly one file.** `src/memory/hindsight_client.py` is the only module that
imports the SDK. Everything else depends on our own interface. That is what makes it possible to
switch memory off, swap deployments, or fake it in tests — and it is why the A/B is *causally*
meaningful rather than a UI toggle that changes nothing.

**2. Tags scope, metadata travels.** Recall is filtered by **tags** (`service:checkout-api`,
`incident_type:latency`, `event_type:resolution`); **metadata** (`incident_id`, `runbook_id`,
`source`, `timestamp`) is context returned *with* a memory and is **never** used as a filter. Getting
these confused is the most common way to build a memory system that looks right and retrieves wrong.

**3. Relevance thresholds are calibrated, not guessed.** Hindsight returns low-relevance neighbours
rather than an empty list. We measured relevant matches at 0.353–1.087 and irrelevant at 0.000–0.089,
and set the cutoff at 0.2 — so *"no relevant memory found"* is an honest statement rather than a
near-miss dressed up as evidence. For an exact tag scope the threshold is disabled, because when the
scope *is* the relevance signal a similarity floor only discards true matches.

**4. The grounding guard.** Model output is untrusted input. Every tool call is validated by name,
shape, argument types and enum values before execution. **A cited memory ID must have been recalled
in that run** — a fabricated citation is rejected and repaired once, then the run fails honestly.

**5. Model fallback that does not lie.** Retryable failures back off and fall back. Permanent ones
fail clearly. A permanent primary failure does **not** silently swap models, because a silent swap
would change what the demo actually ran on.

**6. Retry, then degrade honestly.** Transient Hindsight failures retry with bounded backoff. After
exhaustion the run continues in degraded mode and says so. A failed authoritative retain is never
reported as a success.

---

## 7. What we deliberately did not build

No Slack integration. No PagerDuty. No auth or multi-tenancy. No Kubernetes. No multi-agent
orchestration. No general-purpose knowledge base.

Each is a real post-v1 path — the workflow is designed to slot into an existing paging system — and
each would have cost us the thing that makes this project defensible: **depth on memory.**

---

## 8. Limitations, stated before anyone asks

- **The full curve has not been run.** 26 evaluated incidents at ~20k tokens each against a
  200k daily free-tier limit. What is recorded is one complete pattern ladder and one complete teach
  replay.
- **The fallback model is unreliable.** `qwen/qwen3.8-27b` intermittently emits its own XML tool
  syntax, which the provider rejects. Measured: 2 of 4 runs completed. It also rejects our request
  size on the free tier outright. Every number above is from the **primary** model, so this does not
  qualify them — but the fallback is not currently a working fallback.
- **Screenshots in the README are outstanding.** Captures taken against the test harness with a fake
  model were deleted rather than shipped. A README image of a fake model is a claim, not a
  placeholder.
- **The dataset is synthetic.** 52 incidents for a fictional company. Realistic and internally
  consistent, but not production data.

---

## 9. Judge Q&A

**"Isn't this just RAG over a vector store?"**
No, and the difference is the write path. A retrieval system reads a corpus someone else maintains.
Here the corpus is *created by the incident workflow itself*. The last demo step is the agent getting
better at an incident that had no precedent — which RAG over a static corpus cannot do.

**"What stops the model poisoning memory with a hallucinated fix?"**
The confirmation boundary. The model has two proposal tools and they write only incident timeline
state — `proposed` and `pending_confirmation`. Only an explicit operator outcome creates
authoritative memory. There is a test asserting the retain count does not move when a proposal is
made.

**"How do you know the memory is real and not the model making it up?"**
Two ways. Recall is scoped by tags, so it can only return memories matching the service and incident
type. And the grounding guard rejects any citation the model was not shown in that run — verified by
a test that feeds it an invented memory ID.

**"What happens when Hindsight is down?"**
Bounded retries, then degrade. The run continues and the Inspector shows `memory service
unavailable`, which is deliberately distinct from `no relevant memory found`. The agent is told to
treat it as *no evidence*, not *no precedent*. A failed retain is never reported as successful.

**"Did you actually measure the improvement, or is that a claim?"**
Measured, and the caveats are in the doc. Teach→replay gain of +1 on Root Cause Hit@1 on the shipped
model; a four-point curve on one pattern. `n = 1` per cutoff. We did not set a target in advance and
we are not reporting one.

**"What was the hardest bug?"**
A cause ID was reaching the `lookup_runbook` path but not the `recall_similar_incidents` path, so the
agent could retrieve the right fix and still be unable to name the cause. Root Cause Hit@1 never
moved and **nothing reported an error** — the test suite was green because the test covered the path
that worked. We found it by running the thing and reading the record. Both runs are still in the log,
one line apart. That is the failure mode this project is most exposed to: a rule that holds on one
path and quietly not on another.

**"What would you do with more time?"**
Run the full six-pattern curve. Fix the fallback's tool loop, or drop it and document that the
primary is required. Wire it into real PagerDuty — the memory layer does not change, only the input.

---

## 10. The close

> Every on-call team already has this memory. It lives in a veteran engineer's head, and it walks out
> the door when they change jobs.
>
> DejaOps makes that memory **durable, retrievable at 3am, and provably useful** — and it keeps the
> human in charge of what counts as true.
>
> The system did not get better because we retrained anything. It got better because a confirmed
> outcome became memory. **That is a product you can build on.**

---

## Timing cards

**3 minutes** — hook (30s) · problem (15s) · memory OFF then ON on the same alert (60s) · operator
confirmation (15s) · teach→replay (45s) · close (15s)

**5 minutes** — everything above, plus: the novel incident's honest "no precedent" answer, the
Memory Inspector walkthrough, and the measured curve with its caveat.

**If the network or quota fails:** switch to `?source=mock`, say clearly that you are on fixtures,
and present the recorded numbers from §5 — which are real either way. **Never present a mock run as
a live one.**

---

## Links

- Hindsight: https://github.com/vectorize-io/hindsight
- Hindsight docs: https://docs.hindsight.vectorize.io/
- Vectorize on agent memory: https://vectorize.io/what-is-agent-memory
