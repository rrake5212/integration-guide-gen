"""The provider entry: the durable, auditable answer to "where is the auth page".

Gate E (approved 2026-10-29): a machine proposal is not truth. The finder writes
proposals; a human confirm turns one into the entry's surface and freezes it. Once an
entry is human-confirmed the finder may add proposals but never overwrite the answer —
that is what makes repeat lookups deterministic and makes the 2.4% coverage problem go
away one provider at a time.

File: ``guidegen/data/surfaces.json`` (diffable, committed). Curated ``providers.json``
stays the hand-written seed; this file records what the finder found and what a person
confirmed, with provenance and a timestamp on both.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
# Read-only seed shipped in the image; the writable copy lives on the data volume in a container
# (the image's own tree is root-owned, so writing there fails as uid 10001).
SEED_PATH = DATA_DIR / "surfaces.json"
SURFACES_PATH = Path(os.environ.get("GUIDEGEN_SURFACES") or SEED_PATH)

STATUSES = ("resolved", "needs_review", "unknown", "stale")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Surface:
    kind: str
    docs_url: str
    console_url: str | None = None
    discovery_url: str | None = None
    title: str | None = None
    confidence: str = "medium"
    score: int = 0
    rung: str | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> "Surface":
        return cls(
            kind=raw.get("kind") or "unknown",
            docs_url=raw.get("docs_url") or "",
            console_url=raw.get("console_url"),
            discovery_url=raw.get("discovery_url"),
            title=raw.get("title"),
            confidence=raw.get("confidence") or "medium",
            score=int(raw.get("score") or 0),
            rung=raw.get("rung"),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Entry:
    id: str
    name: str
    availability: str = "unknown"
    domains: list[str] = field(default_factory=list)
    auth_families: list[str] = field(default_factory=list)
    status: str = "needs_review"
    accepted_by: str | None = None
    accepted_at: str | None = None
    surfaces: list[Surface] = field(default_factory=list)
    proposals: list[Surface] = field(default_factory=list)
    find: dict | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def locked(self) -> bool:
        """A human answer is never replaced by a machine proposal."""
        return self.accepted_by == "human"

    @property
    def docs_url(self) -> str | None:
        return self.surfaces[0].docs_url if self.surfaces else None

    @classmethod
    def from_dict(cls, raw: dict) -> "Entry":
        return cls(
            id=raw["id"],
            name=raw.get("name") or raw["id"],
            availability=raw.get("availability") or "unknown",
            domains=list(raw.get("domains") or []),
            auth_families=list(raw.get("auth_families") or []),
            status=raw.get("status") or "needs_review",
            accepted_by=raw.get("accepted_by"),
            accepted_at=raw.get("accepted_at"),
            surfaces=[Surface.from_dict(s) for s in raw.get("surfaces") or []],
            proposals=[Surface.from_dict(s) for s in raw.get("proposals") or []],
            find=raw.get("find"),
            notes=list(raw.get("notes") or []),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "availability": self.availability,
            "domains": list(self.domains), "auth_families": list(self.auth_families),
            "status": self.status, "accepted_by": self.accepted_by, "accepted_at": self.accepted_at,
            "surfaces": [s.to_dict() for s in self.surfaces],
            "proposals": [s.to_dict() for s in self.proposals],
            "find": self.find, "notes": list(self.notes),
        }


def _curated_seed(providers: dict | None = None) -> dict[str, Entry]:
    """The nine hand-written providers are entries too, already human-accepted."""
    raw = providers
    if raw is None:
        path = DATA_DIR / "providers.json"
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"providers": []}
    out: dict[str, Entry] = {}
    for row in raw.get("providers", []):
        surfaces = []
        docs = list(row.get("docs") or [])
        if docs:
            surfaces.append(Surface(kind="oauth_app_registration", docs_url=docs[0],
                                    console_url=row.get("console"), discovery_url=row.get("discovery"),
                                    title=f"{row.get('name')} docs", confidence="high", rung="curated"))
        out[row["id"]] = Entry(
            id=row["id"], name=row["name"], availability=row.get("availability") or "unknown",
            domains=list(row.get("domains") or []), auth_families=[],
            status="resolved" if surfaces else "needs_review", accepted_by="human",
            accepted_at=None, surfaces=surfaces, proposals=[], find=None, notes=["curated seed"],
        )
    return out


class SurfaceStore:
    def __init__(self, path: Path | str | None = None, providers: dict | None = None) -> None:
        # An explicit path means "this store is its own world" (tests, fixtures). Only the
        # default/environment path falls back to the read-only seed shipped in the image.
        self._allow_seed = path is None
        self.path = Path(path) if path else SURFACES_PATH
        self._entries: dict[str, Entry] = _curated_seed(providers)
        self._load_file()
        self._dirty = False

    # ------------------------------------------------------------------ storage
    def _load_file(self) -> None:
        source = self.path
        if not source.exists():
            # first run in a container: start from the packaged seed, write to the volume
            if self._allow_seed and SEED_PATH.exists() and SEED_PATH != self.path:
                source = SEED_PATH
            else:
                return
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for entry_id, row in (raw.get("entries") or {}).items():
            entry = Entry.from_dict({**row, "id": entry_id})
            seeded = self._entries.get(entry_id)
            if seeded and seeded.locked and not entry.surfaces:
                entry.surfaces = seeded.surfaces
                entry.accepted_by = "human"
            self._entries[entry_id] = entry

    def save(self) -> None:
        payload = {"version": 1, "updated_at": _now(),
                   "entries": {k: v.to_dict() for k, v in sorted(self._entries.items())}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
        self._dirty = False

    # ------------------------------------------------------------------- access
    def get(self, entry_id: str) -> Entry | None:
        return self._entries.get(entry_id)

    def all(self) -> dict[str, Entry]:
        return dict(self._entries)

    def by_domain(self, host: str) -> Entry | None:
        host = host.lower()
        best: tuple[int, Entry] | None = None
        for entry in self._entries.values():
            for domain in entry.domains:
                if host == domain or host.endswith("." + domain):
                    if best is None or len(domain) > best[0]:
                        best = (len(domain), entry)
        return best[1] if best else None

    def pending(self, limit: int | None = None) -> list[Entry]:
        """Entries still awaiting a human: unresolved ones AND every machine-accepted one.

        Gate E: an auto-resolved entry is usable, but it is not ground truth until a person
        confirms it. Counting only needs_review/unknown would hide exactly the batch we are
        supposed to review.
        """
        rows = [
            e for e in self._entries.values()
            if e.status in ("needs_review", "unknown") or e.accepted_by == "auto"
        ]
        rows.sort(key=lambda e: (e.accepted_by == "auto", e.status != "needs_review", e.name.lower()))
        return rows[:limit] if limit else rows

    # ------------------------------------------------------------------ mutation
    def propose(self, result, *, entry_id: str | None = None, name: str | None = None,
                domains: list[str] | None = None, auth_families: list[str] | None = None) -> Entry:
        """Record a find result as proposals. Never overwrites a human answer."""
        eid = entry_id or result.brand.lower().replace(" ", "-")
        entry = self._entries.get(eid) or Entry(id=eid, name=name or result.brand)
        entry.name = name or entry.name
        if domains:
            entry.domains = sorted({*entry.domains, *domains})
        if auth_families:
            entry.auth_families = list(auth_families)
        entry.find = result.to_dict()
        entry.proposals = [
            Surface(kind=c.kind, docs_url=c.url, title=c.title, confidence=c.confidence,
                    score=c.score, rung=c.rung)
            for c in result.surfaces if c.verified
        ][:3]
        if result.status == "resolved" and entry.proposals and not entry.locked:
            entry.status = "resolved"
            entry.accepted_by = "auto"
            entry.accepted_at = _now()
            entry.surfaces = [entry.proposals[0]]
        elif not entry.locked:
            entry.status = result.status if result.status in STATUSES else "needs_review"
        entry.notes = list(result.notes)
        self._entries[eid] = entry
        self._dirty = True
        return entry

    def confirm(self, entry_id: str, index: int = 0, *, by: str = "human") -> Entry:
        entry = self._entries.get(entry_id)
        if entry is None:
            raise KeyError(f"no entry {entry_id!r}")
        pool = entry.proposals or entry.surfaces
        if not pool:
            raise ValueError(f"entry {entry_id!r} has no surface to confirm")
        if index < 0 or index >= len(pool):
            raise IndexError(f"proposal {index} out of range (0..{len(pool) - 1})")
        chosen = pool[index]
        entry.surfaces = [Surface(**{**chosen.to_dict(), "confidence": "high"})]
        entry.status = "resolved"
        entry.accepted_by = by
        entry.accepted_at = _now()
        self._dirty = True
        return entry

    def mark_stale(self, entry_id: str, reason: str) -> Entry:
        entry = self._entries[entry_id]
        entry.status = "stale"
        entry.notes = sorted({*entry.notes, reason})
        self._dirty = True
        return entry
