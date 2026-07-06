"""Backup / DR health: snapshot + replication status with stale-backup flags."""

from labsuite.backup import BackupRegistry
from labsuite.engine import ControlPlane
from labsuite.seed import build_lab


def test_seeded_lab_has_stale_and_ok_backups():
    cp = build_lab()
    h = cp.backup_health()
    assert h["total"] >= 8
    assert h["stale"] >= 1
    assert 0 <= h["protected_pct"] <= 100


def test_staleness_follows_schedule_threshold():
    reg = BackupRegistry()
    reg.add("a", "dataset", "hourly", 5)   # <= 6 -> ok
    reg.add("b", "dataset", "hourly", 7)   # > 6  -> stale
    reg.add("c", "vm", "weekly", 200)      # > 192 -> stale
    stale = {r.resource for r in reg.stale()}
    assert stale == {"b", "c"}


def test_run_backup_clears_staleness():
    cp = build_lab()
    stale_before = {r["resource"] for r in cp.backup_health()["records"] if r["status"] == "stale"}
    target = next(iter(stale_before))
    cp.run_backup(target)
    stale_after = {r["resource"] for r in cp.backup_health()["records"] if r["status"] == "stale"}
    assert target not in stale_after


def test_run_backup_unknown_resource_returns_none():
    cp = build_lab()
    assert cp.run_backup("tank/does-not-exist") is None


def test_stale_backup_raises_high_alert():
    cp = build_lab()
    alerts = cp.action_center()["alerts"]
    assert any(a["category"] == "backup" and a["severity"] == "high" for a in alerts)


def test_backup_survives_state_roundtrip():
    cp = build_lab()
    restored = ControlPlane.from_dict(cp.to_dict())
    assert restored.backup_health()["total"] == cp.backup_health()["total"]
    assert restored.backup_health()["stale"] == cp.backup_health()["stale"]
