"""Unit tests for ``LdapDirectory`` against a stub connection (no real LDAP server needed)."""

import os

import pytest
from ldap3.utils.conv import escape_filter_chars

from app.config import Settings
from app.directory import DirectoryUnavailable, LdapDirectory, make_directory

SETTINGS = Settings(_env_file=None, ldap_user_base="OU=Users,DC=corp,DC=example")


class FakeAttr:
    def __init__(self, values):
        self.values = values

    def __repr__(self):
        return repr(self.values)


def _entry(**attrs) -> dict:
    return {k: FakeAttr(v) for k, v in attrs.items()}


class FakeConnection:
    def __init__(self, bind_ok=True, entries=None, search_raises=None, result_code=0):
        self.bind_ok = bind_ok
        self.entries = entries or []
        self.search_raises = search_raises
        self.result = {
            "result": result_code,
            "description": "success" if not result_code else "error",
        }
        self.unbound = False
        self.last_filter = None

    def bind(self) -> bool:
        return self.bind_ok

    def search(self, base, filter_, attributes=None) -> bool:
        self.last_filter = filter_
        if self.search_raises is not None:
            raise self.search_raises
        # mirrors ldap3's real quirk: False both for zero matches and for a server error
        return self.result["result"] == 0 and bool(self.entries)

    def unbind(self) -> bool:
        self.unbound = True
        return True


def test_filter_values_escaped():
    conn = FakeConnection()
    ld = LdapDirectory(SETTINGS, connect=lambda: conn)

    weird_upn = "weird(user)*@corp.example"
    ld.groups_of(weird_upn)
    assert (
        conn.last_filter
        == f"(&(objectClass=user)(userPrincipalName={escape_filter_chars(weird_upn)}))"
    )  # noqa: E501

    weird_dn = "CN=grp (a1)*,OU=Groups,DC=corp,DC=example"
    ld.members_of(weird_dn)
    assert conn.last_filter == f"(&(objectClass=user)(memberOf={escape_filter_chars(weird_dn)}))"


def test_bind_failure_raises_directory_unavailable():
    conn = FakeConnection(bind_ok=False)
    ld = LdapDirectory(SETTINGS, connect=lambda: conn)
    with pytest.raises(DirectoryUnavailable):
        ld.groups_of("dana@corp.example")
    assert conn.unbound  # cleaned up even on failure


def test_socket_error_raises_directory_unavailable():
    def bad_connect():
        raise OSError("connection refused")

    ld = LdapDirectory(SETTINGS, connect=bad_connect)
    with pytest.raises(DirectoryUnavailable):
        ld.groups_of("dana@corp.example")


def test_unsuccessful_search_without_exception_raises_directory_unavailable():
    """A server error (e.g. insufficientAccessRights, code 50) makes ldap3's ``search`` return
    ``False`` without raising, the same as a legitimate zero-match search; reading that as an
    empty result would misclassify a privileged target (here, an admin) as an ordinary member
    and let a group head authorize a write against them (DR9)."""
    conn = FakeConnection(
        result_code=50, entries=[_entry(memberOf=["CN=admins,DC=corp,DC=example"])]
    )
    ld = LdapDirectory(SETTINGS, connect=lambda: conn)
    with pytest.raises(DirectoryUnavailable):
        ld.groups_of("dana@corp.example")
    assert conn.unbound  # cleaned up even on failure


def test_successful_zero_match_search_is_not_treated_as_an_error():
    """A real zero-match search also returns ``False`` from ``search`` (ldap3's quirk); the
    result code, not the boolean, must decide, or every unknown/no-groups lookup would 503."""
    conn = FakeConnection(result_code=0, entries=[])
    ld = LdapDirectory(SETTINGS, connect=lambda: conn)
    assert ld.groups_of("ghost@corp.example") == []


def test_make_directory_refuses_plain_ldap_url():
    with pytest.raises(RuntimeError):
        make_directory(
            Settings(
                _env_file=None,
                directory_backend="ldap",
                ldap_url="ldap://dc.corp.example",
                ldap_user_base="OU=Users,DC=corp,DC=example",
            )
        )


def test_default_connect_requires_certificate_validation():
    import ssl

    settings = Settings(
        _env_file=None,
        ldap_url="ldaps://dc.corp.example",
        ldap_user_base="OU=Users,DC=corp,DC=example",
    )
    ld = LdapDirectory(settings)
    conn = ld._default_connect()
    assert conn.server.tls.validate == ssl.CERT_REQUIRED


def test_default_connect_never_follows_referrals_and_uses_int_timeouts():
    """A followed ``ldap://`` referral would send the bind password in clear text; ldap3 2.9.1
    also rejects float timeouts on Linux."""
    settings = Settings(
        _env_file=None,
        ldap_url="ldaps://dc.corp.example",
        ldap_user_base="OU=Users,DC=corp,DC=example",
        ldap_timeout_s=2.5,
    )
    conn = LdapDirectory(settings)._default_connect()
    assert conn.auto_referrals is False
    assert isinstance(conn.receive_timeout, int)
    assert isinstance(conn.server.connect_timeout, int)


def test_unbind_failure_is_not_a_server_error_and_does_not_mask_the_result():
    from ldap3.core.exceptions import LDAPSocketCloseError

    class BadUnbind(FakeConnection):
        def unbind(self):
            raise LDAPSocketCloseError("closed")

    ok = BadUnbind(entries=[_entry(memberOf=["CN=g,DC=corp,DC=example"])])
    assert LdapDirectory(SETTINGS, connect=lambda: ok).groups_of("a@corp.example") == [
        "CN=g,DC=corp,DC=example"
    ]
    bad = BadUnbind(bind_ok=False)
    with pytest.raises(DirectoryUnavailable):
        LdapDirectory(SETTINGS, connect=lambda: bad).groups_of("a@corp.example")


def test_members_of_cached_until_ttl():
    calls = []
    entries = [_entry(userPrincipalName=["dana@corp.example"])]

    def connect():
        calls.append(1)
        return FakeConnection(entries=entries)

    now = [0.0]
    ld = LdapDirectory(SETTINGS, connect=connect, now=lambda: now[0])
    dn = "CN=grp-a1,OU=Groups,DC=corp,DC=example"

    assert ld.members_of(dn) == ["dana@corp.example"]
    now[0] += 1
    assert ld.members_of(dn) == ["dana@corp.example"]
    assert len(calls) == 1  # served from cache

    now[0] += SETTINGS.ldap_cache_ttl_s + 1
    assert ld.members_of(dn) == ["dana@corp.example"]
    assert len(calls) == 2  # ttl expired, looked up again


def test_unknown_user_not_cached():
    calls = []

    def connect():
        calls.append(1)
        return FakeConnection(entries=[])

    ld = LdapDirectory(SETTINGS, connect=connect)
    assert ld.display_name("ghost@corp.example") is None
    assert ld.display_name("ghost@corp.example") is None
    assert len(calls) == 2  # a miss is never cached, so it is never stale


def test_make_directory_selects_backend_and_refuses_missing_config():
    from app.directory import FixtureDirectory

    fixture = make_directory(Settings(_env_file=None, directory_backend="fixture"))
    assert isinstance(fixture, FixtureDirectory)

    ldap = make_directory(
        Settings(
            _env_file=None,
            directory_backend="ldap",
            ldap_url="ldaps://dc.corp.example",
            ldap_user_base="OU=Users,DC=corp,DC=example",
        )
    )
    assert isinstance(ldap, LdapDirectory)

    with pytest.raises(RuntimeError):
        make_directory(Settings(_env_file=None, directory_backend="ldap"))


@pytest.mark.parametrize("target", ["real"])
def test_real_ldap(target):
    url = os.environ.get("LDAP_TEST_URL")
    if not url:
        pytest.skip(
            "real target needs LDAP_TEST_URL, LDAP_BIND_DN, LDAP_BIND_PASSWORD, LDAP_USER_BASE"
        )
    settings = Settings(
        _env_file=None,
        ldap_url=url,
        ldap_bind_dn=os.environ.get("LDAP_BIND_DN", ""),
        ldap_bind_password=os.environ.get("LDAP_BIND_PASSWORD", ""),
        ldap_user_base=os.environ["LDAP_USER_BASE"],
    )
    ld = LdapDirectory(settings)
    ld.display_name(os.environ.get("LDAP_TEST_UPN", "nobody@corp.example"))
