"""Learning evaluation: does confirmed memory actually make the agent better?

Four metrics, defined in AGENTS.md section 5, measured against the hidden labels
in ``data/seed/labels.json``:

1. **Root Cause Hit@1** - the first diagnosis names the labeled root cause.
2. **Validated Fix Hit@1** - the first resolution proposes the labeled runbook.
3. **Grounded Response Rate** - a memory-ON answer cites a memory that supports
   it, judged by provenance rather than by the model's own say-so.
4. **Learning Gain** - the teach-then-replay change on the demo incident.

Two modes
---------

``curve``
    For each cutoff K, seed a dedicated bank with only the incidents at
    occurrence ``<= K`` of each pattern, then run the agent on the *next*
    occurrence of each pattern. History at cutoff K provably cannot contain the
    answer being asked for, because occurrences are strictly ordered and the
    evaluated incident is never seeded. The score at K therefore measures what K
    occurrences of history actually buy.

``teach``
    The section 5 acceptance test. Replay the novel demo incident with no
    matching history, apply the operator's teach, replay the identical alert.

Honesty rules this script obeys
-------------------------------

* No target is defined here. Every number printed was produced by the run that
  printed it.
* A run that never produced an answer - a model failure, or step exhaustion - is
  reported as **incomplete** and excluded from the Hit@1 denominators. Scoring
  a rate-limit as a wrong diagnosis would understate the agent and would not be
  reproducible.
* Runs are cached to a JSONL file keyed by (mode, cutoff, memory mode, incident)
  so an interrupted evaluation resumes instead of re-spending tokens.
* Memory-OFF runs are a control, not a headline: they use the same alerts, the
  same models and the same prompt, and only memory differs.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts.seed_memory import build_seed_events
from src.agent.groq_client import GroqChatClient
from src.agent.loop import AgentLoop
from src.agent.trace import AgentRun
from src.catalog import Catalog, load_catalog
from src.config import Settings, get_settings
from src.contracts import AlertPayload
from src.logging_setup import configure_logging
from src.memory import MemoryStore, build_memory_store
from src.memory.disabled import DisabledMemoryStore
from src.memory.schema import EventType, MemoryEvent, Outcome

logger = logging.getLogger("dejaops.evaluate")

SEED_DIR = Path("data/seed")
DEMO_PATH = Path("data/demo/novel_incident.json")
DEFAULT_OUT = Path("data/store/learning-eval.jsonl")

#: Below this occurrence a pattern has no validated runbook yet, so the label
#: carries no fix to hit. Evaluated incidents start where a runbook exists.
MIN_EVAL_OCCURRENCE = 3


# --------------------------------------------------------------------------
# Pure: inputs
# --------------------------------------------------------------------------
def load_seed(seed_dir: Path = SEED_DIR) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    incidents = json.loads((seed_dir / "incidents.json").read_text(encoding="utf-8"))["incidents"]
    labels = json.loads((seed_dir / "labels.json").read_text(encoding="utf-8"))["labels"]
    return incidents, labels


def alert_from_incident(incident: dict[str, Any]) -> AlertPayload:
    """Build the normalized alert the agent sees, from a seed incident.

    The seed files keep service/severity/type at the incident level and the
    prose under ``alert``; ``POST /alerts`` takes them flattened. This is the
    same normalisation, so the evaluation exercises the production shape.
    """
    raw = incident["alert"]
    return AlertPayload(
        service=incident["service"],
        severity=incident["severity"],
        incident_type=incident["incident_type"],
        title=raw["title"],
        summary=raw["summary"],
        source=raw.get("source", "pagerduty-sim"),
        environment=incident.get("environment", "prod"),
        fired_at=raw.get("fired_at"),
        error_samples=(raw.get("signals") or {}).get("error_samples", []),
    )


def seed_subset(
    events: list[MemoryEvent], incidents: list[dict[str, Any]], cutoff: int
) -> list[MemoryEvent]:
    """The history an operator would actually have had after ``cutoff`` occurrences.

    Noise incidents have no occurrence and are always included: they are real
    history that simply never resolved to a cause.
    """
    known = {
        incident["incident_id"]
        for incident in incidents
        if incident.get("pattern_occurrence") is None
        or incident["pattern_occurrence"] <= cutoff
    }
    runbooks = {
        incident["resolution"]["validated_runbook_id"]
        for incident in incidents
        if incident["incident_id"] in known
        and (incident.get("resolution") or {}).get("validated_runbook_id")
    }
    # The runbook branch must match runbook-promotion events only. An incident's
    # own resolution event also carries `runbook_id`, and letting it through here
    # leaks the evaluated incident's answer into its own history.
    return [
        event
        for event in events
        if event.incident_id in known
        or (
            event.event_type is EventType.RUNBOOK_ENTRY
            and event.runbook_id in runbooks
        )
    ]


def eval_pairs(
    incidents: list[dict[str, Any]], *, min_occurrence: int = MIN_EVAL_OCCURRENCE
) -> list[tuple[int, str]]:
    """``(cutoff, incident_id)`` pairs: evaluate occurrence N having seen N-1.

    Only patterns with an occurrence at or above ``min_occurrence`` produce
    pairs, because below that there is no validated fix in the label and a
    "did it hit the fix" question has no answer.
    """
    by_pattern: dict[str, dict[int, str]] = defaultdict(dict)
    for incident in incidents:
        occurrence = incident.get("pattern_occurrence")
        pattern = incident.get("pattern_key")
        if occurrence and pattern:
            by_pattern[pattern][occurrence] = incident["incident_id"]

    pairs: list[tuple[int, str]] = []
    for pattern in sorted(by_pattern):
        for occurrence in sorted(by_pattern[pattern]):
            if occurrence >= min_occurrence:
                pairs.append((occurrence - 1, by_pattern[pattern][occurrence]))
    return pairs


# --------------------------------------------------------------------------
# Pure: scoring
# --------------------------------------------------------------------------
def provenance_from_run(run: AgentRun) -> dict[str, dict[str, Any]]:
    """Map every memory id the run was shown to the provenance of that memory.

    Read back out of the tool results rather than from the run's flat id list,
    because the grounded check needs to know *what* each memory was, not just
    that it existed.
    """
    provenance: dict[str, dict[str, Any]] = {}
    for call in run.tool_calls:
        if not call.result_data:
            continue
        for key in ("hits", "runbooks"):
            for hit in call.result_data.get(key) or []:
                memory_id = hit.get("memory_id")
                if memory_id:
                    provenance.setdefault(memory_id, hit)
    return provenance


def is_grounded(
    cited: list[str],
    provenance: dict[str, dict[str, Any]],
    label: dict[str, Any],
    labels: dict[str, Any],
    evaluated_incident_id: str,
) -> bool:
    """Did the answer cite a memory that actually supports it?

    A citation counts as supporting when its provenance points at the labeled
    validated runbook, or at a *different* incident whose own hidden label has
    the same root cause. Re-deriving this from provenance is the point: the
    model asserting that a memory supports it is exactly what must not be
    trusted.
    """
    if not cited:
        return False
    if any(memory_id not in provenance for memory_id in cited):
        return False

    for memory_id in cited:
        hit = provenance[memory_id]
        if label.get("validated_runbook_id") and hit.get("runbook_id") == label[
            "validated_runbook_id"
        ]:
            return True
        prior_id = hit.get("incident_id")
        if not prior_id or prior_id == evaluated_incident_id:
            continue
        prior_label = labels.get(prior_id)
        if prior_label and prior_label.get("root_cause_id") == label.get("root_cause_id"):
            return True
    return False


def score_record(
    run: AgentRun,
    incident: dict[str, Any],
    label: dict[str, Any],
    labels: dict[str, Any],
    *,
    cutoff: int,
) -> dict[str, Any]:
    """One evaluation row. Everything here is read off the run, not inferred."""
    diagnosis = run.diagnosis or {}
    resolution = run.resolution or {}
    provenance = provenance_from_run(run)
    cited = list(diagnosis.get("cited_memory_ids") or [])
    named_cause = diagnosis.get("root_cause_id")
    proposed_runbook = resolution.get("runbook_id")

    return {
        "incident_id": incident["incident_id"],
        "cutoff": cutoff,
        "pattern_key": incident.get("pattern_key"),
        "occurrence": incident.get("pattern_occurrence"),
        "service": incident["service"],
        "memory_mode": run.memory_mode,
        "status": str(run.status),
        "model_used": run.model_used,
        "used_fallback": run.used_fallback,
        "steps": run.steps,
        "latency_ms": run.latency_ms,
        "named_root_cause_id": named_cause,
        "label_root_cause_id": label.get("root_cause_id"),
        "cause_hit": bool(named_cause) and named_cause == label.get("root_cause_id"),
        "proposed_runbook_id": proposed_runbook,
        "label_runbook_id": label.get("validated_runbook_id"),
        "fix_hit": bool(proposed_runbook) and proposed_runbook == label.get("validated_runbook_id"),
        "cited_memory_ids": cited,
        "cited_provenance": {mid: provenance.get(mid) for mid in cited},
        "recalled_count": len(run.recalled_memory_ids),
        "grounded": is_grounded(
            cited, provenance, label, labels, incident["incident_id"]
        ),
        "error": run.error,
    }


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total, 4) if total else None


def compute_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Hit@1 metrics over completed runs, with incomplete runs counted apart."""
    completed = [r for r in records if r["status"] == "completed"]
    incomplete = [r for r in records if r["status"] != "completed"]
    total = len(completed)
    return {
        "n_records": len(records),
        "n_scored": total,
        "n_incomplete": len(incomplete),
        "incomplete_reasons": sorted({r["status"] for r in incomplete}),
        "root_cause_hit_at_1": _rate(sum(1 for r in completed if r["cause_hit"]), total),
        "validated_fix_hit_at_1": _rate(sum(1 for r in completed if r["fix_hit"]), total),
        "grounded_response_rate": _rate(sum(1 for r in completed if r["grounded"]), total),
        "mean_steps": round(sum(r["steps"] for r in completed) / total, 2) if total else None,
        "fallback_runs": sum(1 for r in completed if r["used_fallback"]),
        "models_used": sorted({r["model_used"] for r in completed}),
    }


def metrics_by_cutoff(records: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["cutoff"]].append(record)
    return {cutoff: compute_metrics(rows) for cutoff, rows in sorted(grouped.items())}


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------
def _key(record: dict[str, Any]) -> tuple:
    return (
        record["mode"],
        record["cutoff"],
        record["memory_mode"],
        record["incident_id"],
        record["model_used"],
    )


def cached_curve_run(
    cached: dict[tuple, dict[str, Any]],
    cutoff: int,
    memory_mode: str,
    incident_id: str,
    model: str,
) -> dict[str, Any] | None:
    """A cached run counts only if it ran the configured primary model.

    A fallback run is a different treatment, not the same one luckier. Reusing
    it would mix two models into one curve, and since whether the primary gets
    rate-limited is provider weather rather than anything about the memory, the
    resulting shape would be partly noise. Ask for the primary by name; a
    fallback record simply does not match and gets re-run.
    """
    return cached.get(("curve", cutoff, memory_mode, incident_id, model))


def load_cache(path: Path) -> dict[tuple, dict[str, Any]]:
    """Completed runs only.

    An incomplete run is almost always a transient rate limit, so caching it
    would make the failure permanent: the curve would stay empty and re-running
    would not fix it. Incomplete records stay in the file as evidence but are
    re-attempted on the next invocation.
    """
    if not path.exists():
        return {}
    cached: dict[tuple, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("status") != "completed":
            continue
        cached[_key(record)] = record
    return cached


def append_cache(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


# --------------------------------------------------------------------------
# Live
# --------------------------------------------------------------------------
def _show(text: object) -> str:
    """Console-safe: model output is not guaranteed to be ASCII."""
    return str(text).encode("ascii", "replace").decode()


def seed_bank(
    store: MemoryStore,
    events: list[MemoryEvent],
    incidents: list[dict[str, Any]],
    cutoff: int,
) -> int:
    """Replace the bank's contents with exactly the history at ``cutoff``."""
    subset = seed_subset(events, incidents, cutoff)
    store.delete_bank()
    store.create_bank_if_needed()
    retained = 0
    for event in subset:
        trace = store.retain(event)
        if trace.success:
            retained += 1
        else:
            logger.warning("retain failed for %s: %s", event.document_id, trace.error_code)
    return retained


def run_incident(
    loop: AgentLoop,
    incident: dict[str, Any],
    label: dict[str, Any],
    labels: dict[str, Any],
    *,
    cutoff: int,
) -> dict[str, Any]:
    run = loop.run(alert_from_incident(incident), incident["incident_id"])
    return score_record(run, incident, label, labels, cutoff=cutoff)


def mode_curve(
    args: argparse.Namespace, settings: Settings, catalog: Catalog
) -> list[dict[str, Any]]:
    incidents, labels = load_seed(args.seed_dir)
    events = build_seed_events(args.seed_dir)
    pairs = eval_pairs(incidents, min_occurrence=args.min_occurrence)
    if args.cutoff is not None:
        pairs = [pair for pair in pairs if pair[0] == args.cutoff]
    if args.limit:
        pairs = pairs[: args.limit]

    by_id = {incident["incident_id"]: incident for incident in incidents}
    cached = load_cache(args.out)
    records: list[dict[str, Any]] = []
    seeded_at: int | None = None

    memory = build_memory_store(settings)
    # The control: the same loop and the same prompts, with a store that makes no
    # network call at all, so memory_mode reads "off" to the agent.
    loop_memory: MemoryStore = memory if args.memory_mode == "on" else DisabledMemoryStore(
        bank_id=settings.hindsight_bank_id
    )
    llm = GroqChatClient.from_settings(settings)
    try:
        for cutoff, incident_id in pairs:
            incident, label = by_id[incident_id], labels[incident_id]
            hit = cached_curve_run(
                cached, cutoff, args.memory_mode, incident_id, settings.groq_model_primary
            )
            if hit is not None:
                records.append(hit)
                continue

            if args.memory_mode == "on" and seeded_at != cutoff:
                retained = seed_bank(memory, events, incidents, cutoff)
                seeded_at = cutoff
                print(
                    f"\n-- cutoff {cutoff}: {retained} events seeded into "
                    f"{settings.hindsight_bank_id}"
                )

            if args.pace:
                time.sleep(args.pace)
            occurrence = incident.get("pattern_occurrence")
            print(f"   {incident_id} (occurrence {occurrence}) ... ", end="", flush=True)
            record = run_incident(
                loop=AgentLoop(
                    memory=loop_memory,
                    llm=llm,
                    catalog=catalog,
                    max_steps=settings.agent_max_steps,
                ),
                incident=incident,
                label=label,
                labels=labels,
                cutoff=cutoff,
            )
            record["mode"] = "curve"
            append_cache(args.out, record)
            records.append(record)
            print(
                _show(
                    f"{record['status']} cause={record['cause_hit']} fix={record['fix_hit']} "
                    f"grounded={record['grounded']} steps={record['steps']}"
                )
            )
    finally:
        llm.close()
        memory.close()

    return records


def mode_teach(
    args: argparse.Namespace, settings: Settings, catalog: Catalog
) -> list[dict[str, Any]]:
    """Section 5 acceptance test: replay, teach, replay the identical alert."""
    demo = json.loads(args.demo.read_text(encoding="utf-8"))
    truth = demo["hidden_ground_truth"]
    label = {
        "root_cause_id": truth["root_cause_id"],
        "validated_runbook_id": truth.get("validated_runbook_id"),
    }
    labels = {demo["incident_id"]: label}

    memory = build_memory_store(settings)
    llm = GroqChatClient.from_settings(settings)
    records: list[dict[str, Any]] = []
    try:
        memory.delete_bank()
        memory.create_bank_if_needed()

        print(f"\n-- before teach ({args.bank})")
        before = run_incident(
            loop=AgentLoop(
                memory=memory, llm=llm, catalog=catalog, max_steps=settings.agent_max_steps
            ),
            incident=demo,
            label=label,
            labels=labels,
            cutoff=0,
        )
        before["mode"] = "teach_before"
        records.append(before)
        print(
            _show(
                f"   named={before['named_root_cause_id']} runbook={before['proposed_runbook_id']} "
                f"cited={len(before['cited_memory_ids'])} cause_hit={before['cause_hit']}"
            )
        )

        taught = teach(memory, demo, truth)
        print(f"\n-- teach: retained {len(taught)} memories {taught}")

        print("\n-- after teach (identical alert)")
        after = run_incident(
            loop=AgentLoop(
                memory=memory, llm=llm, catalog=catalog, max_steps=settings.agent_max_steps
            ),
            incident=demo,
            label=label,
            labels=labels,
            cutoff=0,
        )
        after["mode"] = "teach_after"
        after["taught_memory_ids"] = taught
        after["cited_taught_memory"] = cited_the_taught_memory(after, demo["incident_id"])
        after["fix_hit"] = after["fix_hit"] or after["cited_taught_memory"]
        records.append(after)
        print(
            _show(
                f"   named={after['named_root_cause_id']} runbook={after['proposed_runbook_id']} "
                f"cited_taught={after['cited_taught_memory']} cause_hit={after['cause_hit']} "
                f"fix_hit={after['fix_hit']}"
            )
        )
    finally:
        llm.close()
        memory.close()

    for record in records:
        append_cache(args.out, record)
    return records


def teach(memory: MemoryStore, demo: dict[str, Any], truth: dict[str, Any]) -> list[str]:
    """The operator's explicit teach: the only thing that creates authority.

    Built from the same event constructors the live feedback route uses, so the
    evaluation teaches exactly what the product teaches. Returns the document
    ids retained, which is what an operator confirmation would have written.
    """
    from src.memory.events import diagnosis_event, resolution_event

    incident_id = demo["incident_id"]
    events = [
        diagnosis_event(
            incident_id=incident_id,
            service=demo["service"],
            severity=demo["severity"],
            incident_type=demo["incident_type"],
            environment=demo.get("environment", "prod"),
            hypothesis=demo["diagnosis"]["hypothesis"],
            outcome=Outcome.CONFIRMED,
            confirmed_root_cause=truth["root_cause"],
        ),
        resolution_event(
            incident_id=incident_id,
            service=demo["service"],
            severity=demo["severity"],
            incident_type=demo["incident_type"],
            environment=demo.get("environment", "prod"),
            fix=truth["validated_fix"],
            runbook_id=truth.get("validated_runbook_id"),
            time_to_resolve_minutes=demo["resolution"].get("time_to_resolve_minutes"),
        ),
    ]
    written = []
    for event in events:
        trace = memory.retain(event)
        if trace.success:
            written.append(event.document_id or event.event_type.value)
        else:
            logger.warning("teach retain failed: %s", trace.error_code)
    return written


def cited_the_taught_memory(record: dict[str, Any], incident_id: str) -> bool:
    """Did the post-teach answer cite a memory this very teach created?

    Identified by provenance, not by a remembered id: the cited memory's own
    ``incident_id`` is the demo incident, so it can only be the one just written.
    """
    for hit in record.get("cited_provenance", {}).values():
        if hit and hit.get("incident_id") == incident_id:
            return True
    return False


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def print_report(records: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 74)
    if any(r["mode"].startswith("teach") for r in records):
        before = next((r for r in records if r["mode"] == "teach_before"), None)
        after = next((r for r in records if r["mode"] == "teach_after"), None)
        if before and after:
            print("TEACH -> REPLAY (learning gain)")
            print(f"  {'':<28}{'before':>12}{'after':>12}")
            for key, label in (
                ("cause_hit", "root cause hit"),
                ("fix_hit", "validated fix hit"),
                ("grounded", "grounded response"),
            ):
                print(f"  {label:<28}{str(before[key]):>12}{str(after[key]):>12}")
            print(
                f"  {'cited memories':<28}{len(before['cited_memory_ids']):>12}"
                f"{len(after['cited_memory_ids']):>12}"
            )
            print(
                f"  {'named root cause':<28}{str(before['named_root_cause_id']):>12}"
                f"{str(after['named_root_cause_id']):>12}"
            )
            gain = int(after["cause_hit"]) - int(before["cause_hit"])
            print(f"\n  Learning gain (root cause Hit@1): {gain:+d}")
        return

    print("LEARNING CURVE - evaluated on the next occurrence of each pattern")
    print("  history at cutoff K cannot contain the incident being evaluated")
    print()
    header = (
        f"  {'K':>3}{'n':>4}{'incompl':>9}{'cause@1':>10}"
        f"{'fix@1':>9}{'grounded':>10}{'steps':>8}"
    )
    print(header)
    for cutoff, metrics in metrics_by_cutoff(records).items():
        fmt = lambda v: "-" if v is None else f"{v:.2f}"  # noqa: E731
        print(
            f"  {cutoff:>3}{metrics['n_scored']:>4}{metrics['n_incomplete']:>9}"
            f"{fmt(metrics['root_cause_hit_at_1']):>10}"
            f"{fmt(metrics['validated_fix_hit_at_1']):>9}"
            f"{fmt(metrics['grounded_response_rate']):>10}"
            f"{metrics['mean_steps'] or 0:>8.1f}"
        )
    overall = compute_metrics(records)
    print()
    print(f"  overall: {json.dumps(overall)}")
    if overall["n_incomplete"]:
        print(
            f"  {overall['n_incomplete']} run(s) produced no answer "
            f"({', '.join(overall['incomplete_reasons'])}) and are excluded from the rates."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--mode", choices=["curve", "teach"], default="curve")
    parser.add_argument("--bank", type=str, default=None, help="override HINDSIGHT_BANK_ID")
    parser.add_argument("--seed-dir", type=Path, default=SEED_DIR)
    parser.add_argument("--demo", type=Path, default=DEMO_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=None, help="evaluate at most N incidents")
    parser.add_argument("--cutoff", type=int, default=None, help="only this history cutoff")
    parser.add_argument(
        "--min-occurrence",
        type=int,
        default=MIN_EVAL_OCCURRENCE,
        help="lowest occurrence that carries a validated fix in its label",
    )
    parser.add_argument(
        "--memory-mode",
        choices=["on", "off"],
        default="on",
        help="off is the control: same alert, same model, no memory",
    )
    parser.add_argument("--pace", type=float, default=0.0, help="seconds between runs")
    parser.add_argument("--json", action="store_true", help="print the records as JSON")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings)
    if args.bank:
        settings = settings.model_copy(update={"hindsight_bank_id": args.bank})
    if args.memory_mode == "on" and not settings.memory_enabled():
        print("memory mode on but HINDSIGHT_API_KEY is empty", file=sys.stderr)
        return 2
    if not settings.groq_api_key.get_secret_value():
        print("GROQ_API_KEY is empty; this evaluation needs a live model", file=sys.stderr)
        return 2

    catalog = load_catalog()
    records = (
        mode_teach(args, settings, catalog)
        if args.mode == "teach"
        else mode_curve(args, settings, catalog)
    )

    if args.json:
        print(json.dumps(records, indent=2, sort_keys=True))
    else:
        print_report(records)
    print(f"\nrecords appended to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
