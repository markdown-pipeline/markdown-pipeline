"""A fake connector used only in tests (MD-45): in-memory items, scriptable changes, deletions and failures.

It implements the source contract (``mdpipeline.sources``) and registers itself as source type ``fake``. A pipeline source of type
``fake`` has the option ``world`` (the key of a ``FakeWorld`` registered with ``FakeWorld.register``)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from mdpipeline.config import Source
from mdpipeline.sources import SourceEntry, SourceScan, register_source_type

WORLDS: dict[str, "FakeWorld"] = {}
BROKEN: set[str] = set()          # identities whose content cannot be produced (a conversion error)


@dataclass
class Item:
    identity: str
    relpath: str
    text: str
    version: str = "v1"
    created: float = 1_700_000_000.0
    modified: float = 1_700_000_000.0
    extra: dict = field(default_factory=dict)


class FakeWorld:
    def __init__(self, name: str = "w") -> None:
        self.name, self.items, self.fail_with = name, {}, None
        self.content_reads: list[str] = []
        self.scans = 0
        self.guard_empty = True
        WORLDS[name] = self

    def put(self, identity: str, relpath: str, text: str, version: str | None = None, created: float | None = None, **extra) -> Item:
        old = self.items.get(identity)
        item = Item(identity, relpath, text, version or (f"v{int(old.version[1:]) + 1}" if old else "v1"),
                    created if created is not None else (old.created if old else 1_700_000_000.0), extra=extra)
        self.items[identity] = item
        return item

    def remove(self, identity: str) -> None:
        self.items.pop(identity, None)

    def fail(self, error: str | Exception | None) -> None:
        self.fail_with = error

    def source(self, label: str = "fake") -> Source:
        return Source("fake://world", label, type="fake", options={"world": self.name})


class FakeSource:
    def __init__(self, source: Source) -> None:
        self.source = source
        self.world = WORLDS[source.options["world"]]

    def scan(self, include, exclude, known=None) -> SourceScan:
        w, src = self.world, self.source
        w.scans += 1
        result = SourceScan(label=src.label, path=src.path, kind="fake", guard_empty=w.guard_empty)
        if w.fail_with:
            if isinstance(w.fail_with, Exception):
                raise w.fail_with
            result.status, result.error = "unreadable", str(w.fail_with)
            return result
        for item in sorted(w.items.values(), key=lambda i: i.identity):
            def read(item=item) -> bytes:
                if item.identity in BROKEN:
                    raise ValueError("cannot convert this item")
                w.content_reads.append(item.identity)
                return item.text.encode()

            result.entries.append(SourceEntry(item.relpath, "", item.modified, len(item.text), identity=item.identity, created=item.created,
                                              version=item.version, provenance={"fake_id": item.identity, **item.extra}, content=read))
        return result


register_source_type("fake", FakeSource)


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
