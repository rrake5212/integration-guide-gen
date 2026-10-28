"""Registry: curated OAuth providers + the toolkit-name index (provider vocabulary).

Two layers, kept deliberately separate:

* ``Provider`` — the entity that owns the OAuth app / API keys. This is what a guide
  is generated for (Google's Cloud console, GitHub's developer settings, ...).
* ``toolkits`` — Composio's catalogue, used only as a vocabulary of product names
  (``GOOGLESHEETS`` -> Google). Composio is never a fact source for a guide.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    domains: tuple[str, ...]
    aliases: tuple[str, ...]
    slug_prefixes: tuple[str, ...]
    availability: str
    docs: tuple[str, ...]
    console: str | None = None
    discovery: str | None = None
    note: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "domains": list(self.domains),
            "availability": self.availability,
            "docs": list(self.docs),
            "console": self.console,
            "discovery": self.discovery,
            "note": self.note,
        }


def _load_json(name: str) -> dict:
    with open(DATA_DIR / name, encoding="utf-8") as fh:
        return json.load(fh)


def load_providers() -> dict[str, Provider]:
    out: dict[str, Provider] = {}
    for raw in _load_json("providers.json")["providers"]:
        out[raw["id"]] = Provider(
            id=raw["id"],
            name=raw["name"],
            domains=tuple(raw.get("domains", ())),
            aliases=tuple(raw.get("aliases", ())),
            slug_prefixes=tuple(raw.get("slug_prefixes", ())),
            availability=raw["availability"],
            docs=tuple(raw.get("docs", ())),
            console=raw.get("console"),
            discovery=raw.get("discovery"),
            note=raw.get("note"),
        )
    return out


def load_toolkits() -> list[dict]:
    return _load_json("toolkits.json")["toolkits"]


class Registry:
    """In-memory indexes built once per process."""

    def __init__(self) -> None:
        self.providers = load_providers()
        self.toolkits = load_toolkits()

        self.by_alias: dict[str, set[str]] = {}
        for provider in self.providers.values():
            for alias in (provider.name.lower(), *provider.aliases):
                key = " ".join(alias.lower().split())
                self.by_alias.setdefault(key, set()).add(provider.id)

        self.toolkit_by_name: dict[str, str] = {}
        for tk in self.toolkits:
            self.toolkit_by_name.setdefault(tk["name"].lower().strip(), tk["slug"])
            self.toolkit_by_name.setdefault(tk["slug"].lower().strip(), tk["slug"])

    def provider_for_slug(self, slug: str) -> Provider | None:
        """Map a toolkit slug to its OAuth provider by longest registered prefix."""
        best: tuple[int, Provider] | None = None
        for provider in self.providers.values():
            for prefix in provider.slug_prefixes:
                if slug.startswith(prefix):
                    if best is None or len(prefix) > best[0]:
                        best = (len(prefix), provider)
        return best[1] if best else None

    def toolkit_slug(self, text: str) -> str | None:
        return self.toolkit_by_name.get(" ".join(text.lower().split()))

    def provider(self, provider_id: str) -> Provider:
        return self.providers[provider_id]
