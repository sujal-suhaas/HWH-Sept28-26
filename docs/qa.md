# Judge Q&A — architecture and edge cases

Answers state what the code does. Where something is unmeasured or unimplemented, the answer says so.

---

## A. Memory architecture

**Why is Hindsight a separate service rather than a table in your database?**
Because the operations are different in kind. SQLite answers "what is the state of incident INC-9001" — exact lookup, transactional, needs to be right. Memory answers "what have we seen that resembles this" — semantic, fuzzy, ranked, and it must be able to say *nothing matches*. Building the second on the first means writing a similarity search, a relevance threshold and a scoping layer by hand. Hindsight is that layer.

**How much of Hindsight do you actually use?**
Four operations: create a bank, retain, recall, and health. We deliberately use a small surface. We do not use Hindsight's entity extraction, its graph, its attachments or its async retain — those exist, and we are not claiming them.

**Is Hindsight actually required, or could you swap it?**
It is required by the brief, and the architecture treats it that way: it is behind one file. `src/memory/hindsight_client.py` is the only module in the repo that imports the SDK — verified by grep across `src/`, `scripts/` and `tests/`. Everything else depends on our own `MemoryStore` interface. That boundary is what makes memory-OFF, a provider swap, or a fake store in tests possible without touching agent code.

**What is a memory bank and why do you have one?**
Hindsight's isolation unit. Ours is `dejaops-prod`. One bank for the workspace means recall can only ever return memories from this deployment, which is a correctness property, not a preference.

**Walk me through what gets written to memory.**
Six event types, and only from lifecycle events: `INCIDENT_OPEN` (automatic, on every alert), `DIAGNOSIS` (on operator outcome), `RESOLUTION` (only on explicit confirmation), `POSTMORTEM` (after the fact), `RUNBOOK_ENTRY` (promoted from a confirmed resolution), `OPERATOR_CORRECTION` (operator says the agent was wrong). Ordinary chat turns are not retained. That is deliberate — retaining everything makes recall worse, not better.

**Why are tags and metadata different things here?**
Because they do different jobs. **Tags scope the search** — `service:checkout-api`, `incident_type:latency`, `event_type:resolution`. **Metadata travels with a result** — `incident_id`, `runbook_id`, `source`, `timestamp`. Metadata is never used as a filter. Using metadata as a recall filter is the most common way to build a memory system that looks right and retrieves wrong, so we made the distinction structural rather than conventional.

**How does recall decide what is relevant?**
Two things. First, scope: recall is filtered by tags, so a checkout latency alert only searches checkout latency memories. Second, a similarity floor. We measured the separation on the seeded bank — relevant matches scored 0.353–1.087, irrelevant neighbours 0.000–0.089 — and set the threshold at 0.2, inside the gap. That matters because Hindsight returns low-relevance neighbours rather than an empty list, so without a floor, "no relevant memory found" would be a near-miss dressed up as evidence.

**What happens when the tag scope is already exact?**
The similarity floor is disabled for that recall. If you are asking for exactly one runbook by its tag, the scope *is* the relevance signal, and a similarity threshold only discards true matches. That was a real bug we hit: a runbook scored 0.0 against an exact tag scope and got filtered out.

**Why two recall calls instead of one?**
Because one broad call ranks wrong. With a single service-scoped recall, near-duplicate `INCIDENT_OPEN` memories outrank the resolutions and postmortems that actually carry an outcome — so the useful memory never reaches the model. We split it: a narrative recall scoped to `event_type:incident_open`, and an outcome recall scoped to `event_type:resolution`. Narrative matches narrative; outcome matches outcome.

**Hindsight returns each memory twice. Why, and what do you do?**
It returns the retained document (`type="world"`, with metadata and a document ID) and its own derived paraphrase (`type="observation"`, no metadata). Both are the same memory. We dedupe on a stable identity — incident ID plus event type, or runbook ID for runbook entries — and keep the copy with better provenance, which means the one with metadata. Without that, the model sees the same memory twice and cites it as two pieces of evidence.

---

## B. The confirmation boundary

**What stops the model writing a hallucinated fix into memory?**
The model has two proposal tools and neither writes memory. `propose_diagnosis` and `propose_resolution` write only incident timeline state — `proposed` and `pending_confirmation`. The only thing that creates authoritative memory is an explicit operator outcome through `POST /incidents/{id}/feedback`. There is a test asserting the retain count does not move when a proposal is made.

**Why is that boundary necessary rather than nice to have?**
Because bad memory is worse than no memory. If the agent could write "resolved: it was DNS" and be wrong, that lie persists, and the *next* incident recalls it and gets worse. A memory system without a write boundary degrades over time instead of improving.

**Which feedback types write memory?**
`DIAGNOSIS_CONFIRMED` and `DIAGNOSIS_REJECTED` write a `DIAGNOSIS` memory with that outcome. `OPERATOR_CORRECTION` writes an `OPERATOR_CORRECTION` plus a `DIAGNOSIS`. `RESOLUTION_CONFIRMED` writes a `RESOLUTION` and promotes a `RUNBOOK_ENTRY`. `INCONCLUSIVE` writes an inconclusive diagnosis. **`RESOLUTION_FAILED` writes nothing authoritative** — a fix that did not work must never become a validated runbook.

**What if the operator rejects the agent's diagnosis?**
It is retained as `rejected`, never as `confirmed`. That is a useful memory in its own right — it tells a future run that this cause was considered and ruled out.

**What if the operator's correction contradicts the agent's proposal?**
The correction overrides it for that incident. The correction carries the operator's authoritative cause and, when known, the validated fix. It is tested.

**Can an operator change their mind after resolving?**
No. A `RESOLVED` incident rejects further feedback with a 422 and the message says to open a new incident. Reopening would rewrite history that memory has already learned from.

**Who can write memory — can the frontend do it directly?**
The frontend never touches Hindsight. It calls our API, and the API owns every retain. The frontend cannot construct a memory, only an operator outcome that the backend validates and translates.

---

## C. Agent loop and tool safety

**You treat model output as untrusted. What does validation actually check?**
Four things before execution, in order: the tool name is in the allowlist, the arguments parse as JSON, the arguments are a JSON object rather than an array or scalar, and they validate against a per-tool argument model — required fields, types, enums, numeric bounds. Only then does the handler run. A destructive or unknown tool cannot execute because it is not in the allowlist.

**What happens when the model produces a malformed tool call?**
It gets exactly one repair attempt **per tool name**, with the validation error fed back to the model. Per-name rather than a global budget, because a global budget lets a model loop between two broken calls forever. If it is still malformed after repair, the run ends in `tool_call_invalid` rather than retrying indefinitely.

**How do you know the model is not making up its evidence?**
A grounding guard. Any memory ID in `cited_memory_ids` must have been returned by a tool **in that run** — we track recalled IDs on the tool context. A citation the model was never shown is rejected and repaired once. There are tests for an invented ID, an ID from a different run, and an ID cited while memory is off.

**What if memory is off and the model cites something anyway?**
Rejected outright: "cited_memory_ids must be empty when memory_mode is off; no memory was consulted." The model is then repaired. Without that rule, memory-OFF could still produce a grounded-looking answer, and the comparison would be meaningless.

**Can the model invent a runbook ID?**
No. `propose_resolution` validates `runbook_id` against the catalog, and `propose_diagnosis` validates `suspected_root_cause_id` the same way. Both reject unknown IDs and tell the model to use an ID it was actually given.

**How does the model know which cause a runbook treats?**
The runbook ID lives in memory metadata, not in the text, so we annotate every rendered hit with `runbook_id=` and, derived from the catalog, `root_cause_id=`. Without that annotation the model can retrieve the right fix and still be unable to name the cause.

**That sounds like a detail. Why is it in this answer?**
Because it was a real bug, and it is the most instructive one in the project. The annotation was passed on the `lookup_runbook` path and omitted on the `recall_similar_incidents` path. The model retrieved the taught fix, cited it correctly, and never named the cause — so Root Cause Hit@1 never moved, and **nothing reported an error**. The test suite was green because the test covered the path that worked. We found it by running the system and reading the record. The fix is one word; the regression test now pins the invariant across every tool that renders a hit, and the `catalog` parameter is required so the omission is a `TypeError` rather than a silent degradation.

**How many steps can the agent take?**
`agent_max_steps`, currently 8. Measured: the primary model issues roughly one tool call per turn. A model that never stops hits the bound and the run reports `max_steps` rather than looping.

**Does the agent ever fall back to another model?**
Only for retryable failures — rate limits, 5xx, timeouts, connection errors — after bounded backoff. A **permanent** primary failure (401, 403, 404, 422) raises immediately instead of falling back. That is deliberate: silently swapping models would change what the demo actually ran on, and a configuration error should look like a configuration error.

---

## D. Failure and degradation (edge cases)

**What happens when Hindsight returns a 5xx?**
Bounded exponential backoff, up to `hindsight_max_retries` (3). Retryable: 408, 425, 429, 500, 502, 503, 504. After exhaustion the run continues in degraded mode, the trace records `hindsight_unavailable`, and the Memory Inspector shows `memory service unavailable`.

**And a 401 or 403?**
Never retried — one attempt, then it fails as `hindsight_auth_error`. Retrying an auth failure just delays the real message, which is "your credentials are wrong". Tested for both 401 and 403.

**Why does degraded mode exist instead of just failing the run?**
Because a memory outage should not stop incident response. The agent continues without memory, and the UI says so. The important part is that degraded is a **distinct state** from an empty recall: the agent is told to treat it as *no evidence*, not *no precedent*. Those lead to different behaviour, and blurring them would be a lie.

**What if Hindsight returns a malformed result?**
It is rejected and logged, and the well-formed results beside it survive. Three specific rules: a result with no ID or no text is not evidence and is dropped; an unparseable score keeps the text and loses only the score; non-mapping metadata and non-list tags are dropped without losing the hit. Letting a bad row raise would dress it up as a provider outage and burn the retry budget on something retrying cannot fix — and letting it through would hand the model a hit with no ID, which it could cite and which dedupe could not tell apart from another such hit.

**What if every result in a recall is malformed?**
It reports `no_match`, not `degraded`. Rejecting every row is an empty recall, not an outage, and saying which is the whole point.

**What if the bank does not exist?**
It is created if needed, and creation failure is reported as a failure rather than swallowed. A missing bank during seeding creates it; a missing bank mid-recall degrades.

**What if the recall legitimately finds nothing?**
The trace records `no_match` and the model is told honestly that there is no relevant historical match. It does not get an empty string and it does not get to guess. On the novel demo incident the agent says it found nothing applicable and proposes no fix — which is the correct answer, and the one a pattern-matching system gets wrong.

**What if the operator submits feedback for an unknown incident?**
404. For an unknown feedback type, an unknown runbook ID or an unknown root cause ID, 422 with the offending field named. Required fields are validated per feedback type — `DIAGNOSIS_CONFIRMED` requires a root cause, `RESOLUTION_CONFIRMED` requires a validated fix, `OPERATOR_CORRECTION` requires the corrected cause.

**What if the alert payload is bad?**
422 with a usable message: wrong types, blank required fields, overlong title or summary, an unparseable timestamp, an unknown severity or memory mode, non-JSON bodies, and JSON arrays. Validation runs before anything is persisted, so a rejected payload creates no incident.

**What if the model provider is entirely down?**
The run ends in `model_failed` with every provider error code recorded. The incident is still created and the timeline says the run failed — it does not invent a diagnosis. The API returns 503 before creating anything if no API key is configured, so there is no half-created incident.

**What if you seed the bank twice?**
It is idempotent. A state file records retained document IDs, and a re-run retains nothing. The load-bearing case is a bank switch: state written for a *different* bank is not evidence about this one, so it is ignored and the new bank is seeded properly. Getting that wrong is silent — the run would skip everything, report success, and leave the bank empty.

**What if seeding fails halfway?**
The completed document IDs are saved, so the next run resumes at the unretained events rather than starting over. An auth failure stops the run rather than leaving it half-seeded.

**Your evaluation caches runs. What if a run is incomplete?**
Incomplete runs are not cached, so a rate-limited incident is retried on the next invocation instead of being frozen into the results as an absence. And the cache key includes the model, so a run on the fallback can never be reused as a run of the primary — otherwise the curve would silently mix two models and part of its shape would be provider weather rather than memory.

**Is there a single point of failure?**
Yes, and it is the SQLite file. Incident state and timelines live there. It is fine for a single-operator console, and it is marked in the code as needing a real database before concurrent alert ingestion. We did not pretend otherwise.

---

## E. Data and evaluation

**Is the dataset real?**
No. NimbusPay is fictional. 52 incidents across 8 weeks, 7 services, 9 root causes, 9 runbooks, 8 postmortems, 145 durable memories. Internally consistent and deterministic — generated from a fixed seed, so the same inputs produce byte-identical data. It is not production data and we do not claim it is.

**How do you avoid the dataset simply repeating the answer?**
Every incident carries hidden evaluation labels: the true root cause, the validated runbook, service, severity, incident type. Those labels are never retained into memory. Recall sees the alert and the history, not the answer. There is also an explicit leak test — an evaluated incident's own resolution must not appear in the history it is evaluated against, and that test failed the first time we ran it because a filter matched any event carrying a validated runbook ID.

**What does the learning curve actually measure?**
For each occurrence of a recurring pattern, we give the agent the history *strictly before* that occurrence and ask it to diagnose. Then we check its first answer against the hidden label. Root Cause Hit@1 is whether the first diagnosis names the right cause; Validated Fix Hit@1 is whether the first fix matches the labeled runbook; Grounded Response Rate is whether it cited a memory that genuinely supports the answer, checked by provenance rather than by the model's own account.

**What did you actually measure?**
One pattern, cutoffs 2 through 5, on the shipped primary model, all four runs completed: Root Cause Hit@1 `0.00 → 1.00 → 1.00 → 1.00`, Validated Fix Hit@1 `0.00 → 0.00 → 1.00 → 1.00`, grounded `1.00` throughout.

**And the caveat?**
`n = 1` per cutoff, one pattern out of six. Four runs, each a different incident, so the steps between cutoffs are not a controlled comparison. It illustrates the mechanism — later occurrences become diagnosable because confirmed outcomes accumulated — and it is not a rate. No target was set in advance and none is reported. The full six-pattern curve has not been run; at roughly 20k tokens per incident against a 200k daily limit, it cannot be run in one day on the free tier.

**What about the teach-then-replay result?**
Measured on the shipped primary. Before the teach the agent names nothing; after an explicit operator correction and resolution confirmation, the same alert produces `RC-009`, a hit on the cause and the fix, and two cited memories. Learning gain on Root Cause Hit@1: **+1**. The bank is deleted and recreated before the before-run, so the "before" cannot see a stale teach.

**Is there anything you tried to measure and could not?**
Yes, and it is documented rather than omitted. The fallback model cannot reliably complete a multi-turn tool loop — it intermittently emits its own XML tool syntax, which the provider rejects with `400 tool_use_failed`; measured, 2 of 4 runs completed. It also rejects our request size outright on the free tier (`OTPM: Limit 1000, Requested 1502`). Every number above is from the primary, so this does not qualify them — but the fallback is not currently a working fallback, and that is an open issue rather than a fixed one.

---

## F. The hard questions

**How is this different from RAG over a vector store?**
The write path. A retrieval system reads a corpus somebody else maintains. Here the corpus is created by the incident workflow itself: an operator confirms an outcome, and that outcome becomes retrievable memory for the next incident. The last demo step is the agent getting better at an incident that had no precedent — which RAG over a static corpus cannot do.

**How is this different from a chatbot with a system prompt?**
The chatbot has opinions. This has a bank, a scoping scheme, a relevance threshold, a provenance trail, and a write boundary. The difference shows up in the failure cases: this system says "I found nothing applicable" and stops, rather than producing a confident answer with nothing behind it.

**What is the most likely way this breaks in production?**
Memory poisoning through the write path — an operator confirms a wrong cause in a hurry, and that wrong cause is now precedent. The mitigations are that confirmations are attributed to a named operator, corrections override rather than append, and the provenance of every memory is inspectable. A production system would also want to expire or demote memories that later turn out to be wrong; we do not have that, and it is the first thing we would add.

**What is the biggest thing you did not build?**
Real integrations. No PagerDuty, no Slack. The workflow is designed to slot into an existing paging system — the alert payload is the only input — but wiring it up is post-v1. We chose depth on memory over breadth of integration.

**What would you do differently?**
Two things. We would have written the tool-result annotation as a single required code path from the start rather than as an optional parameter on two call sites, because the optionality is exactly what let the cause annotation go missing on one of them. And we would have keyed the evaluation cache by model from the beginning, rather than discovering after the fact that a fallback run could be reused as a primary run.

**What are you least confident about?**
The curve. One pattern, one incident per point, on a free tier that cannot run the full set. The mechanism is demonstrated and the teach-replay result is clean, but the shape of the curve across all six patterns is unverified, and we are not going to imply otherwise.

**Why should we believe any of your numbers?**
Because each one has a command next to it that reproduces it, the caveats travel with the numbers rather than sitting in a separate section, and two of the bugs we reported were found by running the system and disagreeing with our own passing test suite. The screenshots in the README are missing precisely because the only ones we had came from a fake model, and shipping those would have been the dishonest option.
