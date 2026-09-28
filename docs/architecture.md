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

### 2.6 The sync client cannot run inside an event loop

The Hindsight SDK is synchronous and fails with `This event loop is already running` if called from
a running asyncio loop. Two consequences:

1. The startup bank probe in the FastAPI lifespan is executed via `asyncio.to_thread`.
2. Every request handler is a sync `def`, so FastAPI runs it in the threadpool and the whole memory
   layer stays synchronous. A test asserts the startup probe does not run on the event loop.

> `ponytail:` a long agent run occupies one threadpool worker. If concurrent incident throughput
> matters, move the agent loop to an async worker pool or use the SDK's `arecall`/`aretain`.

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

`scripts/seed_memory.py` builds 144 durable events from the seed files
(52 `INCIDENT_OPEN`, 52 `DIAGNOSIS`, 26 `RESOLUTION`, 8 `POSTMORTEM`, 6 `RUNBOOK_ENTRY`) and retains
them into the dedicated `dejaops-prod` Memory Bank.

- Idempotent: retained `document_id`s are recorded in `data/store/seed_state.json` and skipped on
  the next run.
- `--dry-run` prints the events and their tags/metadata without calling Hindsight.
- `--reset` deletes and recreates the bank.
- An authentication failure stops the run rather than leaving a half-seeded bank.
- The demo incident is deliberately **not** seeded; the learning demonstration depends on it
  starting untaught.

`data/store/` is gitignored — SQLite and the seed state file are runtime state, not source.
