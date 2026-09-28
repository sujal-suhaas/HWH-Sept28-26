# AGENTS.md — DejaOps · The On-Call Agent That's Seen This Incident Before

This file is the single source of truth for how this repository is built and demonstrated.
Read it fully before doing anything. If another document conflicts with this one during the
build phase, this one wins. When §14 (Definition of Done) is fully green, switch to
`CONTENT_GUIDE.md` for the content phase.

This plan is based on the project requirements: build on Hindsight memory (required tech), use
an LLM through Groq, make memory the product rather than a cosmetic feature, keep scope tight,
use realistic data, and demonstrate obvious value within about 60 seconds.

---

## 0. Operating rules for the coding agent

- This is a two-person team. Both members have authorized automated work and commits on their
  behalf using their GitHub tokens. The ownership map in §6 reflects the agreed division of labor.
  Do not silently change ownership.
- Execute phases in order (§8). Work in small commits and short-lived PRs. The initial scaffold
  may be committed to `main`; after that, work through branches and PRs. Never intentionally leave
  `main` broken.
- Never run a bare `git commit`. Always commit through `scripts/git-as.sh` so author and committer
  identity are explicit.
- Tokens may exist in environment variables at runtime. Never print, echo, log, commit, or write
  the token values to source files, `.env` files, Git configuration, build artifacts, screenshots,
  CI logs, or documentation.
- Use the GitHub CLI with the appropriate `GH_TOKEN` environment variable for repository, issue,
  PR, review, and merge operations. For Git pushes that require authentication, use a runtime-only
  HTTP authorization header or another non-persistent credential mechanism. Do not embed tokens in
  remote URLs or persist them in `.git/config`.
- Before every push, verify attribution with:

  ```bash
  git log --format='%h %an <%ae> %s' -5
  ```

- When Hindsight API behavior is unclear, consult the current Hindsight documentation before
  coding. Never invent endpoint names or request shapes.
- If an API has changed since this plan was written, update the implementation to the current API
  and keep the architecture below intact unless the change would violate the project requirements.
- Never claim benchmarks, accuracy, latency, or learning improvements that were not produced by an
  actual test or demo run and recorded in the repository.
- New ideas outside the scope fence become GitHub issues labeled `post-v1`; do not build them during
  the project phase.

---

## 1. Team configuration — FILL THIS IN BEFORE THE FIRST COMMIT

| Key | Member A (repo owner) | Member B (collaborator) |
| --- | --- | --- |
| Name | `MEMBER_A_NAME` | `MEMBER_B_NAME` |
| GitHub username | `MEMBER_A_GH_USERNAME` | `MEMBER_B_GH_USERNAME` |
| Commit email | `MEMBER_A_EMAIL` | `MEMBER_B_EMAIL` |
| Role | Memory & Agent Lead | Data, Product & Demo Lead |
| GitHub token env var | `GH_TOKEN_A` | `GH_TOKEN_B` |

Required runtime environment variables:

```text
GH_TOKEN_A=...
GH_TOKEN_B=...
MEMBER_A_NAME=...
MEMBER_A_EMAIL=...
MEMBER_B_NAME=...
MEMBER_B_EMAIL=...
HINDSIGHT_API_KEY=...
HINDSIGHT_BASE_URL=...
GROQ_API_KEY=...
```

`.env` is gitignored. `.env.example` documents every key with a placeholder only. Never place real
credentials in `.env.example`.

The Hindsight Cloud API key is created from the Hindsight Cloud connection flow; the Python SDK
uses a `base_url`, API key, and a `bank_id` when retaining or recalling memory. Hindsight's current
core organizational unit is the **Memory Bank**. Do not treat the word "profile" in this document
as the identifier of the bank; a bank has its own profile/configuration. citeturn999001search0turn999001search8

---

## 2. What we're building

**DejaOps** — an on-call incident response agent for a fictional payments company, NimbusPay.
When an alert fires, the agent recalls similar incidents, prior root causes, validated fixes, and
operator corrections. It proposes a grounded diagnosis and runbook, and the system gets more useful
as confirmed incident outcomes become durable memory.

### Problem

On-call engineers waste the first 30–45 minutes of incidents rediscovering what someone already
solved previously. Runbooks become stale and important operational knowledge lives in people's heads.

### 60-second demo story

> "Pager fires: checkout latency. Memory OFF: generic advice. Memory ON: this pattern matches an
> incident from three weeks ago after a payments-ledger deploy; the previous root cause was Kafka
> consumer lag and the validated fix was scaling the consumer group and replaying the affected
> partition."

The exact example above is a target demo scenario. Do not claim it was observed in the real system
unless the seeded data and actual run produce it.

### Judging criteria mapping

| Criterion | How the project earns it |
| --- | --- |
| Innovation | Cumulative operational memory is the product. The live memory ON/OFF comparison makes the effect of memory observable rather than presenting a generic chatbot. |
| Use of Hindsight Memory | Incident history is retained into Hindsight; relevant history is recalled during diagnosis; confirmed outcomes and operator corrections become future memory; the Memory Inspector exposes retain/recall traces. |
| Technical Implementation | Hindsight is isolated behind one interface; tool-call output is validated; malformed tool calls trigger repair; model fallback is available; Hindsight failures degrade honestly; tests cover the failure paths. |
| User Experience | Chat + incident timeline + Memory Inspector + memory toggle + a short scripted demo. |
| Real-world Impact | The workflow targets a real operational problem and has a clear post-v1 path toward Slack/PagerDuty integrations without requiring those integrations in v1. |

### Scope fence

Do **not** build in v1:

- real Slack integration
- real PagerDuty integration
- authentication or multi-tenancy
- Kubernetes deployment
- voice
- mobile applications
- arbitrary multi-agent orchestration
- a general-purpose knowledge base unrelated to incident response

Put post-v1 ideas in GitHub issues instead.

---

## 3. Stack — fixed decisions

- Python 3.11+
- `uv` for Python environment/package management
- FastAPI + Uvicorn — backend, primarily Member A's domain
- Hindsight — required memory layer; use Hindsight Cloud or the current open-source deployment
- Official Hindsight Python SDK
- Groq — primary model: `openai/gpt-oss-120b`
- Groq — fallback model: `qwen/qwen3.8-27b`
- SQLite — transactional incident/timeline data only; Hindsight remains the memory layer
- React + Vite + Tailwind — frontend, primarily Member B's domain
- Playwright — browser E2E happy path

`qwen/qwen3-32b` is intentionally not used. Groq lists it as deprecated and gives a newer Qwen
replacement path; the currently documented Qwen successor used here is `qwen/qwen3.8-27b`, which
supports tool use and JSON/schema modes. citeturn480535search0turn480535search3

Keep model IDs configurable through environment/config rather than hardcoding them throughout the
codebase. The application must fail clearly if a configured model is no longer available.

---

## 4. Memory design — memory is the product

Use one dedicated Hindsight **Memory Bank** for the deployed DejaOps workspace, for example:

```text
dejaops-prod
```

A Memory Bank is Hindsight's isolated organizational unit. Banks contain memories and have their
own configuration/profile. Use `bank_id` consistently in the adapter. citeturn999001search0turn999001search1

### Retain categories

Retain durable operational facts from these event types:

- `INCIDENT_OPEN` — normalized alert summary, service, symptom cluster, and initial signals
- `DIAGNOSIS` — diagnosis hypothesis plus whether it was confirmed, rejected, or inconclusive
- `RESOLUTION` — validated fix, verification result, and time-to-resolve
- `POSTMORTEM` — root cause, contributing factors, and prevention
- `RUNBOOK_ENTRY` — validated fix recipe promoted from a successful resolution
- `OPERATOR_CORRECTION` — the operator explicitly states the agent was wrong and supplies the actual cause/fix

### Recall moments

- New alert → similar past incidents
- During diagnosis → prior root causes and symptom patterns
- Before proposing a fix → validated runbooks for the suspected cause

### Hindsight metadata vs tags — important

Do **not** use document `metadata` as the recall filter.

Use:

- **tags** for recall scoping/filtering, e.g. `service:checkout-api`, `severity:p1`,
  `incident_type:latency`, `environment:prod`
- **metadata** for source/context fields that should travel with the recalled memory, e.g.
  `incident_id`, `event_type`, `source`, `timestamp`, `runbook_id`

Hindsight's current documentation explicitly distinguishes the two: metadata is additional context
and is returned with recalled memories, while tags are the mechanism for scoping recall. citeturn999001search9

### Memory adapter boundary

Only this file may import the Hindsight SDK:

```text
src/memory/hindsight_client.py
```

Everything else depends on our own memory interface/types. This is required so the application can:

- switch memory OFF without changing agent code
- swap Hindsight Cloud and OSS deployments without rewriting the agent
- test memory behavior with a fake implementation

### Retain policy

Do **not** retain every conversational response just to demonstrate Hindsight.

A response may be ordinary interaction and need not create durable memory. Durable memory is created
only from qualifying incident events or explicit operator feedback.

The system should prefer fewer, high-value memories over repetitive transcript storage. Hindsight's
current retain guidance emphasizes specific, contextual information rather than vague observations. citeturn999001search2

### Source-of-truth rule for memory writes

The LLM may **propose** a diagnosis or resolution, but it is not the authority that declares an
incident resolved.

Authoritative durable memories are created by backend-controlled lifecycle events:

1. `INCIDENT_OPEN` may be retained automatically from the normalized alert.
2. The agent may write a proposed diagnosis into the incident timeline as `proposed`.
3. The operator can confirm, reject, or correct that diagnosis.
4. The backend retains the outcome as `DIAGNOSIS` or `OPERATOR_CORRECTION`.
5. The agent may propose a resolution.
6. Only explicit operator confirmation creates the authoritative `RESOLUTION` memory.
7. A confirmed resolution may be promoted into `RUNBOOK_ENTRY`.
8. A postmortem can later create `POSTMORTEM` memory.

This prevents a model hallucination such as "resolution successful" from poisoning future memory.

### Memory ON behavior

When memory is ON:

- perform recall before diagnosis whenever the incident state allows it
- expose which memories were recalled and why they were considered relevant
- ground the response in those memories when relevant
- retain only qualifying durable events
- record trace data for every recall/retain attempt

The UI must distinguish:

```text
memory recalled
memory retained
no relevant memory found
memory service unavailable
```

Never pretend a memory was found when the recall result was empty.

### Memory OFF behavior

When memory is OFF:

- do not call Hindsight recall
- do not call Hindsight retain
- do not read memory-derived runbooks
- do not include recalled memory IDs in the model context
- clearly label the run as `memory_mode=off`

The same normalized alert, same model configuration, and same system prompt should be used for the
ON/OFF comparison except for memory availability and the memory-derived context block. This makes
the demo causally meaningful.

---

## 5. Learning and evaluation — define "better" before the demo

"The agent got better" is not an acceptable claim without a measurable definition.

Every seeded incident used in the learning-curve test must have hidden evaluation labels in the
seed data, including at minimum:

```text
root_cause_id
validated_runbook_id
service
severity
incident_type
```

### Required metrics

Implement `scripts/evaluate_learning.py` or an equivalent deterministic evaluation command with:

1. **Root Cause Hit@1** — the agent's first diagnosis matches the labeled root cause.
2. **Validated Fix Hit@1** — the first recommended fix matches the labeled validated runbook.
3. **Grounded Response Rate** — a memory-ON response cites at least one recalled memory that actually
   supports the diagnosis/fix.
4. **Learning Gain** — for a teach-then-replay case, compare the pre-teach and post-teach Hit@1 result.

Do not invent numeric targets before running the system. The purpose of the metric is to make the
change observable and repeatable, not to manufacture a passing score.

### Learning-curve acceptance test

Use a dedicated novel scenario:

```text
Before teach:
  memory recall → no matching historical incident
  diagnosis → generic/uncertain response

Operator:
  confirms actual root cause
  confirms validated fix

After teach:
  same normalized alert
  memory recall → newly retained incident memory
  first diagnosis → matches the operator-confirmed root cause
  first fix → matches the operator-confirmed fix/runbook
```

The demo must show the actual recorded before/after result. Do not claim that the memory improved if
the replay does not reproduce the expected change.

---

## 6. Repository layout

```text
├── AGENTS.md
├── CONTENT_GUIDE.md
├── README.md
├── .env.example
├── .github/
│   └── workflows/
│       └── ci.yml
├── src/
│   ├── memory/
│   │   ├── hindsight_client.py
│   │   ├── schema.py
│   │   └── trace.py
│   ├── agent/
│   │   ├── loop.py
│   │   ├── tools.py
│   │   ├── prompts.py
│   │   └── groq_client.py
│   └── api/
│       ├── app.py
│       ├── routes.py
│       └── sqlite_store.py
├── frontend/
│   ├── package.json
│   ├── package-lock.json
│   └── ...
├── data/
│   ├── seed/
│   ├── demo/
│   └── store/
├── scripts/
│   ├── git-as.sh
│   ├── generate_data.py
│   ├── seed_memory.py
│   └── evaluate_learning.py
├── docs/
│   ├── architecture.md
│   └── demo/
│       └── script.md
└── tests/
    ├── memory/
    ├── agent/
    ├── api/
    └── e2e/
```

---

## 7. Ownership map

### Member A — Memory & Agent Lead

Owns:

- `src/`
- `scripts/seed_memory.py`
- `scripts/evaluate_learning.py`
- `tests/memory/`
- `tests/agent/`
- `tests/api/`
- `docs/architecture.md`
- `.env.example`
- README sections: architecture and "How Hindsight memory is used"

Tasks:

- Hindsight adapter, schema, trace
- memory filtering and bank configuration
- Groq client
- model fallback
- tool definitions
- tool-call validation/repair
- agent loop
- operator feedback lifecycle
- FastAPI + SQLite
- seed loader
- evaluation script
- edge-case tests

### Member B — Data, Product & Demo Lead

Owns:

- `frontend/`
- `data/`
- `scripts/generate_data.py`
- `.github/`
- `.gitignore`
- `LICENSE`
- README quickstart/screenshots/demo sections
- `docs/demo/script.md`

Tasks:

- realistic NimbusPay incident dataset
- UI
- Memory Inspector
- memory ON/OFF toggle
- live API integration
- Playwright E2E happy path
- CI frontend stages
- demo rehearsal
- repository hygiene

### Shared

- The other member reviews and merges each member's PR.
- Bugs are attributed by the directory in which the bug lives.
- Work spanning both areas is split into separate PRs where practical rather than mixing unrelated
  ownership into one commit.

---

## 8. Git identity and dual-token protocol

Both PATs are runtime credentials supplied through environment variables. Use them directly through
`GH_TOKEN_A` / `GH_TOKEN_B`; do not create credential files containing their values.

### One-time repository setup

Create the GitHub repository using Member A's authenticated GitHub CLI session, then add Member B as
an administrator/collaborator using Member A's token.

Use:

```bash
GH_TOKEN="$GH_TOKEN_A" gh api repos/OWNER/REPO/collaborators/MEMBER_B_GH_USERNAME \
  -X PUT -f permission=admin
```

For Git remotes, keep the remote URL token-free, for example:

```bash
git remote add origin "https://github.com/OWNER/REPO.git"
git remote add a "https://github.com/OWNER/REPO.git"
git remote add b "https://github.com/OWNER/REPO.git"
```

For authenticated pushes, use a runtime-only authorization mechanism such as Git's command-level
HTTP header configuration. Never bake a token into the remote URL.

### `scripts/git-as.sh`

This is the only way to commit:

```bash
#!/usr/bin/env bash
set -euo pipefail
who="${1:?usage: git-as.sh a|b ...}"
shift

case "$who" in
  a)
    NAME="${MEMBER_A_NAME:?}"
    EMAIL="${MEMBER_A_EMAIL:?}"
    ;;
  b)
    NAME="${MEMBER_B_NAME:?}"
    EMAIL="${MEMBER_B_EMAIL:?}"
    ;;
  *)
    echo "identity must be a or b" >&2
    exit 1
    ;;
esac

GIT_AUTHOR_NAME="$NAME" \
GIT_AUTHOR_EMAIL="$EMAIL" \
GIT_COMMITTER_NAME="$NAME" \
GIT_COMMITTER_EMAIL="$EMAIL" \
git commit "$@"
```

### Git rules

- Branch naming: `a/<area>-<slug>` and `b/<area>-<slug>`.
- Each task in §9 gets a GitHub issue assigned to its owner.
- The owner's commit closes the issue with a `Closes #N` footer.
- A uses Conventional Commits.
- B uses plain imperative commit subjects.
- Every PR is opened by the member who did the work and merged by the other member.
- PRs use merge commits, not squash merges.
- Before every push, verify author and committer identity.
- Never rewrite pushed history.
- If the repo is public, enable branch protection with at least one approval and passing CI.

### PR command pattern

Use the appropriate token only for the command that needs it:

```bash
GH_TOKEN="$GH_TOKEN_A" gh pr create ...
GH_TOKEN="$GH_TOKEN_B" gh pr review N --approve --body "..."
GH_TOKEN="$GH_TOKEN_B" gh pr merge N --merge
```

Do not print the token value and do not add it to shell history intentionally.

---

## 9. Build phases

### Phase 0 — Bootstrap

**A**

- initialize repository
- initial scaffold commit to `main`
- `src/` package layout
- `pyproject.toml`
- config loader
- logging
- `.env.example`
- `/health`

**B**

- `.gitignore`
- `LICENSE`
- frontend skeleton
- CI skeleton
- README stub
- Phase 1 issues

**Exit:** clean clone boots backend and frontend; backend health is 200; basic CI is green.

### Phase 1 — Memory backbone + data generator

**A**

- Hindsight adapter with `retain`, `recall`, bank management/config access as needed
- tags/metadata mapping
- ON/OFF switch
- trace schema
- mocked Hindsight tests
- seed loader skeleton

**B**

- realistic NimbusPay dataset generator
- explicit hidden evaluation labels
- demo scenario seed

**Exit:** dataset generation succeeds; memory tests pass; memory ON/OFF behavior is tested.

### Phase 2 — Agent core + frontend shell

**A**

Freeze the backend contract first.

Agent tools must be divided into:

**Read/lookup tools**

- `recall_similar_incidents`
- `lookup_runbook`
- `get_service_map`

**Proposal tools**

- `propose_diagnosis`
- `propose_resolution`

Proposal tools write only incident timeline state such as `proposed` or `pending_confirmation`.
They do not create authoritative `RESOLUTION` memory.

Agent loop requirements:

- validate tool names
- validate required arguments
- validate output schema
- repair malformed tool calls once
- fall back to `qwen/qwen3.8-27b` if the primary model fails in a retryable way
- return an honest error after fallback failure
- preserve a trace of model calls and tool calls
- tests use a fake LLM

**B**

- Vite/React/Tailwind scaffold
- chat UI
- alert feed
- mock API integration

**Exit:** scripted alert completes in agent tests; frontend renders mock flow; contract is frozen.

### Phase 3 — API + UI integration

**A**

Routes:

```text
POST /alerts
POST /chat/{incident_id}
POST /incidents/{incident_id}/feedback
GET  /incidents/{id}
GET  /incidents/{id}/memory-trace
GET  /health
```

`POST /incidents/{incident_id}/feedback` accepts explicit operator outcomes, for example:

```text
DIAGNOSIS_CONFIRMED
DIAGNOSIS_REJECTED
OPERATOR_CORRECTION
RESOLUTION_CONFIRMED
RESOLUTION_FAILED
INCONCLUSIVE
```

An operator correction includes the authoritative corrected cause and, when known, the validated
fix. The API validates that required fields exist for each feedback type.

**B**

- wire frontend to live API
- incident timeline
- Memory Inspector
- memory ON/OFF toggle
- operator feedback controls
- visible confirmation state after feedback

**Exit:** alert → recall → grounded answer → visible trace; operator can confirm/correct outcome;
correction is retained; replay can retrieve it.

### Phase 4 — Data seeding + learning curve

**A**

- seed Hindsight Memory Bank
- tune recall parameters
- confirm tag-based filters
- record trace samples
- run `scripts/evaluate_learning.py`

**B**

- curated teach-then-replay scenario
- demo script
- screenshots/GIF
- judge Q&A

**Exit:** the same novel incident can be run before teach, taught by an explicit operator action,
and replayed afterward with measurable change according to §5.

### Phase 5 — Polish + pre-submission

**A**

- Hindsight 5xx retry with bounded exponential backoff
- empty recall behavior
- malformed tool-call behavior
- invalid feedback behavior
- bad alert payload behavior
- architecture diagram
- Hindsight explanation
- tag/metadata documentation
- version tag `v1.0.0`

**B**

- frontend polish
- README quickstart
- screenshots/GIF
- Playwright happy path
- final demo rehearsal
- close/label issues

**Exit:** §14 fully green.

---

## 10. Data plan

Fictional company: **NimbusPay**.

### Service catalog

Use 6–8 realistic services, including:

- `checkout-api`
- `payments-ledger`
- `auth-service`
- `fraud-scorer`
- `kafka-bus`
- `redis-cache`
- `webhook-dispatcher`

### Incident history

Seed roughly 40–60 incidents across 8 weeks with:

- realistic alerts
- error messages
- timestamps
- incident timelines
- operator notes
- diagnosis outcomes
- resolutions
- 6–8 detailed postmortems
- stable hidden evaluation labels

### Recurring patterns

1. payments-ledger deploy → checkout latency spike
2. nightly batch → Kafka consumer lag
3. Redis failover flaps
4. TLS certificate rotation → auth-service errors

The recurring pattern should become more diagnosable because later incidents contain confirmed
outcomes, not because the dataset simply repeats the answer in the alert text.

### Demo scenario

`data/demo/` contains a novel incident that is not present in the original history. It is used for
the teach-then-replay learning demonstration.

---

## 11. API and state model

SQLite is the source of truth for active incidents and UI timelines.

A useful incident state progression is:

```text
OPEN
→ DIAGNOSING
→ WAITING_FOR_OPERATOR
→ RESOLVING
→ RESOLVED
```

An incident can also enter `INCONCLUSIVE` or `FAILED` without falsely creating a successful
resolution memory.

Store at minimum:

- incident ID
- alert payload
- service
- severity
- incident type
- current state
- proposed diagnosis
- proposed resolution
- operator outcome
- root cause (when confirmed)
- validated runbook ID (when confirmed)
- created/updated timestamps
- model/memory mode
- tool/memory trace IDs

---

## 12. Hindsight adapter requirements

`src/memory/hindsight_client.py` must expose our stable internal interface, for example:

```text
create_bank_if_needed()
retain(event)
recall(query, tags=...)
health()
```

Do not expose raw Hindsight SDK objects to the rest of the app.

Every adapter call returns normalized data plus trace information such as:

```text
operation
started_at
finished_at
latency_ms
success
hit_count
bank_id
error_code
```

Use the current official SDK/API rather than copied examples from outdated versions. Hindsight's
current docs show Python operations using `bank_id`, and banks are isolated memory containers. citeturn999001search1turn999001search8

### Retry/degrade policy

- Retry transient Hindsight failures with bounded exponential backoff.
- Do not retry permanent validation/authentication errors blindly.
- After retry exhaustion, continue in memory-OFF/degraded mode.
- Show the degraded state in the Memory Inspector.
- Never fabricate recalled memories.
- Never silently convert a failed authoritative retain into a successful one.

---

## 13. Quality bar

### Tool-call validation

Model output is untrusted input.

Before execution:

1. verify tool name
2. validate JSON shape
3. validate argument types
4. validate enum values
5. reject unknown destructive actions
6. execute only allowlisted tools

Malformed call path:

```text
bad tool call
→ repair attempt
→ primary retry
→ fallback model
→ honest error
```

Do not recursively retry forever.

### Memory edge cases

- empty recall → explicitly say there is no relevant historical match
- Hindsight 5xx → retry then degrade
- Hindsight 401/403 → surface configuration failure; do not fabricate memory
- malformed memory payload → reject and log
- duplicate seed → idempotent or detect-and-report
- missing bank → create when appropriate, otherwise fail clearly

### Operator feedback edge cases

- a rejected diagnosis must not become confirmed root cause
- a failed resolution must not become a validated runbook
- an operator correction overrides the model's proposal for that incident
- only confirmed facts enter authoritative memory categories

### Bad alert payloads

Return 4xx with a useful validation message.

### Testing

Backend:

- unit tests for memory adapter
- memory filtering tests
- agent/tool schema tests
- fallback tests
- API validation tests
- operator-feedback lifecycle tests
- retry/degradation tests

Frontend:

- component/unit tests where practical
- production build must pass

E2E:

- Playwright happy path from alert creation to operator confirmation and visible memory trace

CI must execute all relevant layers; a green Python test suite is not sufficient to call the whole
repository green.

---

## 14. Definition of Done

All items below must be true:

- [ ] Clean clone quickstart works from the README.
- [ ] Backend boots and `/health` returns 200.
- [ ] Frontend production build succeeds.
- [ ] CI runs backend lint/tests and frontend build/tests.
- [ ] Playwright happy path passes in CI or in the documented local CI-equivalent command.
- [ ] Memory ON/OFF uses the same alert and same model configuration, with only memory-derived context changed.
- [ ] Memory Inspector clearly shows recall/retain/degraded/no-match states.
- [ ] Hindsight uses a dedicated Memory Bank.
- [ ] Recall scoping uses tags; metadata is used for source/context, not filtering.
- [ ] Operator confirmation/correction is implemented.
- [ ] Unconfirmed model proposals cannot become authoritative resolution/runbook memories.
- [ ] Learning evaluation script exists and produces actual measured results.
- [ ] Teach → replay demonstrates a measurable before/after change on the demo scenario.
- [ ] Seeded NimbusPay history is loaded and visible in the UI.
- [ ] All §13 edge cases have tests.
- [ ] README contains architecture and explicit Hindsight memory explanation.
- [ ] README contains screenshots/GIF and quickstart.
- [ ] Demo script has been rehearsed twice.
- [ ] `v1.0.0` is tagged.
- [ ] No secrets are present in Git history, generated screenshots, CI logs, or tracked files.
- [ ] All normal project issues are closed or explicitly labeled `post-v1`.
- [ ] Then stop feature work and open `CONTENT_GUIDE.md`.

---

## 15. README requirements

The README must:

- describe the product, not the competition/event
- include quickstart
- include architecture diagram
- explain where Hindsight sits
- explain what is retained, recalled, filtered, and why
- explain the operator confirmation boundary
- explain the learning evaluation
- include screenshots/GIF
- reference `.env.example`
- document the memory ON/OFF behavior honestly
- never claim an unmeasured benchmark

---

## 16. Never-do list

- Never commit tokens, `.env`, credentials, or copied secrets.
- Never put a PAT in a Git remote URL.
- Never print a PAT in terminal output or screenshots.
- Never commit under the wrong member identity.
- Never use Hindsight document metadata as if it were recall filtering.
- Never let an unconfirmed LLM resolution become authoritative memory.
- Never claim learning improvement without an actual before/after evaluation run.
- Never invent Hindsight API behavior.
- Never claim a benchmark that did not run.
- Never expand beyond the scope fence during v1.
- Never expose internal chain-of-thought or hidden model reasoning in the UI or content; show concise
  evidence, tool results, memory IDs, and outcome summaries instead.
- Never start the content phase before §14 is fully green.
