# DejaOps architecture

This document records how the system is built and, where it matters, **why** — including the
Hindsight API behaviours that were verified by running code against the live service rather than
assumed from examples.

Verification basis for everything in the "Hindsight facts" section:

- `hindsight-client` **0.10.1**, `GET /version` reports `api_version=0.10.1`
- endpoint `https://api.hindsight.vectorize.io`
- evidence produced by direct API calls recorded in this repository's commit history

---

## 1. Component map

```
                     ┌──────────────────────────────┐
   alert payload ──▶ │  FastAPI  (src/api)          │
                     │  · /health, /health/memory   │
                     │  · incidents, chat, feedback │
                     └───────────┬──────────────────┘
                                 │
              ┌──────────────────┴───────────────────┐
              ▼                                      ▼
   ┌──────────────────────┐               ┌────────────────────────┐
   │ SQLite               │               │ Agent loop (Groq)      │
   │ active incident state│               │ · tool-call validation │
   │ timeline, outcomes   │               │ · repair once          │
   │ = source of truth    │               │ · model fallback       │
   └──────────────────────┘               └───────────┬────────────┘
                                                      │
                                                      ▼
                                          ┌────────────────────────┐
                                          │ MemoryStore (interface)│
                                          └───────────┬────────────┘
                                                      │ only this file imports
                                                      │ the Hindsight SDK
                                                      ▼
                                          ┌────────────────────────┐
                                          │ hindsight_client.py    │
                                          └───────────┬────────────┘
                                                      ▼
                                                 ┌─────────┐
                                                 │Hindsight│
                                                 └─────────┘
```

### Module responsibilities

| Path | Responsibility |
| --- | --- |
| `src/config.py` | environment/`.env` configuration, model IDs, memory mode, thresholds |
| `src/logging_setup.py` | logging with secret redaction (tokens never reach a log line) |
| `src/contracts.py` | the frozen shared contract: alert, proposal, feedback, incident state |
| `src/catalog.py` | read-only service / root-cause / runbook catalog from the seed files |
| `src/memory/interface.py` | `MemoryStore` protocol, `RecallHit`, `RecallOutcome` |
| `src/memory/schema.py` | durable event types, tag/metadata derivation |
| `src/memory/trace.py` | `MemoryTrace`, `TraceLog`, provider-trace sanitisation |
| `src/memory/hindsight_client.py` | **the only Hindsight importer**; retry, degrade, normalise, dedupe |
| `src/memory/disabled.py` | memory-OFF store; performs no network calls |
| `src/memory/fake.py` | in-memory store for tests and offline development |
| `src/agent/prompts.py` | system prompt and message rendering (identical across memory modes) |
| `src/agent/tools.py` | tool specs, argument validation, grounding guards, handlers |
| `src/agent/groq_client.py` | Groq client: bounded retry, model fallback, error taxonomy |
| `src/agent/loop.py` | the bounded agent loop and its honest failure paths |
| `src/agent/trace.py` | `AgentRun`, `ModelCall`, `ToolCall` records |
| `src/api/app.py`, `routes.py` | HTTP surface, lifespan, CORS, trace ring buffer |
| `scripts/generate_data.py` | deterministic NimbusPay dataset + hidden eval labels |
| `scripts/seed_memory.py` | idempotent seeding of the Memory Bank |

The memory boundary exists so the application can switch memory OFF, swap a Hindsight deployment,
or test memory behaviour without touching agent code.

---

## 2. Hindsight facts (verified, not assumed)

### 2.1 Client shape

```python
from hindsight_client import Hindsight

client = Hindsight(base_url=..., api_key=..., timeout=30.0)
client.create_bank(bank_id="dejaops-prod", name="dejaops-prod")
client.retain(bank_id=..., content=..., context=..., timestamp=...,
              document_id=..., metadata={...}, tags=[...], retain_async=False)
client.recall(bank_id=..., query=..., tags=[...], tags_match="all",
              budget="mid", max_tokens=4096, trace=True,
              min_scores={"final": 0.2})
```

Async variants exist (`aretain`, `arecall`, `acreate_bank`, `aclose`). The generated API namespaces
(`client.banks`, `client.memory`, …) must be awaited.

### 2.2 `tags_match` defaults to `"any"` — and that is a footgun

`recall(tags=[...])` defaults to `tags_match="any"`, which means **"match ANY of these tags"**. That
*loosens* the query instead of narrowing it. Measured on the seeded bank with
`tags=["service:checkout-api", "incident_type:latency", "event_type:resolution"]`:

| `tags_match` | hits | returned event types |
| --- | --- | --- |
| `any` (SDK default) | 5 | `INCIDENT_OPEN`, `INCIDENT_OPEN`, observation, `POSTMORTEM`, `POSTMORTEM` |
| `all` | 2 | `RESOLUTION`, `RESOLUTION` |
| `all_strict` | 2 | `RESOLUTION`, `RESOLUTION` |
| `exact` | 0 | — (requires an identical tag set; too strict to be useful) |

**Decision:** our tags express a *scope*, so the adapter defaults to `tags_match="all"`. `"any"` is
still available per call for deliberate broadening. This is asserted by tests.

### 2.3 Recall is never empty — so a threshold is required

Hindsight returns nearest neighbours rather than an empty list. An unrelated query against a
matching tag scope still returned 3–95 results. Without a threshold, "no relevant memory found"
would be a lie.

`recall(min_scores={"final": X})` is applied provider-side. Measured top scores over 8 relevant and
6 irrelevant queries against the seeded bank:

| class | top `final` score range |
| --- | --- |
| relevant | 0.3526 – 1.0874 |
| irrelevant | 0.0000 – 0.0891 |

Separation window is `(0.0891, 0.3526)`. `HINDSIGHT_MIN_FINAL_SCORE` defaults to **0.2**, roughly
2.2× above the highest irrelevant score and 1.76× below the lowest relevant one. Re-calibrate after
any change to the dataset, the embedding model, or the bank configuration.

Note that tightly-scoped queries naturally produce lower `final` scores because fewer candidates
compete in the RRF merge — relevant resolutions scored ~0.35–0.42, not ~1.0. This is why the
threshold cannot be set near 0.3.

### 2.4 Tags vs metadata

| Mechanism | Purpose | DejaOps values |
| --- | --- | --- |
| `tags` | recall scoping / filtering | `environment:prod`, `service:checkout-api`, `severity:p1`, `incident_type:latency`, `event_type:resolution` |
| `metadata` | context carried with the memory | `incident_id`, `event_type`, `source`, `timestamp`, `runbook_id`, `outcome` |

Metadata is **never** used as a recall filter. A test asserts the two key sets are disjoint.

`event_type` appears in both. It is duplicated deliberately: metadata records the provenance of the
memory, while the `event_type:` tag is what makes scoped recall possible (for example
`event_type:runbook_entry` for a runbook-only lookup). Without the tag, runbook retrieval could not
be scoped at all.

### 2.5 Observations are real results

After a retain, Hindsight consolidates facts into **observations** — derived nodes that carry the
inherited tags but may have no `metadata`. Recall therefore returns a mix of raw facts and
observations. The adapter treats both as legitimate hits and does not assume `metadata` is present.

Because each retained document comes back **twice** — once as the document (`type="world"`, with
`document_id` and our metadata) and once as its own paraphrase (`type="observation"`, no metadata)
— the adapter deduplicates on `(incident id, event type)`, preferring the copy that carries
provenance. Without this, every memory was presented to the model twice and half the citations
pointed at ids with no `incident_id`.

Runbook memories are keyed by their runbook id rather than their synthetic `RUNBOOK-<id>` incident
id, because resolution memories also mention a runbook id in their text and the two must not
collapse into one another.

`event_type` is resolved from `metadata` first and from the `event_type:` tag second, so an
observation with no metadata still reports the event type it came from.

### 2.5a A similarity threshold is wrong for an exact tag scope

`min_scores` is a relevance gate, and it is only meaningful when the scope is broad. When the tags
themselves are the relevance signal — "every runbook validated for `checkout-api`" — the gate is a
bug. Measured: `RB-014` is the only runbook validated for `checkout-api`, and it scores **0.0**
against the query `checkout-api latency`, so the default threshold hid it completely and
`lookup_runbook` always reported no match.

`MemoryStore.recall` therefore takes an optional `min_score` override. `lookup_runbook` passes
`0.0` **only when a service narrows the scope**; without a service the scope is the whole runbook
set and the threshold stays on, so an unrelated runbook is never presented as relevant. When
unscoped runbooks do come back, the tool says so in its result.

### 2.5b A long query hides outcome memories

Outcome memories are one short sentence ("Incident INC-1038 involving checkout-api latency was
resolved by scaling the payments-ledger consumer group…"). A long symptom narrative dilutes their
similarity score below the threshold. Measured on the seeded bank, same tag scope, same
resolution memory:

| query | top `final` score |
| --- | --- |
| 40-word symptom narrative | 0.057 |
| 12-word symptom | 0.37 |
| `checkout-api` + 12-word symptom | 1.05 |
| `checkout-api latency` | 1.07 |

The agent therefore issues **two scoped recalls** instead of one broad one: the full narrative
against `event_type:incident_open` (narrative matches narrative), and a short service-anchored
query against `event_type:resolution` (outcome matches outcome). A single service-scoped recall
ranked near-duplicate `INCIDENT_OPEN` memories above every resolution and postmortem, so the
memories that actually carry an outcome never reached the model.

A runbook lookup is scoped by `event_type:runbook_entry` and **never** by `incident_type`.
`RUNBOOK_ENTRY` events are keyed by root cause and service; adding an incident type made every
runbook lookup return nothing. A test asserts this.

### 2.6 The sync client is thread-affine — so the adapter owns a loop thread

Measured, not assumed. The SDK's synchronous wrappers (`retain`, `recall`, ...) call
`asyncio.get_event_loop()` and then `loop.run_until_complete(...)` on the *calling* thread. The SDK's
own docstring says those wrappers "exist for scripts and REPLs" and that the `a*` variants should be
used from "frameworks like FastAPI".

Calling them through FastAPI fails, and not with the documented error. Every memory call from a
handler returned `hindsight_unavailable` / `Timeout context manager should be used inside a task`,
because FastAPI dispatches each sync handler to a threadpool thread whose loop the SDK cannot drive.
A thread-matrix probe pinned the boundary exactly:

| Calling context | Sync wrapper |
| --- | --- |
| main thread, no running loop | works |
| main thread, inside `asyncio.run` | fails: `This event loop is already running` |
| threadpool thread, no loop anywhere | works |
| threadpool thread while a loop runs elsewhere | works |
| **the same client reused from a second thread** | **fails** |

That last row is the one that matters: the client binds to the first thread that uses it, so a
single long-lived client shared across FastAPI's threadpool is broken by construction. It also means
the failure is invisible to a test suite built on a thread-agnostic fake — which is why this survived
until a live HTTP run.

The adapter therefore calls the SDK's **async** methods (`acreate_bank`, `aretain`, `arecall`,
`aget_version`, `adelete_bank`, `aclose`) and marshals each one onto a single dedicated event loop on
a single dedicated thread (`_AsyncBridge`). `asyncio.run_coroutine_threadsafe` wraps the coroutine in
a task, which is what `asyncio.timeout()` inside the SDK and aiohttp requires. One loop also means one
`aiohttp` connection pool, since the SDK builds its session lazily on the loop of the first request.

Consequences:

1. `HindsightMemoryStore` is safe to call from the event-loop thread and from the threadpool. The
   lifespan probe calls it directly; no `asyncio.to_thread` indirection is needed.
2. Nothing outside `src/memory/hindsight_client.py` may touch the provider client. `delete_bank` was
   added to the adapter for this reason — the seed script used to reach into `store._client` and call
   the sync `delete_bank` on the main thread, which poisoned every later call in the same process.
3. A retry builds a **fresh** coroutine per attempt; a coroutine cannot be awaited twice.

Regression guard: `tests/memory/test_hindsight_client.py` drives the adapter through
`asyncio.to_thread` while a loop is running, which is the exact FastAPI shape.

> `ponytail:` one agent run still occupies one threadpool worker for its duration. If concurrent
> incident throughput matters, make the handlers async and await the `a*` methods directly instead of
> marshalling through the bridge.

---

## 3. Authority boundary

The model may *propose*. Only the backend lifecycle may *commit* durable memory.

```
INCIDENT_OPEN   retained automatically from the normalised alert
DIAGNOSIS       written as `proposed` on the timeline; retained only once the operator
                confirms, rejects, or marks it inconclusive
RESOLUTION      retained only on explicit operator confirmation of a verified fix
RUNBOOK_ENTRY   promoted from a confirmed resolution
POSTMORTEM      authored after the fact
OPERATOR_CORRECTION  retained when the operator states the actual cause/fix
```

`MemoryEvent` is structurally incapable of representing a proposal: it only carries the six
authoritative event types above. There is no code path by which a hallucinated
"resolution successful" reaches Hindsight.

---

## 5. The agent loop

`src/agent/loop.py` is bounded and defensive by construction:

- at most `AGENT_MAX_STEPS` model turns (default **8**; measured, the primary model issues roughly
  one tool call per turn, so investigate → diagnose → propose needs about six);
- every tool call is validated before it executes;
- a malformed call gets **exactly one** repair, then the run ends with an honest error;
- a retryable model failure falls back once, and a total failure ends the run with an error rather
  than a fabricated answer;
- the loop stops as soon as the model replies without tool calls.

### 5.1 Tool-call validation

Model output is untrusted input. Before any handler runs, a call must pass, in order:

1. the tool name is in the allowlist (`recall_similar_incidents`, `lookup_runbook`,
   `get_service_map`, `propose_diagnosis`, `propose_resolution`);
2. the arguments parse as a JSON object;
3. required fields are present and correctly typed (Pydantic models per tool);
4. enum values are valid;
5. `cited_memory_ids` is a subset of the memory ids **actually returned by a tool in this run**;
6. a proposed `runbook_id` exists in the catalog;
7. a resolution is not proposed before a diagnosis.

Rules 5–7 are the anti-hallucination guards. Rule 5 is what makes the Grounded Response Rate
metric meaningful rather than self-reported: a citation the model invented cannot survive validation.

On failure the errors are fed back as the tool result, the model may correct itself once, and a
second failure ends the run as `tool_call_invalid`. Repairs are counted **per tool name**, so a
model cannot loop by alternating between two broken calls.

### 5.2 Model fallback

| Failure | Behaviour |
| --- | --- |
| 429, 5xx, timeout, connection | bounded exponential backoff, then the fallback model |
| 400 / 422 | never retried; fails clearly |
| 401 / 403 | never retried; fails clearly |
| 404 (model gone) | never retried; fails clearly, and **does not** silently fall back |

A decommissioned primary model is a configuration failure, not something to paper over with a
different model — a silent swap would change what the demo actually ran on. `verify_models()`
checks both configured models against the provider at startup and fails loudly if either is gone.

### 5.3 What is never shown

The UI and the traces carry concise evidence, tool results, memory ids, and outcome summaries.
`Proposal.evidence_summary` is capped at two sentences by the prompt and by validation. Model
chain-of-thought is never requested, stored, or displayed; only token counts from the provider's
usage block are retained.

---

## 6. Retry and degradation

| Failure | Behaviour |
| --- | --- |
| 5xx, 429, 408, 425, or a connection error | retried with bounded exponential backoff (`base * 2^attempt`), up to `HINDSIGHT_MAX_RETRIES` |
| 400 / 422 | not retried; `hindsight_validation_error` |
| 401 / 403 | not retried; `hindsight_auth_error`; surfaced as a configuration failure |
| 404 | not retried; `hindsight_not_found` |
| retries exhausted | the call returns a trace with `success=false`, `degraded=true`, and the run continues |

Provider failures never raise out of the adapter. They are reported through `MemoryTrace`, and:

- a failed authoritative retain is never reported as a success;
- a failed recall returns no hits and `degraded=true`, never fabricated memory;
- the `error_message` contains the exception type and message, and a test asserts an API key never
  appears in it.

Every memory call produces a `MemoryTrace` (`operation`, `started_at`, `finished_at`, `latency_ms`,
`success`, `hit_count`, `attempts`, `bank_id`, `error_code`, `degraded`, `no_match`, `query`, `tags`,
`min_score`, sanitized `provider_trace`). Provider traces are stripped of embeddings and bounded in
size before storage or display — the raw recall trace embeds a 384-float query vector that must
never reach the UI or a log.

---

## 7. Memory ON vs OFF

`MEMORY_MODE=off` builds a `DisabledMemoryStore` that makes **no Hindsight calls at all** — it does
not merely skip the prompt context. Every call it answers returns `mode=off` and
`error_code=memory_off`, so the UI can label the run honestly rather than showing a silent empty
result.

Both modes use the same normalized alert, the same model configuration, and the same system prompt.
The only difference is memory availability and the memory-derived context block, which is what makes
the comparison causally meaningful.

---

## 8. Dataset design

`scripts/generate_data.py` is deterministic for a given `--seed` (default `1337`) and produces:

- 52 incidents across ~8 weeks over a 7-service catalog
- 8 postmortems
- hidden evaluation labels in a **separate** file (`data/seed/labels.json`) carrying
  `root_cause_id`, `validated_runbook_id`, `service`, `severity`, `incident_type`
- one novel demo incident (`data/demo/novel_incident.json`) that shares no symptom signature with
  the history

The learning curve is encoded in the data, not asserted in prose:

| occurrence of a pattern | diagnosis outcome | runbook |
| --- | --- | --- |
| 1st | `inconclusive` | none |
| 2nd | `rejected` | none |
| 3rd and later | `confirmed` | validated runbook id |

So later incidents are easier to diagnose **because they carry a confirmed outcome**, not because
the alert text repeats an answer. Tests enforce that the first occurrence of every pattern is never
confirmed, and that no unconfirmed incident carries a validated runbook.

---

## 9. Seeding

`scripts/seed_memory.py` builds 145 durable events from the seed files
(52 `INCIDENT_OPEN`, 52 `DIAGNOSIS`, 26 `RESOLUTION`, 8 `POSTMORTEM`, 7 `RUNBOOK_ENTRY`) and retains
them into the dedicated `dejaops-prod` Memory Bank.

- Idempotent: retained `document_id`s are recorded in `data/store/seed_state.json` and skipped on
  the next run.
- `--dry-run` prints the events and their tags/metadata without calling Hindsight.
- `--reset` deletes and recreates the bank.
- An authentication failure stops the run rather than leaving a half-seeded bank.
- The demo incident is deliberately **not** seeded; the learning demonstration depends on it
  starting untaught.

`data/store/` is gitignored — SQLite and the seed state file are runtime state, not source.

---

## 10. Learning evaluation

`scripts/evaluate_learning.py` measures the four metrics in AGENTS.md section 5 against the hidden
labels. The metrics are defined here, once, so that "the agent got better" is a number rather than
an impression.

### 10.1 Root Cause Hit@1 needs a checkable diagnosis

A hit requires the agent's first diagnosis to name the labeled root cause. That is only measurable
if the diagnosis carries a cause **id**, so `propose_diagnosis` gained an optional
`suspected_root_cause_id`, validated against the catalog exactly like `runbook_id` (an id outside
the catalog is a validation error and goes down the repair path).

The model can only name a cause it has been shown, so the vocabulary reaches it two ways:
`get_service_map` with no argument returns the root cause catalog, and every runbook hit is
annotated with the `root_cause_id` that runbook treats. The field is optional: an honest "nothing
in the evidence singles one out" stays available, and it is scored as a miss rather than a guess.

### 10.2 The curve cannot see its own answer

`curve` mode evaluates occurrence *N* of a pattern having seeded a bank with only occurrences
`<= N-1` of every pattern. The evaluated incident is never in the bank, so a score at cutoff K
measures what K occurrences of history actually buy.

This is the property the tests exist for, and it caught a real leak. The first implementation kept
an event when `event.incident_id` was known **or** `event.runbook_id` was validated by a known
incident. An incident's own `RESOLUTION` event also carries `runbook_id`, so at every cutoff the
evaluated incident's own resolution was seeded into the bank it was being asked about — which would
have inflated every score at cutoff 3 and above. The runbook branch now matches `RUNBOOK_ENTRY`
events only.

The measured effect of the fix, on the real seed:

| cutoff K | events seeded | resolutions | runbook entries |
| --- | --- | --- | --- |
| 2 | 52 | 0 | 0 |
| 3 | 84 | 7 | 7 |
| 4 | 108 | 14 | 7 |
| 5 | 130 | 21 | 7 |
| 6 | 142 | 25 | 7 |

Cutoff 2 holds no validated fix at all, which is what makes it the interesting first point: the
agent has history, but no precedent for a fix.

### 10.3 Grounded means grounded in provenance

Grounded Response Rate does not take the model's word for it. A citation counts as supporting when
the memory's own provenance — read back out of the tool results, not from the run's flat id list —
points at the labeled validated runbook, or at a *different* incident whose hidden label has the
same root cause. An incident's own alert memory never counts as evidence for its own diagnosis.

### 10.4 Incomplete is not wrong

A run that never produced an answer — a rate limit, a provider rejection, step exhaustion — is
recorded as incomplete and **excluded from the Hit@1 denominators**. Scoring a rate limit as a
wrong diagnosis would understate the agent and would not reproduce.

For the same reason incomplete records are **not cache hits**. An earlier version cached them, which
made a transient failure permanent: re-running would skip the incident and the curve would stay
empty forever. Completed records are cached; incomplete ones are re-attempted on the next
invocation, and stay in the file as evidence.

### 10.5 What a full run costs

Measured on the Groq free tier: one incident costs roughly 20k tokens across its tool-call turns,
and the free tier allows 200k tokens per day for `openai/gpt-oss-120b`. The full curve is 26
evaluated incidents, so **it cannot complete in one day on the free tier** — it needs a paid tier,
or several days of resuming. `--limit`, `--cutoff` and `--pace` exist for that, and the JSONL cache
makes each day additive rather than restarting.

This is a constraint on the measurement, not a result. No curve numbers are recorded here until a
run produces them.

### 10.6 A finding the evaluation surfaced: the fallback cannot reliably call tools

`qwen/qwen3.8-27b` is configured as the fallback and was chosen because it advertises tool use.
Driven through a multi-turn tool loop it repeatedly emits its own XML tool syntax
(`<tool_call><function=...>`) instead of the OpenAI function-call shape, which Groq rejects with
`400 tool_use_failed`. The arguments in the rejected output were correct — the serialization is
what fails.

Consequences, stated plainly:

- the primary model's failures still fall back, but the fallback's tool loop is unreliable: measured
  2 of 4 runs completed and 2 ended in an honest `AllModelsFailedError` rather than a worse answer
- `tool_choice="auto"` is already set, so this is not a missing request parameter
- the classification is unchanged. `tool_use_failed` is a 400 and is treated as permanent. Retrying
  a stochastic formatting failure *might* help, but that is a hypothesis, and it will not be
  written down as a fix until it is measured.

The failure is honest either way: the run reports `model_failed`, the timeline says so, and no
fabricated diagnosis is produced.

### 10.7 The first measured run: partial, and on the fallback

Four incidents, one pattern, cutoffs 2 through 5. Two produced an answer.

| cutoff K | incident | status | model | cause@1 | fix@1 | grounded | steps |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2 | INC-1008 | `model_failed` | `qwen/qwen3.8-27b` | — | — | — | 2 |
| 3 | INC-1016 | `model_failed` | `qwen/qwen3.8-27b` | — | — | — | 3 |
| 4 | INC-1019 | `completed` | `qwen/qwen3.8-27b` | hit `RC-007` | hit `RB-018` | yes, 8 cited | 5 |
| 5 | INC-1029 | `completed` | `qwen/qwen3.8-27b` | hit `RC-007` | hit `RB-018` | yes, 8 cited | 5 |

Rates over the two scored runs: Root Cause Hit@1 `1.0`, Validated Fix Hit@1 `1.0`, Grounded Response
Rate `1.0`, mean steps `5.0`.

**What this does not show.** It does not show the learning curve. The two cutoffs that would show
its shape are exactly the two that failed: cutoff 2 holds no validated fix at all, and cutoff 3 is
where the first one appears. The two runs that completed are the two easiest points, where the
answer has been in history for several occurrences. Two perfect scores at the easy end are not
evidence of a curve, and are not reported as one.

**And every one of the four ran on the fallback.** The primary was rate-limited for all of them, so
these are fallback numbers. The product ships with `openai/gpt-oss-120b` as primary, so these do not
characterise the shipped configuration either.

What it does show is that the upper end works end to end: with a confirmed outcome in history the
agent named the correct cause, proposed the correct runbook, and cited eight memories that support
it — and the grounded check confirmed those citations by provenance rather than by the model's
account of them.

The two failures are retried automatically on the next invocation, because incomplete records are
not cache hits. The curve is complete when cutoffs 2 and 3 produce answers.

`--mode teach` was attempted on the same day and both of its runs also ended in `model_failed` for
the same reason. Its mechanism is tested without a model in `tests/api/test_teach_replay.py`; the
live before/after result is not yet measured.
