"""Read-only catalog of the fictional NimbusPay estate.

Backs the ``get_service_map`` tool and runbook lookups. Loaded once per seed
directory and cached, because the seed files do not change at runtime.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DEFAULT_SEED_DIR = Path("data/seed")


@dataclass(frozen=True)
class Service:
    name: str
    tier: int
    owner: str
    dependencies: tuple[str, ...] = ()
    slo: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "tier": self.tier,
            "owner": self.owner,
            "dependencies": list(self.dependencies),
            "slo": self.slo,
        }


@dataclass(frozen=True)
class RootCause:
    id: str
    name: str
    summary: str
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "name": self.name, "summary": self.summary, "detail": self.detail}


@dataclass(frozen=True)
class Runbook:
    id: str
    title: str
    root_cause_id: str
    steps: tuple[str, ...] = ()
    verified: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "root_cause_id": self.root_cause_id,
            "steps": list(self.steps),
            "verified": self.verified,
        }


@dataclass(frozen=True)
class Catalog:
    company: str
    services: tuple[Service, ...] = ()
    root_causes: tuple[RootCause, ...] = ()
    runbooks: tuple[Runbook, ...] = ()
    _service_index: dict[str, Service] = field(default_factory=dict, repr=False)
    _runbook_index: dict[str, Runbook] = field(default_factory=dict, repr=False)
    _root_cause_index: dict[str, RootCause] = field(default_factory=dict, repr=False)

    def service(self, name: str) -> Service | None:
        return self._service_index.get(name)

    def runbook(self, runbook_id: str) -> Runbook | None:
        return self._runbook_index.get(runbook_id)

    def root_cause(self, root_cause_id: str) -> RootCause | None:
        return self._root_cause_index.get(root_cause_id)

    def service_names(self) -> list[str]:
        return [service.name for service in self.services]

    def service_map(self, service: str | None = None) -> dict[str, object]:
        """Whole estate, or one service with its dependencies resolved."""
        if service is None:
            return {
                "company": self.company,
                "services": [item.to_dict() for item in self.services],
            }

        found = self.service(service)
        if found is None:
            return {
                "company": self.company,
                "requested": service,
                "found": False,
                "known_services": self.service_names(),
            }
        return {
            "company": self.company,
            "requested": service,
            "found": True,
            "service": found.to_dict(),
            "dependencies": [
                self._service_index[dep].to_dict()
                for dep in found.dependencies
                if dep in self._service_index
            ],
        }


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_catalog(seed_dir: Path | str = DEFAULT_SEED_DIR) -> Catalog:
    seed_dir = Path(seed_dir)
    services_payload = _load_json(seed_dir / "services.json")
    runbooks_payload = _load_json(seed_dir / "runbooks.json")
    root_causes_payload = _load_json(seed_dir / "root_causes.json")

    services = tuple(
        Service(
            name=item["name"],
            tier=int(item.get("tier", 3)),
            owner=item.get("owner", "unknown"),
            dependencies=tuple(item.get("dependencies", ())),
            slo=item.get("slo", ""),
        )
        for item in services_payload["services"]
    )
    runbooks = tuple(
        Runbook(
            id=item["id"],
            title=item["title"],
            root_cause_id=item["root_cause_id"],
            steps=tuple(item.get("steps", ())),
            verified=bool(item.get("verified", False)),
        )
        for item in runbooks_payload["runbooks"]
    )
    root_causes = tuple(
        RootCause(
            id=item["id"],
            name=item["name"],
            summary=item["summary"],
            detail=item.get("detail", ""),
        )
        for item in root_causes_payload["root_causes"]
    )

    return Catalog(
        company=services_payload.get("company", "NimbusPay"),
        services=services,
        root_causes=root_causes,
        runbooks=runbooks,
        _service_index={service.name: service for service in services},
        _runbook_index={runbook.id: runbook for runbook in runbooks},
        _root_cause_index={cause.id: cause for cause in root_causes},
    )


@lru_cache(maxsize=4)
def get_catalog(seed_dir: str = str(DEFAULT_SEED_DIR)) -> Catalog:
    return load_catalog(seed_dir)
