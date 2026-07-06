"""Adapter interfaces -- the seams that make every layer swappable.

Each of the four systems in the stack is reached through an abstract *provider*
interface. The control plane and the SCIM sync engine depend ONLY on these
interfaces, never on a concrete implementation. That means any layer can be
replaced without touching the rest of the code:

    IdentityProvider   <- OktaDirectory (in-memory)   -> OktaApiIdentityProvider (real Okta REST API)
    DirectoryProvider  <- ActiveDirectory (in-memory)  -> LdapDirectoryProvider (real AD via LDAP)
    StorageProvider    <- TrueNAS (in-memory)          -> TrueNasApiStorageProvider (real TrueNAS API)
    ComputeProvider    <- Proxmox (in-memory)          -> ProxmoxApiComputeProvider (real PVE API)

The in-memory implementations that ship with LabSuite are the reference
adapters; ``labsuite.adapters.live`` holds the skeletons for the real systems.
"""

from __future__ import annotations

import abc
from collections.abc import Iterable

from labsuite.models import VM, AccessLevel, Department, Group, ProxmoxRole, Share, User


class IdentityProvider(abc.ABC):
    """The identity / login layer (Okta and work-alikes).

    The authoritative source of users, group membership, and authentication.
    """

    @abc.abstractmethod
    def create_user(
        self,
        username: str,
        display_name: str,
        email: str,
        department: Department,
        password: str,
        *,
        title: str = "",
        manager: str | None = None,
    ) -> User: ...

    @abc.abstractmethod
    def deactivate_user(self, username: str) -> None: ...

    @abc.abstractmethod
    def set_password(self, username: str, password: str) -> None: ...

    @abc.abstractmethod
    def get_user(self, username: str) -> User | None: ...

    @abc.abstractmethod
    def list_users(self) -> list[User]: ...

    @abc.abstractmethod
    def ensure_group(self, name: str, description: str = "") -> Group: ...

    @abc.abstractmethod
    def list_groups(self) -> list[Group]: ...

    @abc.abstractmethod
    def add_to_group(self, username: str, group_name: str) -> None: ...

    @abc.abstractmethod
    def remove_from_group(self, username: str, group_name: str) -> None: ...

    @abc.abstractmethod
    def remove_from_all_groups(self, username: str) -> list[str]: ...

    @abc.abstractmethod
    def members_of_group(self, group_name: str) -> set[str]: ...

    @abc.abstractmethod
    def groups_for(self, username: str) -> set[str]: ...

    @abc.abstractmethod
    def authenticate(self, username: str, password: str) -> str: ...


class DirectoryProvider(abc.ABC):
    """The directory layer (Active Directory and work-alikes).

    Receives users/groups from the identity provider and owns the permission
    structure -- crucially, *nested* groups and their transitive resolution.
    """

    @abc.abstractmethod
    def upsert_user(self, user: User) -> bool: ...

    @abc.abstractmethod
    def deactivate_user(self, username: str) -> bool: ...

    @abc.abstractmethod
    def is_active(self, username: str) -> bool: ...

    @abc.abstractmethod
    def list_users(self) -> list[User]: ...

    @abc.abstractmethod
    def ensure_group(self, name: str, description: str = "", ou: str | None = None) -> Group: ...

    @abc.abstractmethod
    def set_group_members(self, name: str, members: set[str]) -> bool: ...

    @abc.abstractmethod
    def nest_group(self, parent: str, child: str) -> None: ...

    @abc.abstractmethod
    def direct_groups(self, username: str) -> set[str]: ...

    @abc.abstractmethod
    def effective_groups(self, username: str) -> set[str]: ...

    @abc.abstractmethod
    def members_of(self, group_name: str) -> set[str]: ...


class StorageProvider(abc.ABC):
    """The file-storage layer (TrueNAS and work-alikes)."""

    @abc.abstractmethod
    def add_share(self, name: str, dataset: str, description: str = "", *, sensitive: bool = False) -> Share: ...

    @abc.abstractmethod
    def grant(self, share_name: str, group: str, level: AccessLevel) -> None: ...

    @abc.abstractmethod
    def get_share(self, name: str) -> Share: ...

    @abc.abstractmethod
    def list_shares(self) -> list[Share]: ...

    @abc.abstractmethod
    def level_for(self, share_name: str, groups: set[str]) -> tuple[AccessLevel, list[str]]: ...

    @abc.abstractmethod
    def shares_for(self, groups: set[str]) -> dict[str, AccessLevel]: ...


class ComputeProvider(abc.ABC):
    """The virtualization / compute layer (Proxmox and work-alikes)."""

    @abc.abstractmethod
    def add_node(self, name: str) -> None: ...

    @abc.abstractmethod
    def add_vm(self, vmid: int, name: str, node: str, pool: str | None = None) -> VM: ...

    @abc.abstractmethod
    def grant(self, path: str, group: str, role: ProxmoxRole) -> None: ...

    @abc.abstractmethod
    def get_vm(self, vmid: int) -> VM: ...

    @abc.abstractmethod
    def list_vms(self) -> list[VM]: ...

    @abc.abstractmethod
    def role_for_vm(self, vmid: int, groups: set[str]) -> tuple[ProxmoxRole, list[str]]: ...

    @abc.abstractmethod
    def can(self, vmid: int, groups: set[str], privilege: str) -> tuple[bool, ProxmoxRole, ProxmoxRole, list[str]]: ...

    @abc.abstractmethod
    def objects_for(self, groups: set[str]) -> dict[int, ProxmoxRole]: ...


__all__ = [
    "IdentityProvider",
    "DirectoryProvider",
    "StorageProvider",
    "ComputeProvider",
    "Iterable",
]
