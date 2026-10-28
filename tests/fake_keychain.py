"""A fake OS keychain for tests: the real keychain (and the real app data directory) is never touched by the suite."""
from __future__ import annotations


class FakeKeychain:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.key: bytes | None = None
        self.calls: list[str] = []

    def get(self) -> bytes | None:
        self.calls.append("get")
        return self.key if self.available else None

    def set(self, key: bytes) -> bool:
        self.calls.append("set")
        if not self.available:
            return False
        self.key = key
        return True

    def lose(self) -> None:
        """The keychain entry disappeared (another user, a wiped keyring)."""
        self.key = None
