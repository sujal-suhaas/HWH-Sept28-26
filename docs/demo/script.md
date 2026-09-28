# DejaOps demo script

Two runs of the same alert, one with memory and one without, then one incident the
system has never seen. Roughly 90 seconds if nobody interrupts; the 60-second version
is steps 2 through 5.

Everything below is runnable from a clean clone. Where a number appears, it was
produced by the command next to it — see [Measured, not asserted](#measured-not-asserted).

---

## Before you start

```bash
# backend, memory ON, seeded bank
set -a && . ./.env && set +a
uv run python scripts/seed_memory.py --reset        # 145 events into dejaops-prod
uv run uvicorn src.api.app:app --port 8000

# frontend
cd frontend && npm run dev -- --host 127.0.0.1 --port 5173
```

Open <http://127.0.0.1:5173>. The header shows the data source. If it says `mock`, you
are on fixtures and the demo is not real — drop the `?source=mock` query parameter.

**One thing to check before an audience arrives:** `/health` reports the memory mode and
the bank. If `memory_mode` is `off`, or the bank is empty, the memory-on run will look
exactly like the memory-off run and the whole demo collapses. `scripts/seed_memory.py
--dry-run` prints what would be retained without calling Hindsight.

---

## 1. The problem, in one sentence

> On-call engineers spend the first 30–45 minutes of an incident rediscovering what
> someone already solved, because the knowledge is in a postmortem nobody read and a
> runbook that went stale.

## 2. A known pattern, with memory ON

Leave **Memory mode: on**. Pick `P1 · checkout-api · checkout-api p99 latency above 2s`
and press **Open incident with memory on**.

While it runs (~15s, and it is calling a real model), say:

> The agent is not answering from the prompt. It recalled prior incidents, looked up a
> validated runbook, and every memory id it cites came back from a tool in this run.

What appears:

- **Proposed diagnosis** — `proposed`, `confidence: high`, naming payments-ledger
  consumer saturation, with a **cited memory** chip
- **Proposed resolution** — `pending confirmation`, tagged `RB-014`
- **Timeline** — `memory_retained`, then `recall_similar_incidents`, then `lookup_runbook`
- **Memory inspector** — one row per Hindsight call: operation, hit count, latency,
  attempts, the query, the tag scope, and `min_score`

**The single most important sentence in the demo**, pointing at the proposal card:

> This is a proposal. Nothing here is memory yet. The agent does not get to declare an
> incident resolved.

## 3. The confirmation boundary

In **Operator outcome**, the diagnosis is already selected and the confirmed root cause
is **already filled in** — `RC-001`, because the agent proposed `RB-014` and the catalog
knows which cause that runbook treats. Press **Record diagnosis confirmed**.

Point at the card: the badge changes from `proposed` to `confirmed by operator`, and the
footer changes to *"Only that outcome — not this proposal — became memory."*

Then select **resolution confirmed** and record it. The incident state becomes
`resolved` and the runbook is recorded.

> Three memories were just written: the confirmed diagnosis, the resolution, and the
> runbook promoted from it. Those exist because an operator confirmed them, not because
> a model said so.

## 4. The same alert, memory OFF

Press **Re-run with memory off**.

Same alert. Same models. Same system prompt. The only thing that changed is that
Hindsight is not called — and the UI says so, on the incident (`memory off`) and in the
inspector (*"Hindsight was not called for this run… this is not a failed recall"*).

| | memory ON | memory OFF |
| --- | --- | --- |
| diagnosis confidence | `high` | `low` |
| memories cited | 2 | 0 |
| proposed resolution | `RB-014` | none |
| memory traces | 8 | 0 |
| inspector | recalled / retained | *Hindsight was not called* |

> This is the comparison that makes memory the product rather than a feature. It is the
> same question both times.

## 5. The incident nobody has seen

Pick `P1 · webhook-dispatcher · webhook signature verification failing for in-flight
deliveries` and open it with memory on.

> NimbusPay has six prior webhook incidents. All six are retry storms. This one is not,
> and the agent should say so rather than pattern-match to the nearest neighbour.

The agent recalls real webhook history — six prior incidents, all retry storms — and then has
to decide whether that precedent actually applies. It usually does not claim a retry storm for a
signature error, and it reports `confidence: low` with no resolution. If it *does* reach for the
retry-storm runbook, that is the more instructive outcome, not a broken demo: it is a
plausible-but-wrong match, and the next step is why the operator override exists.

Either way, there is no memory about signature verification to recall — that is the invariant,
and it is enforced by a test rather than hoped for.

Then teach it, in the UI:

1. **operator correction** — corrected cause: *signing key rotated with no dual-key overlap
   window*; validated fix: *restore the previous key alongside the new key, then drain the queue*
2. **resolution confirmed** — and **select `RB-051`** in the runbook dropdown

Selecting `RB-051` is the step that matters and it is easy to skip. The correction text is free
text, so it carries no cause id; the runbook does. Confirming a resolution with `RB-051` is what
promotes it to a `RUNBOOK_ENTRY` memory, and a runbook hit is annotated with the cause it treats.
That is how the cause id becomes retrievable, and it is why the runbook is in the catalog
(`RC-009`/`RB-051`) while being absent from memory.

> The operator just wrote the first memory about this failure mode. Watch what changes.

Re-run the identical alert. This is the measured result, not a hope — on the shipped primary
model, the same alert, same prompt, same bank lifecycle:

| | before teach | after teach |
| --- | --- | --- |
| named root cause | `None` | **`RC-009`** |
| Root Cause Hit@1 | `False` | **`True`** |
| Validated Fix Hit@1 | `False` | **`True`** |
| grounded response | `False` | **`True`** |
| cited memories | 0 | 2 |

> That is the whole thesis. The system did not get better because we retrained anything. It got
> better because a confirmed outcome became durable memory that the next incident can recall.

If it does **not** name `RC-009`, that is still the honest demo — say so and move on rather than
asserting the table. The failure to check for is the one that hid here for a while: the agent
retrieving the fix, citing it correctly, and never naming the cause. If you see that, the tool
result is missing `root_cause_id=` on the recalled hit; `docs/architecture.md` §10.8 explains it.

---

## Measured, not asserted

These came from actual runs. Reproduce any of them with the command shown.

| Claim | Evidence | Command |
| --- | --- | --- |
| 145 durable events across 52 incidents | `retained=145 skipped=0 failed=0` | `uv run python scripts/seed_memory.py --dry-run` |
| Memory ON cites real memories; OFF cites none | ON: confidence `high`, 2 cited, `RB-014`, 8 traces. OFF: `low`, 0 cited, no resolution, 0 traces | the two runs above |
| An operator confirmation is what writes memory | three retains landed on confirm (`DIAGNOSIS`, `RESOLUTION`, `RUNBOOK_ENTRY`), and `INC-9001:resolution` and `RUNBOOK-RB-014:checkout-api:runbook_entry` read back out of the bank afterwards | `uv run python scripts/seed_memory.py` then the feedback flow |
| **Teach → replay works on the demo incident** | Root Cause Hit@1 `False → True`, gain **+1**; named `RC-009`; 2 memories cited | `uv run python scripts/evaluate_learning.py --mode teach` |
| **The curve rises with confirmed history** | RC-007 ladder, cutoffs 2–5: cause@1 `0.00 → 1.00 → 1.00 → 1.00`, fix@1 `0.00 → 0.00 → 1.00 → 1.00` | `uv run python scripts/evaluate_learning.py --mode curve --limit 4` |
| The agent can name a root cause | named `RC-007` and `RC-009` correctly in live runs | `docs/architecture.md` §10.7 |
| The demo's ground truth is catalogued but unseeded | `RC-009` and `RB-051` are in the catalog; 0 memories reference either, at every history cutoff including the full bank | `uv run pytest tests/data/test_demo_scenario.py -v` |
| Both runs used the shipped primary model | `fallback_runs: 0`, `models_used: ["openai/gpt-oss-120b"]` | the `--json` output of either command |

**The caveats, which belong in the same breath as the numbers:**

- **The curve is `n = 1` per cutoff, one pattern of six.** Four runs, each a different incident,
  so the steps between cutoffs are not a controlled comparison. It illustrates the mechanism —
  later occurrences become diagnosable because confirmed outcomes accumulated. It is not a rate.
  No target was set in advance and none is reported.
- **A full six-pattern curve has not been run.** 26 incidents at ~20k tokens each against a 200k
  daily free-tier limit; `docs/architecture.md` §10.5. Do not present the ladder above as "the
  learning curve" without saying it is one pattern.
- **No latency or accuracy improvement figure exists** beyond what is in these tables.

**Not measured at all:** screenshots in the README come from a live run and are still outstanding. The
captures that exist were taken against the E2E harness with a fake model, and putting those in a
README would misrepresent the product, so they were deleted rather than shipped. A live capture was
attempted and both runs ended in `AllModelsFailedError` — the primary had 1,844 of its 200,000 daily
tokens left, and the fallback rejects the request size outright (`OTPM: Limit 1000, Requested 1502`,
issue #25). Re-run the capture once quota allows; do not substitute harness images for live ones.

---

## Judge Q&A

**"Is this just RAG over a vector store?"**
No, and the difference is the write path. A retrieval system reads a corpus somebody else
maintains. Here the corpus is created by the incident workflow itself: an operator
confirms an outcome, and that outcome becomes retrievable memory for the next incident.
The demo's last step is the agent getting better at an incident that had no precedent —
which RAG over a static corpus cannot do.

**"What stops the model poisoning memory with a hallucinated resolution?"**
The confirmation boundary. The model has two proposal tools and they write only incident
timeline state — `proposed` and `pending_confirmation`. The only thing that creates
authoritative memory is an explicit operator outcome. There is a test that asserts the
retain count does not move when a proposal is made.

**"What if Hindsight is down?"**
The adapter retries transient failures with bounded backoff, then degrades. A degraded
recall is reported as `memory service unavailable` and is deliberately distinct from
`no relevant memory found` — the agent is told to treat it as *no evidence* rather than
*no precedent*, and the inspector shows the degraded state. A failed authoritative retain
is never silently converted into a success.

**"Why should I believe the ON/OFF comparison is fair?"**
Because it is the same alert, the same model IDs, and the same system prompt, and the
only difference is whether the memory tools return anything. Memory OFF does not read
memory-derived runbooks, does not include recalled ids in the context, and labels the run
`memory_mode=off`. The prompt is byte-identical between the two.

**"How do you know it got better, rather than just sounding better?"**
`scripts/evaluate_learning.py`. Root Cause Hit@1 and Validated Fix Hit@1 are scored
against hidden labels in `data/seed/labels.json` that the agent never sees. The
evaluation seeds a bank with only the history that existed *before* the incident being
evaluated, so it provably cannot recall the answer it is being asked for. Grounded
Response Rate does not take the model's word for a citation — it reads the memory's
provenance back out of the tool results and checks it.

**"What is the most honest limitation?"**
The fallback model. `qwen/qwen3.8-27b` intermittently emits its own XML tool syntax, which
the provider rejects; measured, it completed 2 of 4 evaluation runs. We have not papered
over it — it is `docs/architecture.md` §10.6 and a `post-v1` issue, and the error
classification is unchanged because a candidate fix has not been measured. A fallback run
that cannot complete reports `model_failed` rather than producing a worse answer.

**"What would you do with more time?"**
Slack and PagerDuty ingestion (both explicitly out of scope for v1), a cause with more
than one validated runbook so the two Hit@1 metrics can diverge more often, and enough
model quota to finish the curve.

---

## If something goes wrong on stage

- **Nothing recalls anything.** The bank is empty or `memory_mode` is off. Check `/health`.
  Re-seed with `--reset`.
- **Every run ends in an error.** Check `/health` for the model IDs, and the backend log.
  Provider rate limits are the usual cause, and they look like `model_failed` in the
  timeline rather than a wrong answer.
- **The UI shows `mock` in the header.** Drop `?source=mock`. Fixtures are for tests.
- **The re-run button does nothing.** The incident must be open first; re-run replays the
  *current* incident's alert in the other memory mode.
