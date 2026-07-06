"""Proxmox role resolution with hierarchical path inheritance."""

from labsuite.models import ProxmoxRole
from labsuite.proxmox import Proxmox


def _pve() -> Proxmox:
    pve = Proxmox()
    pve.add_vm(101, "train-01", "pve-gpu-01", pool="research")
    pve.add_vm(900, "dc01", "pve-lab-01", pool="infra")
    pve.grant("/", "Domain-Admins", ProxmoxRole.PVE_ADMIN)
    pve.grant("/pool/research", "GPU-Cluster-Users", ProxmoxRole.PVE_VM_USER)
    return pve


def test_pool_grant_applies_to_member_vm():
    pve = _pve()
    role, via = pve.role_for_vm(101, {"GPU-Cluster-Users"})
    assert role == ProxmoxRole.PVE_VM_USER
    assert via == ["GPU-Cluster-Users"]


def test_root_grant_inherited_everywhere():
    pve = _pve()
    assert pve.role_for_vm(101, {"Domain-Admins"})[0] == ProxmoxRole.PVE_ADMIN
    assert pve.role_for_vm(900, {"Domain-Admins"})[0] == ProxmoxRole.PVE_ADMIN


def test_pool_grant_does_not_leak_to_other_pool():
    pve = _pve()
    # A research-pool grant must not apply to the infra-pool VM.
    assert pve.role_for_vm(900, {"GPU-Cluster-Users"})[0] == ProxmoxRole.NO_ACCESS


def test_privilege_check():
    pve = _pve()
    groups = {"GPU-Cluster-Users"}
    allowed, granted, required, _ = pve.can(101, groups, "vm.power")
    assert allowed and granted == ProxmoxRole.PVE_VM_USER
    # ...but not enough to migrate (needs admin).
    allowed, _, required, _ = pve.can(101, groups, "vm.migrate")
    assert not allowed and required == ProxmoxRole.PVE_VM_ADMIN


def test_strongest_role_wins_across_paths():
    pve = _pve()
    # Domain-Admins (root, ADMIN) beats GPU-Cluster-Users (pool, VM_USER).
    role, _ = pve.role_for_vm(101, {"GPU-Cluster-Users", "Domain-Admins"})
    assert role == ProxmoxRole.PVE_ADMIN
