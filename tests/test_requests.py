"""Self-service access requests + approvals."""

import json

import pytest

from labsuite.seed import build_lab


def test_request_creates_pending():
    cp = build_lab()
    req = cp.request_access("lpark", "Data-Science", "ML side-project")
    assert req.status == "pending"
    assert req.id in {r.id for r in cp.requests.pending()}


def test_request_for_unknown_user_rejected():
    cp = build_lab()
    with pytest.raises(KeyError):
        cp.request_access("ghost", "Data-Science")


def test_approve_grants_access_through_the_normal_path():
    cp = build_lab()
    assert "research-data" not in cp.resolve_access("lpark").truenas  # lab-ops has none
    req = cp.request_access("lpark", "Data-Science", "ML side-project")
    approved = cp.approve_request(req.id, approver="sanbu")
    assert approved.status == "approved" and approved.decided_by == "sanbu"
    # Data-Science nests into Research + GPU-Cluster-Users, so access now resolves.
    report = cp.resolve_access("lpark")
    assert "Research" in report.ad_effective_groups
    assert report.truenas.get("research-data") == "modify"


def test_deny_grants_nothing():
    cp = build_lab()
    req = cp.request_access("lpark", "Domain-Admins", "wants admin")
    denied = cp.deny_request(req.id, approver="sanbu", note="not justified")
    assert denied.status == "denied" and denied.note == "not justified"
    assert "Domain-Admins" not in cp.resolve_access("lpark").ad_effective_groups


def test_decided_request_is_idempotent():
    cp = build_lab()
    req = cp.request_access("lpark", "Data-Science")
    cp.approve_request(req.id)
    # approving again is a no-op that returns the already-approved request
    again = cp.approve_request(req.id)
    assert again.status == "approved"


def test_requests_are_audited_and_persist():
    cp = build_lab()
    req = cp.request_access("lpark", "Data-Science", "why")
    cp.approve_request(req.id)
    actions = {e.action for e in cp.audit.events}
    assert {"access.request", "request.approve"} <= actions
    restored = type(cp).from_dict(json.loads(json.dumps(cp.to_dict())))
    assert restored.requests.get(req.id).status == "approved"
