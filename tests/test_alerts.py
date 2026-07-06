"""Action center: one feed aggregating every outstanding operational flag."""

from labsuite.models import Department
from labsuite.seed import build_lab


def test_seeded_lab_has_alerts_with_counts():
    cp = build_lab()
    ac = cp.action_center()
    assert ac["total"] == len(ac["alerts"])
    assert set(ac["counts"]) == {"high", "medium", "info"}
    # The seed deliberately includes overdue maintenance, low stock, open safety,
    # and a misplaced camera -- all should surface as high/medium alerts.
    cats = {a["category"] for a in ac["alerts"]}
    assert {"equipment", "safety", "network"} <= cats


def test_alerts_are_sorted_high_first():
    cp = build_lab()
    order = {"high": 0, "medium": 1, "info": 2}
    sev = [order[a["severity"]] for a in cp.action_center()["alerts"]]
    assert sev == sorted(sev)


def test_expired_training_raises_a_high_alert():
    cp = build_lab()
    cp.expire_training("talvarez", "Biosafety")
    titles = [a["title"] for a in cp.action_center()["alerts"] if a["severity"] == "high"]
    assert any("talvarez" in t and "Biosafety" in t for t in titles)


def test_fresh_hire_without_mfa_raises_a_medium_alert():
    cp = build_lab()
    cp.onboard("Nadia Rahman", Department.INVIVO, "invivo-scientist")
    alerts = cp.action_center()["alerts"]
    assert any(a["category"] == "mfa" and "nrahman" in a["title"] for a in alerts)


def test_resolving_a_flag_removes_its_alert():
    cp = build_lab()
    before = cp.action_center()["total"]
    cp.move_device("cam-server-room", "IoT")  # fix the segmentation flag
    after = cp.action_center()
    assert after["total"] == before - 1
    assert not any(a["category"] == "network" for a in after["alerts"])


def test_pending_request_shows_as_info():
    cp = build_lab()
    cp.request_access("anguyen", "GPU-Cluster-Users", "need the cluster")
    alerts = cp.action_center()["alerts"]
    assert any(a["category"] == "requests" and a["severity"] == "info" for a in alerts)


def test_alert_views_are_valid_targets():
    cp = build_lab()
    valid = {"review", "compliance", "explorer", "ops", "saas", "network", "requests",
             "jit", "cost", "backup"}
    assert all(a["view"] in valid for a in cp.action_center()["alerts"])
