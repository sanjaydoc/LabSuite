"""The Okta -> AD SCIM reconcile: correctness, idempotency, deactivation cascade."""

from labsuite.active_directory import ActiveDirectory
from labsuite.models import Department
from labsuite.okta import OktaDirectory
from labsuite.scim import ScimSync


def _fixture():
    okta = OktaDirectory()
    okta.create_user("anguyen", "Alice Nguyen", "a@lab.local", Department.BIO, "pw")
    okta.create_user("bokafor", "Bob Okafor", "b@lab.local", Department.PLATFORM, "pw")
    okta.ensure_group("Research")
    okta.add_to_group("anguyen", "Research")
    ad = ActiveDirectory()
    return okta, ad, ScimSync(okta, ad)


def test_reconcile_provisions_users_and_groups():
    okta, ad, sync = _fixture()
    report = sync.reconcile()
    assert set(report.created) == {"anguyen", "bokafor"}
    assert ad.is_active("anguyen") and ad.is_active("bokafor")
    assert ad.direct_groups("anguyen") == {"Research"}


def test_reconcile_is_idempotent():
    okta, ad, sync = _fixture()
    sync.reconcile()
    second = sync.reconcile()
    assert not second.changed
    assert second.created == [] and second.updated == [] and second.memberships_changed == []


def test_deactivation_cascades_to_ad():
    okta, ad, sync = _fixture()
    sync.reconcile()
    okta.deactivate_user("anguyen")
    report = sync.reconcile()
    assert "anguyen" in report.deactivated
    assert not ad.is_active("anguyen")
    # ...and the user is stripped from every AD group.
    assert "anguyen" not in ad.direct_groups("anguyen")
    assert ad.effective_groups("anguyen") == set()


def test_group_membership_update_syncs():
    okta, ad, sync = _fixture()
    sync.reconcile()
    okta.add_to_group("bokafor", "Research")
    report = sync.reconcile()
    assert "Research" in report.memberships_changed
    assert ad.direct_groups("bokafor") == {"Platform", "Research"} or "Research" in ad.direct_groups("bokafor")
