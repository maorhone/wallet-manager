import ssl
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import yaml
from ldap3 import ALL, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.core.results import RESULT_SUCCESS
from ldap3.utils.conv import escape_filter_chars

from app.config import Settings


def _is_successful(conn: Connection) -> bool:
    """``Connection.search`` returns ``False`` both on a zero-match search and on a server
    error (ldap3's boolean conflates "no entries" with "failed"); the LDAP result code is the
    only reliable signal, so a search error cannot be read back as an authoritative empty
    result and misclassify a privileged target as an ordinary member (DR9)."""
    return conn.result.get("result") == RESULT_SUCCESS


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


class LdapDirectory:
    """Phase 3 directory backed by LDAPS (SPEC §5.1, §9.1): one bind + search per lookup, no sync.

    ``connect`` returns a fresh, unbound ``Connection``; this class binds, searches and unbinds it.
    Any ``LDAPException`` or ``OSError`` (a failed bind included) becomes ``DirectoryUnavailable``,
    with a static message so bind credentials never reach it.
    """

    def __init__(
        self,
        settings: Settings,
        connect: Callable[[], Connection] | None = None,
        now: Callable[[], float] = time.monotonic,
    ):
        self._settings = settings
        self._connect = connect or self._default_connect
        self._now = now
        self._members_cache: dict[str, tuple[float, list[str]]] = {}
        self._name_cache: dict[str, str] = {}

    def _default_connect(self) -> Connection:
        # LDAPS only (enforced in make_directory); CERT_REQUIRED stops an impersonated server
        # from capturing the bind password or supplying forged, privileged role memberships.
        server = Server(
            self._settings.ldap_url,
            get_info=ALL,
            connect_timeout=self._settings.ldap_timeout_s,
            tls=Tls(validate=ssl.CERT_REQUIRED),
        )
        return Connection(
            server,
            user=self._settings.ldap_bind_dn,
            password=self._settings.ldap_bind_password,
            receive_timeout=self._settings.ldap_timeout_s,
        )

    def _search(self, filter_: str, attributes: list[str]) -> list:
        try:
            conn = self._connect()
        except (LDAPException, OSError) as exc:
            raise DirectoryUnavailable("directory connection failed") from exc
        try:
            if not conn.bind():
                raise DirectoryUnavailable("directory bind failed")
            conn.search(self._settings.ldap_user_base, filter_, attributes=attributes)
            if not _is_successful(conn):
                raise DirectoryUnavailable("directory search failed")
            return list(conn.entries)
        except (LDAPException, OSError) as exc:
            raise DirectoryUnavailable("directory search failed") from exc
        finally:
            conn.unbind()

    def groups_of(self, upn: str) -> list[str]:
        """Direct ``memberOf`` only; not cached, since this is the one-time onboarding read."""
        safe = escape_filter_chars(upn)
        entries = self._search(f"(&(objectClass=user)(userPrincipalName={safe}))", ["memberOf"])
        return [str(v) for v in entries[0]["memberOf"].values] if entries else []

    def members_of(self, dn: str) -> list[str]:
        now = self._now()
        cached = self._members_cache.get(dn)
        if cached is not None and cached[0] > now:
            return cached[1]
        safe = escape_filter_chars(dn)
        entries = self._search(f"(&(objectClass=user)(memberOf={safe}))", ["userPrincipalName"])
        upns = sorted({str(v) for e in entries for v in e["userPrincipalName"].values})
        self._members_cache[dn] = (now + self._settings.ldap_cache_ttl_s, upns)
        return upns

    def display_name(self, upn: str) -> str | None:
        """Only a found name is cached, so ``not_in_directory`` is never stale."""
        if upn in self._name_cache:
            return self._name_cache[upn]
        safe = escape_filter_chars(upn)
        entries = self._search(f"(&(objectClass=user)(userPrincipalName={safe}))", ["displayName"])
        if not entries or not entries[0]["displayName"].values:
            return None
        name = str(entries[0]["displayName"].values[0])
        self._name_cache[upn] = name
        return name


def make_directory(settings: Settings) -> Directory:
    if settings.directory_backend == "ldap":
        if not settings.ldap_url or not settings.ldap_user_base:
            raise RuntimeError(
                "ldap_url and ldap_user_base are required for directory_backend=ldap"
            )
        if not settings.ldap_url.lower().startswith("ldaps://"):
            raise RuntimeError("ldap_url must use ldaps:// so bind credentials stay encrypted")
        return LdapDirectory(settings)
    return FixtureDirectory(settings.seed_file)
