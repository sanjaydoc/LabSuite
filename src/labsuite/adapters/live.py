"""Skeleton adapters for the *real* systems.

These classes implement the same four interfaces as the in-memory reference
adapters, but against production systems. They are intentionally shipped as
**skeletons**: every method documents the exact real-world API call / cmdlet it
would make and raises :class:`NotImplementedError`. Filling one in is a
self-contained task that requires *no* changes anywhere else in LabSuite --
that is the entire point of the adapter layer.

To go live, you would:

1. ``pip install`` the relevant client (``okta``, ``ldap3``, ``proxmoxer``,
   ``requests`` for the TrueNAS REST API);
2. implement the bodies below;
3. construct the control plane with the live adapters::

       from labsuite.engine import ControlPlane
       from labsuite.adapters.live import (
           OktaApiIdentityProvider, LdapDirectoryProvider,
           TrueNasApiStorageProvider, ProxmoxApiComputeProvider,
       )

       cp = ControlPlane(
           okta=OktaApiIdentityProvider(org_url=..., api_token=...),
           ad=LdapDirectoryProvider(server=..., bind_dn=..., password=...),
           truenas=TrueNasApiStorageProvider(base_url=..., api_key=...),
           proxmox=ProxmoxApiComputeProvider(host=..., token_id=..., secret=...),
       )

   Every ``cp.onboard(...)`` / ``cp.offboard(...)`` / ``cp.check_*`` call then
   drives the real estate, unchanged.
"""

from __future__ import annotations

from labsuite.adapters.base import (
    ComputeProvider,
    DirectoryProvider,
    EndpointProvider,
    IdentityProvider,
    StorageProvider,
)
from labsuite.models import AccessLevel, Device, Group, ProxmoxRole, Share, User

_STUB = "live adapter not yet implemented -- wire the real API call here"


class OktaApiIdentityProvider(IdentityProvider):
    """Drive a real Okta org through its REST API (``/api/v1``).

    Requires an Okta API token with the appropriate admin scopes.
    """

    def __init__(self, *, org_url: str, api_token: str) -> None:
        self.org_url = org_url
        self.api_token = api_token
        # e.g. self._client = okta.Client({"orgUrl": org_url, "token": api_token})

    def create_user(self, username, display_name, email, department, password, *, title="", manager=None) -> User:
        # POST /api/v1/users?activate=true  with profile + credentials
        raise NotImplementedError(_STUB)

    def deactivate_user(self, username: str) -> None:
        # POST /api/v1/users/{id}/lifecycle/deactivate
        raise NotImplementedError(_STUB)

    def set_password(self, username: str, password: str) -> None:
        # POST /api/v1/users/{id}  with credentials.password
        raise NotImplementedError(_STUB)

    def get_user(self, username: str) -> User | None:
        # GET /api/v1/users/{login}
        raise NotImplementedError(_STUB)

    def list_users(self) -> list[User]:
        # GET /api/v1/users  (paginated)
        raise NotImplementedError(_STUB)

    def ensure_group(self, name: str, description: str = "") -> Group:
        # GET/POST /api/v1/groups  (find-or-create an OKTA_GROUP)
        raise NotImplementedError(_STUB)

    def list_groups(self) -> list[Group]:
        # GET /api/v1/groups
        raise NotImplementedError(_STUB)

    def add_to_group(self, username: str, group_name: str) -> None:
        # PUT /api/v1/groups/{groupId}/users/{userId}
        raise NotImplementedError(_STUB)

    def remove_from_group(self, username: str, group_name: str) -> None:
        # DELETE /api/v1/groups/{groupId}/users/{userId}
        raise NotImplementedError(_STUB)

    def remove_from_all_groups(self, username: str) -> list[str]:
        # GET /api/v1/users/{id}/groups  then DELETE each membership
        raise NotImplementedError(_STUB)

    def members_of_group(self, group_name: str) -> set[str]:
        # GET /api/v1/groups/{groupId}/users
        raise NotImplementedError(_STUB)

    def groups_for(self, username: str) -> set[str]:
        # GET /api/v1/users/{id}/groups
        raise NotImplementedError(_STUB)

    def authenticate(self, username: str, password: str) -> str:
        # POST /api/v1/authn  (or the OIDC /token endpoint) -> session/JWT
        raise NotImplementedError(_STUB)


class LdapDirectoryProvider(DirectoryProvider):
    """Drive a real Active Directory over LDAP (e.g. via ``ldap3``).

    On Windows you would equivalently shell out to the AD PowerShell module
    (``New-ADUser``, ``Add-ADGroupMember``, ``Disable-ADAccount``).
    """

    def __init__(self, *, server: str, bind_dn: str, password: str, base_dn: str = "") -> None:
        self.server = server
        self.bind_dn = bind_dn
        self.base_dn = base_dn
        # e.g. self._conn = ldap3.Connection(server, bind_dn, password, auto_bind=True)

    def upsert_user(self, user: User) -> bool:
        # ldap add/modify of the user object under the right OU
        raise NotImplementedError(_STUB)

    def deactivate_user(self, username: str) -> bool:
        # set userAccountControl ACCOUNTDISABLE bit; remove group memberships
        raise NotImplementedError(_STUB)

    def is_active(self, username: str) -> bool:
        # search (sAMAccountName=...) and inspect userAccountControl
        raise NotImplementedError(_STUB)

    def list_users(self) -> list[User]:
        # paged search (objectClass=user)
        raise NotImplementedError(_STUB)

    def ensure_group(self, name: str, description: str = "", ou: str | None = None) -> Group:
        # find-or-create a group object (objectClass=group)
        raise NotImplementedError(_STUB)

    def set_group_members(self, name: str, members: set[str]) -> bool:
        # replace the group's 'member' attribute
        raise NotImplementedError(_STUB)

    def nest_group(self, parent: str, child: str) -> None:
        # add the child group DN to the parent group's 'member' attribute
        raise NotImplementedError(_STUB)

    def direct_groups(self, username: str) -> set[str]:
        # read the user's memberOf
        raise NotImplementedError(_STUB)

    def effective_groups(self, username: str) -> set[str]:
        # LDAP_MATCHING_RULE_IN_CHAIN (1.2.840.113556.1.4.1941) for nested groups
        raise NotImplementedError(_STUB)

    def members_of(self, group_name: str) -> set[str]:
        # expand group membership (transitive)
        raise NotImplementedError(_STUB)


class TrueNasApiStorageProvider(StorageProvider):
    """Drive a real TrueNAS through its REST API (``/api/v2.0``)."""

    def __init__(self, *, base_url: str, api_key: str) -> None:
        self.base_url = base_url
        self.api_key = api_key

    def add_share(self, name, dataset, description="", *, sensitive=False) -> Share:
        # POST /api/v2.0/sharing/smb  (+ pool/dataset create)
        raise NotImplementedError(_STUB)

    def grant(self, share_name: str, group: str, level: AccessLevel) -> None:
        # POST /api/v2.0/filesystem/setacl  with an ACE for the AD group SID
        raise NotImplementedError(_STUB)

    def get_share(self, name: str) -> Share:
        # GET /api/v2.0/sharing/smb/id/{id}
        raise NotImplementedError(_STUB)

    def list_shares(self) -> list[Share]:
        # GET /api/v2.0/sharing/smb
        raise NotImplementedError(_STUB)

    def level_for(self, share_name: str, groups: set[str]) -> tuple[AccessLevel, list[str]]:
        # read the dataset ACL and resolve the winning ACE for these groups
        raise NotImplementedError(_STUB)

    def shares_for(self, groups: set[str]) -> dict[str, AccessLevel]:
        raise NotImplementedError(_STUB)


class ProxmoxApiComputeProvider(ComputeProvider):
    """Drive a real Proxmox VE cluster through its API (``/api2/json``)."""

    def __init__(self, *, host: str, token_id: str, secret: str, verify_tls: bool = True) -> None:
        self.host = host
        self.token_id = token_id
        self.verify_tls = verify_tls
        # e.g. self._px = proxmoxer.ProxmoxAPI(host, token_name=..., token_value=secret)

    def add_node(self, name: str) -> None:
        raise NotImplementedError(_STUB)

    def add_vm(self, vmid, name, node, pool=None):
        # POST /api2/json/nodes/{node}/qemu
        raise NotImplementedError(_STUB)

    def grant(self, path: str, group: str, role: ProxmoxRole) -> None:
        # PUT /api2/json/access/acl  {path, groups, roles}
        raise NotImplementedError(_STUB)

    def get_vm(self, vmid: int):
        # GET /api2/json/cluster/resources?type=vm
        raise NotImplementedError(_STUB)

    def list_vms(self):
        raise NotImplementedError(_STUB)

    def role_for_vm(self, vmid, groups):
        # GET /api2/json/access/acl and resolve with path inheritance
        raise NotImplementedError(_STUB)

    def can(self, vmid, groups, privilege):
        raise NotImplementedError(_STUB)

    def objects_for(self, groups):
        raise NotImplementedError(_STUB)


class MdmEndpointProvider(EndpointProvider):
    """Drive a real MDM (Kandji / Jamf / Intune) to image and wipe laptops."""

    def __init__(self, *, base_url: str, api_token: str) -> None:
        self.base_url = base_url
        self.api_token = api_token

    def assign(self, username: str, image_name: str) -> Device:
        # look up/assign a device, apply the Blueprint/Profile for image_name
        raise NotImplementedError(_STUB)

    def wipe_and_return(self, username: str) -> Device | None:
        # POST a remote-wipe / lock command for the user's enrolled device
        raise NotImplementedError(_STUB)

    def device_for(self, username: str) -> Device | None:
        raise NotImplementedError(_STUB)

    def list_devices(self) -> list[Device]:
        raise NotImplementedError(_STUB)


__all__ = [
    "OktaApiIdentityProvider",
    "LdapDirectoryProvider",
    "TrueNasApiStorageProvider",
    "ProxmoxApiComputeProvider",
    "MdmEndpointProvider",
]
