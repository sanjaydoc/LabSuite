"""TrueNAS share access resolution."""

from labsuite.models import AccessLevel
from labsuite.truenas import TrueNAS


def _nas() -> TrueNAS:
    nas = TrueNAS()
    nas.add_share("research-data", "tank/research")
    nas.grant("research-data", "Research", AccessLevel.MODIFY)
    nas.grant("research-data", "Platform", AccessLevel.READ)
    nas.grant("research-data", "Domain-Admins", AccessLevel.FULL)
    return nas


def test_strongest_grant_wins():
    nas = _nas()
    level, via = nas.level_for("research-data", {"Research", "Platform"})
    assert level == AccessLevel.MODIFY
    assert via == ["Research"]


def test_no_matching_group_is_no_access():
    nas = _nas()
    level, via = nas.level_for("research-data", {"Legal"})
    assert level == AccessLevel.NONE
    assert via == []


def test_full_access_via_admin():
    nas = _nas()
    level, _ = nas.level_for("research-data", {"Domain-Admins"})
    assert level == AccessLevel.FULL


def test_shares_for_lists_only_reachable():
    nas = _nas()
    nas.add_share("legal", "tank/legal")
    nas.grant("legal", "Legal", AccessLevel.FULL)
    reachable = nas.shares_for({"Research"})
    assert reachable == {"research-data": AccessLevel.MODIFY}


def test_roundtrip_serialisation():
    nas = _nas()
    restored = TrueNAS.from_dict(nas.to_dict())
    assert restored.level_for("research-data", {"Research"})[0] == AccessLevel.MODIFY
