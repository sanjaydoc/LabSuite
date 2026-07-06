"""The Proxmox layer: VMs / internal servers with hierarchical, group-based ACLs.

Proxmox VE assigns *roles* to *groups* at a *path*. Paths are hierarchical:

    /                 -> the whole datacenter
    /pool/<name>      -> a resource pool
    /vms/<vmid>       -> a single guest

A grant on a parent path is inherited by its children, and a user's role on an
object is the strongest role any of their AD groups is granted on that object or
any ancestor path. Roles map to concrete privileges (see
``models.PROXMOX_PRIVILEGES``), so "can user X start VM 101?" resolves to a single
role comparison.
"""

from __future__ import annotations

from labsuite.adapters.base import ComputeProvider
from labsuite.models import PROXMOX_PRIVILEGES, VM, ProxmoxACE, ProxmoxRole


class Proxmox(ComputeProvider):
    """The reference in-memory :class:`ComputeProvider` (a Proxmox datacenter).

    Swap this for :class:`labsuite.adapters.live.ProxmoxApiComputeProvider` to
    drive a real PVE cluster -- the control plane never has to change.
    """

    def __init__(self, cluster: str = "pve-lab") -> None:
        self.cluster = cluster
        self.nodes: list[str] = []
        self.vms: dict[int, VM] = {}
        self.acl: list[ProxmoxACE] = []

    def add_node(self, name: str) -> None:
        if name not in self.nodes:
            self.nodes.append(name)

    def add_vm(self, vmid: int, name: str, node: str, pool: str | None = None) -> VM:
        self.add_node(node)
        vm = VM(vmid=vmid, name=name, node=node, pool=pool)
        self.vms[vmid] = vm
        return vm

    def grant(self, path: str, group: str, role: ProxmoxRole) -> None:
        self.acl.append(ProxmoxACE(path=path, group=group, role=role))

    def get_vm(self, vmid: int) -> VM:
        return self.vms[vmid]

    def list_vms(self) -> list[VM]:
        return list(self.vms.values())

    # ------------------------------------------------------------------ #
    # Resolution
    # ------------------------------------------------------------------ #
    def _paths_for_vm(self, vm: VM) -> list[str]:
        paths = ["/", vm.path]
        if vm.pool:
            paths.append(f"/pool/{vm.pool}")
        return paths

    def role_for_path(self, paths: list[str], groups: set[str]) -> tuple[ProxmoxRole, list[str]]:
        best = ProxmoxRole.NO_ACCESS
        via: list[str] = []
        for ace in self.acl:
            if ace.path in paths and ace.group in groups:
                if ace.role > best:
                    best = ace.role
                    via = [ace.group]
                elif ace.role == best and best > ProxmoxRole.NO_ACCESS:
                    via.append(ace.group)
        return best, sorted(set(via))

    def role_for_vm(self, vmid: int, groups: set[str]) -> tuple[ProxmoxRole, list[str]]:
        vm = self.vms[vmid]
        return self.role_for_path(self._paths_for_vm(vm), groups)

    def can(self, vmid: int, groups: set[str], privilege: str) -> tuple[bool, ProxmoxRole, ProxmoxRole, list[str]]:
        """Return (allowed, granted_role, required_role, via_groups) for a privilege."""
        required = PROXMOX_PRIVILEGES[privilege]
        granted, via = self.role_for_vm(vmid, groups)
        return granted >= required, granted, required, via

    def objects_for(self, groups: set[str]) -> dict[int, ProxmoxRole]:
        """Every VM the groups can reach, with the effective role."""
        result: dict[int, ProxmoxRole] = {}
        for vmid in self.vms:
            role, _ = self.role_for_vm(vmid, groups)
            if role > ProxmoxRole.NO_ACCESS:
                result[vmid] = role
        return result

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "cluster": self.cluster,
            "nodes": list(self.nodes),
            "vms": [v.to_dict() for v in self.vms.values()],
            "acl": [a.to_dict() for a in self.acl],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Proxmox:
        pve = cls(cluster=data.get("cluster", "pve-lab"))
        pve.nodes = list(data.get("nodes", []))
        pve.vms = {v["vmid"]: VM.from_dict(v) for v in data.get("vms", [])}
        pve.acl = [ProxmoxACE.from_dict(a) for a in data.get("acl", [])]
        return pve
