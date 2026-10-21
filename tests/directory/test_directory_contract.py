"""§16 Phase 3 exit criterion: fixture and ldap3 implementations of ``Directory`` agree on the
same `seed/dev.yaml` dataset (known user, unknown user, a known user with no groups)."""

import pytest

from app.config import Settings
from app.directory import FixtureDirectory, LdapDirectory

from .ldap_mock import mock_connect

SEED = "seed/dev.yaml"
KNOWN, NO_GROUPS, UNKNOWN = "dani@corp.example", "lost@corp.example", "ghost@corp.example"
GROUP_A1_DN = "CN=grp-a1,OU=Groups,DC=corp,DC=example"


@pytest.fixture(params=["fixture", "ldap"])
def directory(request):
    if request.param == "fixture":
        return FixtureDirectory(SEED)
    settings = Settings(_env_file=None, ldap_user_base="OU=Users,DC=corp,DC=example")
    return LdapDirectory(settings, connect=mock_connect(SEED))


async def test_contract_groups_of(directory):
    assert directory.groups_of(KNOWN) == ["CN=grp-a1,OU=Groups,DC=corp,DC=example"]
    assert directory.groups_of(NO_GROUPS) == []
    assert directory.groups_of(UNKNOWN) == []


async def test_contract_members_of(directory):
    assert sorted(directory.members_of(GROUP_A1_DN)) == [
        "dana@corp.example",
        "dani@corp.example",
        "twin@corp.example",
        "yael@corp.example",
    ]
    assert directory.members_of("CN=no-such-group,OU=Groups,DC=corp,DC=example") == []


async def test_contract_display_name(directory):
    assert directory.display_name(KNOWN) == "Dani Cohen"
    assert directory.display_name(NO_GROUPS) == "Lior Lost"
    assert directory.display_name(UNKNOWN) is None
