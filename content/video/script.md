# DejaOps demo video — script

Target **3–4 minutes**. Two presenters, screen recording.

Fill in before recording:

- `[PRESENTER A]` — Sujal Suhaas
- `[PRESENTER B]` — Sai Srikar

Every command, endpoint, and UI label below exists in the repository. Nothing here is invented
terminal output — if a run differs, say what actually happened rather than the line written down.

**Before you hit record:** seed the bank, confirm `/health` is 200, and delete `data/store/dejaops.db`
so the incident list starts empty. Have the browser at `http://127.0.0.1:5173` and the terminal
visible.

---

## 1. Intro — 0:00–0:30

**Screen:** the DejaOps UI, empty incident list.

> **[PRESENTER A]:** I'm Sujal. This is DejaOps, an on-call incident agent for a payments company
> that has seen this incident before.
>
> **[PRESENTER B]:** I'm Sai. The claim we want to test is not "it remembers things". It's that
> remembering changes the answer — and that we can show the difference.

**Action:** hover the memory ON/OFF toggle, don't click yet.

---

## 2. The problem — 0:30–1:00

**Screen:** terminal, `data/seed/` directory listing.

> **[PRESENTER B]:** On-call engineers spend the first half hour of an incident rediscovering
> something a colleague solved last month. We generated 52 incidents across eight weeks to give the
> agent a history to learn from.
>
> **[PRESENTER A]:** The trick is that the early occurrences don't contain the answer. First time a
> pattern appears, the diagnosis is inconclusive. Second time, a theory that doesn't hold. From the
> third, a confirmed cause and a validated runbook. So the alert text never carries the cause —
> only the recorded outcomes do.

**Action:** open `data/seed/labels.json`, show that the answer key is a separate file.

> **[PRESENTER B]:** And the expected answers live here, outside the incident. If they were in the
> alert payload they'd end up in the prompt, and we'd be measuring whether the model can copy a
> field.

---

## 3. Live demo — 1:00–3:15

### 3a. Memory OFF — 1:00–1:30

**Action:** select the alert `P1 · checkout-api · checkout-api p99 latency above 2s` from the
**Alert** dropdown. Click **memory off**. Click **Open incident with memory**.

> **[PRESENTER A]:** Same alert we're about to run the other way. Memory off means no recall call
> and no retain call at all — not an empty result.

**Wait** for the run. Point at the proposal card.

> **[PRESENTER B]:** Confidence low, no root cause named, nothing cited. It has no precedent to
> point at, so it says so.

**Action:** scroll to the Memory Inspector.

> **[PRESENTER A]:** And the Inspector says Hindsight was not called. "No memory was consulted" and
> "no relevant memory was found" are different claims, and the UI doesn't blur them.

### 3b. Memory ON — 1:30–2:10

**Action:** click **Re-run with memory on**.

> **[PRESENTER B]:** Same alert, same model, same system prompt. The only thing that changed is
> whether memory is available.

**Wait** for the run.

> **[PRESENTER A]:** Now it names a root cause and cites the memories it reasoned from. That's the
> whole comparison in one click.

**Action:** scroll the Memory Inspector slowly.

> **[PRESENTER B]:** Each recall and retain attempt is listed with its own outcome — how many hits,
> what scope, whether it succeeded. Nothing here is a summary; these are the actual traces.

### 3c. Operator confirmation — 2:10–2:45

**Action:** in **Operator outcome**, choose `DIAGNOSIS_CONFIRMED`. Confirm the root cause is
prefilled. Click **Record diagnosis confirmed**. Then choose `RESOLUTION_CONFIRMED`, pick the
matching runbook, click **Record resolution confirmed**.

> **[PRESENTER A]:** The agent proposed. It cannot confirm. Only this does.

> **[PRESENTER B]:** And watch the diagnosis card stay confirmed after we confirm the resolution.
> That was a real bug — a single outcome field meant the second confirmation reset the first card
> to "proposed" on an incident that was already resolved.

**Action:** point at the timeline.

> **[PRESENTER A]:** Three retains now: the confirmed diagnosis, the confirmed resolution, and the
> runbook entry promoted from it.

### 3d. Teach, then replay — 2:45–3:15

**Action:** select the novel alert, run with memory ON, and show that no matching history is found.
Then confirm the cause and fix, and re-run the same alert.

> **[PRESENTER B]:** This incident is deliberately absent from the seeded history. First run: it has
> nothing to recall, so it can't name the cause.
>
> **[PRESENTER A]:** An operator confirms the actual cause and the fix. That confirmation is what
> writes the memory. Same alert, replayed.

> **[PRESENTER B]:** Now it names the cause. Not because the alert changed — because the system has
> seen the outcome once.

---

## 4. Evidence and takeaway — 3:15–3:50

**Screen:** terminal.

```bash
uv run python scripts/evaluate_learning.py --mode curve --limit 4 --memory-mode on
```

**Action:** run it, point at the `overall` line.

> **[PRESENTER A]:** This is the measured version of what we just showed. Root Cause Hit@1 goes from
> zero at a history cutoff of two, to one from cutoff three on — once a confirmed outcome exists to
> recall.

> **[PRESENTER B]:** Four runs, four cutoffs, one pattern. `n = 1` per cutoff, so it's a ladder, not
> a rate. We're not claiming a benchmark — we're showing the shape.

> **[PRESENTER A]:** The part we'd pass on: a memory demo where the history contains the answer
> proves storage. What makes this one worth watching is that the early occurrences genuinely don't
> know, and the later ones do.

**Screen:** end card with the repository URL.

---

## Five title options

1. DejaOps: an on-call agent that has seen this incident before
2. Memory ON vs OFF: showing an agent actually learn from incident history
3. We measured the learning curve instead of claiming it
4. Building an incident agent that remembers — and proving it
5. Teach, retain, replay: a Hindsight memory demo in three minutes

---

## Recording notes

- Do not show `.env`, API keys, or terminal output containing credentials. The seeding and
  evaluation commands read keys from the environment and print none.
- If the model doesn't propose on a run, say so and re-run rather than editing the footage to
  imply it did. That failure is real and was the subject of a fixed bug.
- If the fallback model is used, the incident records which model answered. Mention it.
