"""Domain models shared across the four layers of the stack.

These dataclasses are deliberately plain and JSON-serialisable so the whole
control plane can be snapshotted to disk (see ``engine.ControlPlane.to_dict``)
and reloaded between CLI invocations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum


class Department(str, Enum):
    """Merge Labs functional teams -- drive default group assignments (policy.py).

    Bioengineering splits into functional teams (Bio, In-Vivo, Delivery,
    Platform, Data-Science); the rest are G&A (Lab-Ops, Compliance, Facilities,
    Legal, IT). Each value is the team's base AD/Okta group name.
    """

    BIO = "Bio"  # molecular tech: cell bio, protein eng, immuno, histology
    INVIVO = "In-Vivo"  # rodent surgery / in-vivo studies
    DELIVERY = "Delivery"  # AAV vector core
    PLATFORM = "Platform"  # research platform / automation
    DATA_SCIENCE = "Data-Science"  # ML research
    LAB = "Lab"  # bench lab / histology
    LAB_OPS = "Lab-Ops"  # lab operations, equipment, procurement
    COMPLIANCE = "Compliance"  # IACUC / animal-research compliance
    FACILITIES = "Facilities"  # facilities & workplace ops
    LEGAL = "Legal"
    IT = "IT"


class AccessLevel(IntEnum):
    """TrueNAS share access, ordered so ``max()`` picks the strongest grant."""

    NONE = 0
    READ = 1
    MODIFY = 2
    FULL = 3

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.name.lower()


class ProxmoxRole(IntEnum):
    """Proxmox roles, ordered by privilege (mirrors PVE's built-in roles)."""

    NO_ACCESS = 0
    PVE_AUDITOR = 1  # read-only
    PVE_VM_USER = 2  # console, start/stop
    PVE_VM_ADMIN = 3  # reconfigure, snapshot, migrate
    PVE_ADMIN = 4  # full node administration

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return _PROXMOX_ROLE_LABELS[self]


_PROXMOX_ROLE_LABELS: dict[ProxmoxRole, str] = {
    ProxmoxRole.NO_ACCESS: "NoAccess",
    ProxmoxRole.PVE_AUDITOR: "PVEAuditor",
    ProxmoxRole.PVE_VM_USER: "PVEVMUser",
    ProxmoxRole.PVE_VM_ADMIN: "PVEVMAdmin",
    ProxmoxRole.PVE_ADMIN: "PVEAdmin",
}


# Which Proxmox role is the minimum required to perform a given privilege.
PROXMOX_PRIVILEGES: dict[str, ProxmoxRole] = {
    "audit": ProxmoxRole.PVE_AUDITOR,
    "vm.console": ProxmoxRole.PVE_VM_USER,
    "vm.power": ProxmoxRole.PVE_VM_USER,
    "vm.config": ProxmoxRole.PVE_VM_ADMIN,
    "vm.snapshot": ProxmoxRole.PVE_VM_ADMIN,
    "vm.migrate": ProxmoxRole.PVE_VM_ADMIN,
    "node.admin": ProxmoxRole.PVE_ADMIN,
}


@dataclass
class User:
    """A person. Lives authoritatively in Okta; a synced copy lives in AD."""

    username: str
    display_name: str
    email: str
    department: Department
    title: str = ""
    active: bool = True
    manager: str | None = None

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "display_name": self.display_name,
            "email": self.email,
            "department": self.department.value,
            "title": self.title,
            "active": self.active,
            "manager": self.manager,
        }

    @classmethod
    def from_dict(cls, data: dict) -> User:
        return cls(
            username=data["username"],
            display_name=data["display_name"],
            email=data["email"],
            department=Department(data["department"]),
            title=data.get("title", ""),
            active=data.get("active", True),
            manager=data.get("manager"),
        )


@dataclass
class Group:
    """A security group.

    In Okta the ``members`` set is authoritative and ``member_groups`` is unused.
    In Active Directory a group may *nest* other groups via ``member_groups`` --
    that is what ``ActiveDirectory.effective_groups`` resolves transitively.
    """

    name: str
    description: str = ""
    members: set[str] = field(default_factory=set)
    member_groups: set[str] = field(default_factory=set)
    ou: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "members": sorted(self.members),
            "member_groups": sorted(self.member_groups),
            "ou": self.ou,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Group:
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            members=set(data.get("members", [])),
            member_groups=set(data.get("member_groups", [])),
            ou=data.get("ou"),
        )


@dataclass
class Share:
    """A TrueNAS SMB/NFS share backed by a dataset, with a group-keyed ACL."""

    name: str
    dataset: str
    description: str = ""
    acl: dict[str, AccessLevel] = field(default_factory=dict)
    sensitive: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "dataset": self.dataset,
            "description": self.description,
            "acl": {g: int(lvl) for g, lvl in self.acl.items()},
            "sensitive": self.sensitive,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Share:
        return cls(
            name=data["name"],
            dataset=data["dataset"],
            description=data.get("description", ""),
            acl={g: AccessLevel(v) for g, v in data.get("acl", {}).items()},
            sensitive=data.get("sensitive", False),
        )


@dataclass
class VM:
    """A Proxmox guest. ``path`` is ``/vms/<vmid>``; it also belongs to a pool."""

    vmid: int
    name: str
    node: str
    pool: str | None = None

    @property
    def path(self) -> str:
        return f"/vms/{self.vmid}"

    def to_dict(self) -> dict:
        return {"vmid": self.vmid, "name": self.name, "node": self.node, "pool": self.pool}

    @classmethod
    def from_dict(cls, data: dict) -> VM:
        return cls(vmid=data["vmid"], name=data["name"], node=data["node"], pool=data.get("pool"))


@dataclass
class ProxmoxACE:
    """One Proxmox access-control entry: ``(path, group) -> role``.

    ``path`` uses PVE's hierarchical scheme: ``/`` (everything), ``/pool/<name>``,
    or ``/vms/<vmid>``. A grant on a parent path is inherited by children.
    """

    path: str
    group: str
    role: ProxmoxRole

    def to_dict(self) -> dict:
        return {"path": self.path, "group": self.group, "role": int(self.role)}

    @classmethod
    def from_dict(cls, data: dict) -> ProxmoxACE:
        return cls(path=data["path"], group=data["group"], role=ProxmoxRole(data["role"]))


@dataclass
class AccessDecision:
    """The audited result of one access check, with its justification."""

    username: str
    system: str  # "truenas" | "proxmox"
    resource: str
    action: str
    allowed: bool
    granted: str  # e.g. "modify" or "PVEVMUser"
    required: str
    via_groups: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "system": self.system,
            "resource": self.resource,
            "action": self.action,
            "allowed": self.allowed,
            "granted": self.granted,
            "required": self.required,
            "via_groups": self.via_groups,
            "reason": self.reason,
        }


class DeviceStatus(str, Enum):
    """Lifecycle of a managed laptop."""

    ASSIGNED = "assigned"  # imaged + shipped, in use
    WIPE_RETURN = "wipe & return"  # flagged at offboarding
    RETIRED = "retired"


@dataclass
class DeviceImage:
    """A standardized laptop build (the "image") assigned to a hire by role."""

    name: str
    platform: str  # macOS | Windows | Linux
    model: str  # default hardware model shipped
    encryption: str  # FileVault | BitLocker
    mdm: str  # Kandji | Intune | Jamf
    mfa: str  # Okta Verify
    config_mgmt: str  # Iru | Ansible
    apps: list[str] = field(default_factory=list)
    ad_joined: bool = False
    notes: str = ""

    def security_summary(self) -> str:
        """e.g. 'FileVault + Okta Verify + Iru' -- the day-one hardening line."""
        return f"{self.encryption} + {self.mfa} + {self.config_mgmt}"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "platform": self.platform,
            "model": self.model,
            "encryption": self.encryption,
            "mdm": self.mdm,
            "mfa": self.mfa,
            "config_mgmt": self.config_mgmt,
            "apps": list(self.apps),
            "ad_joined": self.ad_joined,
            "notes": self.notes,
        }


@dataclass
class Device:
    """A specific managed laptop assigned to a person."""

    asset_tag: str
    model: str
    platform: str
    image: str  # DeviceImage.name
    assignee: str | None
    status: DeviceStatus = DeviceStatus.ASSIGNED
    serial: str = ""

    def to_dict(self) -> dict:
        return {
            "asset_tag": self.asset_tag,
            "model": self.model,
            "platform": self.platform,
            "image": self.image,
            "assignee": self.assignee,
            "status": self.status.value,
            "serial": self.serial,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Device:
        return cls(
            asset_tag=data["asset_tag"],
            model=data["model"],
            platform=data["platform"],
            image=data["image"],
            assignee=data.get("assignee"),
            status=DeviceStatus(data["status"]),
            serial=data.get("serial", ""),
        )
