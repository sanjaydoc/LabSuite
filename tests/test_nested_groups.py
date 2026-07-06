"""AD nested-group resolution -- the transitive closure that drives every ACL."""

from labsuite.active_directory import ActiveDirectory
from labsuite.models import Department, User


def _ad_with(username: str, *direct_groups: str) -> ActiveDirectory:
    ad = ActiveDirectory()
    ad.upsert_user(User(username, username.title(), f"{username}@lab.local", Department.RESEARCH))
    for g in direct_groups:
        ad.set_group_members(g, {username})
    return ad


def test_direct_membership():
    ad = _ad_with("anguyen", "Research")
    assert ad.effective_groups("anguyen") == {"Research"}


def test_single_level_nesting():
    ad = _ad_with("anguyen", "Research")
    ad.nest_group("GPU-Cluster-Users", "Research")  # Research is inside GPU-Cluster-Users
    assert ad.effective_groups("anguyen") == {"Research", "GPU-Cluster-Users"}


def test_multi_level_nesting():
    ad = _ad_with("anguyen", "Research")
    ad.nest_group("GPU-Cluster-Users", "Research")
    ad.nest_group("All-Compute", "GPU-Cluster-Users")
    assert ad.effective_groups("anguyen") == {"Research", "GPU-Cluster-Users", "All-Compute"}


def test_cycle_is_safe():
    ad = _ad_with("anguyen", "A")
    ad.nest_group("A", "B")
    ad.nest_group("B", "A")  # deliberate cycle
    # Must terminate and include both, not loop forever.
    assert ad.effective_groups("anguyen") == {"A", "B"}


def test_inactive_user_has_no_effective_groups():
    ad = _ad_with("anguyen", "Research")
    ad.deactivate_user("anguyen")
    assert ad.effective_groups("anguyen") == set()


def test_members_of_includes_nested():
    ad = _ad_with("anguyen", "Research")
    ad.nest_group("GPU-Cluster-Users", "Research")
    assert "anguyen" in ad.members_of("GPU-Cluster-Users")
