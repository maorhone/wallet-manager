from pathlib import Path
from typing import Protocol

import yaml


class DirectoryUnavailable(Exception):
    """The directory could not be read (SPEC §5.4); onboarding answers 503 and writes nothing."""


class Directory(Protocol):
    def groups_of(self, upn: str) -> list[str]: ...

    def members_of(self, dn: str) -> list[str]: ...

    def display_name(self, upn: str) -> str | None: ...


class FixtureDirectory:
    """Phase 1 directory backed by seed/dev.yaml (LDAP arrives in Phase 3)."""

    def __init__(self, path: str | Path):
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        self.users = {u["upn"]: u for u in data.get("users", [])}

    def groups_of(self, upn: str) -> list[str]:
        return list(self.users.get(upn, {}).get("groups", []))

    def members_of(self, dn: str) -> list[str]:
        return [upn for upn, u in self.users.items() if dn in u.get("groups", [])]

    def display_name(self, upn: str) -> str | None:
        return self.users.get(upn, {}).get("display_name")
