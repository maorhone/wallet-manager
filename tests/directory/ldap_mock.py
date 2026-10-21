"""An in-memory ldap3 ``MOCK_SYNC`` server backed by ``seed/dev.yaml`` (SPEC §16): no real LDAP
server is needed to contract-test ``LdapDirectory`` against the same dataset as the fixture."""

import secrets
from collections.abc import Callable
from pathlib import Path

import yaml
from ldap3 import MOCK_SYNC, OFFLINE_AD_2012_R2, Connection, Server

BIND_DN = "CN=wm-svc,OU=Service Accounts,DC=corp,DC=example"


def mock_connect(yaml_path: str | Path) -> Callable[[], Connection]:
    """Build the mock server once and return a ``connect()`` factory of fresh connections to it."""
    password = secrets.token_urlsafe(16)
    server = Server("mock.corp.example", get_info=OFFLINE_AD_2012_R2)
    seed = Connection(server, user=BIND_DN, password=password, client_strategy=MOCK_SYNC)
    seed.strategy.add_entry(BIND_DN, {"userPassword": password, "sAMAccountName": "wm-svc"})

    data = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
    for u in data.get("users", []):
        dn = f"CN={u['upn'].split('@')[0]},OU=Users,DC=corp,DC=example"
        attrs = {"objectClass": "user", "userPrincipalName": u["upn"]}
        if u.get("display_name"):
            attrs["displayName"] = u["display_name"]
        if u.get("groups"):
            attrs["memberOf"] = list(u["groups"])
        seed.strategy.add_entry(dn, attrs)

    def connect() -> Connection:
        return Connection(server, user=BIND_DN, password=password, client_strategy=MOCK_SYNC)

    return connect
