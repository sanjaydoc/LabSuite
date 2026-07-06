"""The control plane: one object that operates the whole stack end-to-end.

``ControlPlane`` wires the four layers together and exposes the *operations* an IT
owner actually performs:

* ``onboard``       -- create in Okta, assign policy groups, sync to AD, report the
                       downstream TrueNAS + Proxmox access the hire now has;
* ``offboard``      -- deactivate in Okta, strip groups, sync (cascading the
                       deactivate into AD), and *verify* zero residual access;
* ``login``         -- authenticate against Okta, returning a session token;
* ``resolve_access``-- walk Okta -> AD (nested groups) -> TrueNAS + Proxmox and
                       return everything a user can touch;
* ``check_access``  -- a single audited allow/deny decision;
* ``access_review`` -- the quarterly "who can touch what" report, with flags;
* ``sync``          -- run the SCIM reconcile on demand.

Every step is recorded in the audit log.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from labsuite.active_directory import ActiveDirectory
from labsuite.adapters.base import (
    ComputeProvider,
    DirectoryProvider,
    EndpointProvider,
    IdentityProvider,
    StorageProvider,
)
from labsuite.audit import AuditLog
from labsuite.endpoints import DeviceFleet
from labsuite.models import AccessDecision, AccessLevel, Department, ProxmoxRole
from labsuite.okta import OktaDirectory
from labsuite.policy import image_for, onboarding_groups
from labsuite.proxmox import Proxmox
from labsuite.scim import ScimSync, SyncReport
from labsuite.truenas import TrueNAS

_ADJECTIVES = "brave calm clever bright quick warm keen bold".split()
_NOUNS = "otter falcon cedar delta harbor quartz meadow signal".split()


def _generate_passphrase() -> str:
    return f"{secrets.choice(_ADJECTIVES)}-{secrets.choice(_NOUNS)}-{secrets.randbelow(9000) + 1000}"


@dataclass
class OnboardResult:
    username: str
    email: str
    temp_password: str
    okta_groups: list[str]
    sync: SyncReport
    truenas_access: dict[str, str] = field(default_factory=dict)
    proxmox_access: dict[int, str] = field(default_factory=dict)
    device: dict | None = None

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "email": self.email,
            "temp_password": self.temp_password,
            "okta_groups": self.okta_groups,
            "sync": self.sync.to_dict(),
            "truenas_access": self.truenas_access,
            "proxmox_access": self.proxmox_access,
            "device": self.device,
        }


@dataclass
class OffboardResult:
    username: str
    removed_groups: list[str]
    sync: SyncReport
    residual_truenas: dict[str, str] = field(default_factory=dict)
    residual_proxmox: dict[int, str] = field(default_factory=dict)
    device: dict | None = None

    @property
    def clean(self) -> bool:
        return not self.residual_truenas and not self.residual_proxmox

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "removed_groups": self.removed_groups,
            "sync": self.sync.to_dict(),
            "residual_truenas": self.residual_truenas,
            "residual_proxmox": self.residual_proxmox,
            "clean": self.clean,
            "device": self.device,
        }


@dataclass
class AccessReport:
    username: str
    active: bool
    okta_groups: list[str]
    ad_effective_groups: list[str]
    truenas: dict[str, str]
    proxmox: dict[str, str]

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "active": self.active,
            "okta_groups": self.okta_groups,
            "ad_effective_groups": self.ad_effective_groups,
            "truenas": self.truenas,
            "proxmox": self.proxmox,
        }


class ControlPlane:
    """Operates Okta -> Active Directory -> TrueNAS + Proxmox as one system."""

    def __init__(
        self,
        okta: IdentityProvider | None = None,
        ad: DirectoryProvider | None = None,
        truenas: StorageProvider | None = None,
        proxmox: ComputeProvider | None = None,
        endpoints: EndpointProvider | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        # Any of these may be a real-system adapter; the control plane only ever
        # speaks to the provider interfaces.
        self.okta: IdentityProvider = okta or OktaDirectory()
        self.ad: DirectoryProvider = ad or ActiveDirectory()
        self.truenas: StorageProvider = truenas or TrueNAS()
        self.proxmox: ComputeProvider = proxmox or Proxmox()
        self.endpoints: EndpointProvider = endpoints or DeviceFleet()
        self.audit = audit or AuditLog()
        self.scim = ScimSync(self.okta, self.ad, self.audit)

    # ------------------------------------------------------------------ #
    # Identity helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _username_from_name(display_name: str) -> str:
        parts = display_name.lower().split()
        if len(parts) >= 2:
            return f"{parts[0][0]}{parts[-1]}"
        return parts[0] if parts else "user"

    def _unique_username(self, base: str) -> str:
        username = base
        suffix = 1
        while self.okta.get_user(username) is not None:
            suffix += 1
            username = f"{base}{suffix}"
        return username

    # ------------------------------------------------------------------ #
    # Onboarding
    # ------------------------------------------------------------------ #
    def onboard(
        self,
        display_name: str,
        department: Department,
        role: str,
        *,
        title: str = "",
        manager: str | None = None,
        email_domain: str = "lab.local",
        actor: str = "it-admin",
    ) -> OnboardResult:
        """Provision a new hire cleanly across the whole stack."""
        username = self._unique_username(self._username_from_name(display_name))
        email = f"{username}@{email_domain}"
        password = _generate_passphrase()
        groups = onboarding_groups(department, role)

        self.okta.create_user(
            username, display_name, email, department, password, title=title or role, manager=manager
        )
        self.audit.record(actor, "user.create", username, "okta", detail=f"role={role}")
        for group in groups:
            self.okta.ensure_group(group)
            self.okta.add_to_group(username, group)
        self.audit.record(actor, "access.grant", username, "okta", detail=",".join(groups))

        report = self.scim.reconcile(actor=actor)

        # Endpoint half: image + ship a managed laptop for the role.
        device = self.endpoints.assign(username, image_for(role))
        self.audit.record(
            actor, "device.provision", device.asset_tag, "endpoint",
            detail=f"{device.model} · {device.image}",
        )

        access = self.resolve_access(username)
        return OnboardResult(
            username=username,
            email=email,
            temp_password=password,
            okta_groups=groups,
            sync=report,
            truenas_access=access.truenas,
            proxmox_access={int(k.split("/")[-1]): v for k, v in access.proxmox.items()},
            device=device.to_dict(),
        )

    # ------------------------------------------------------------------ #
    # Offboarding
    # ------------------------------------------------------------------ #
    def offboard(self, username: str, *, actor: str = "it-admin") -> OffboardResult:
        """Deprovision a departing employee same-day and verify no residual access."""
        if self.okta.get_user(username) is None:
            raise KeyError(f"no such user: {username!r}")

        removed = self.okta.remove_from_all_groups(username)
        self.okta.deactivate_user(username)
        self.audit.record(actor, "user.deactivate", username, "okta", detail="offboard")

        report = self.scim.reconcile(actor=actor)

        # Endpoint half: flag the managed laptop for wipe & return.
        device = self.endpoints.wipe_and_return(username)
        if device is not None:
            self.audit.record(
                actor, "device.wipe", device.asset_tag, "endpoint", detail="wipe & return",
            )

        # Verification: after deprovisioning, effective access must be empty.
        groups = self.ad.effective_groups(username)
        residual_nas = {k: str(v) for k, v in self.truenas.shares_for(groups).items()}
        residual_pve = {
            self.proxmox.get_vm(vmid).path: str(role)
            for vmid, role in self.proxmox.objects_for(groups).items()
        }
        outcome = "success" if not residual_nas and not residual_pve else "error"
        self.audit.record(
            actor,
            "offboard.verify",
            username,
            "control-plane",
            outcome=outcome,
            detail=f"residual_nas={len(residual_nas)} residual_pve={len(residual_pve)}",
        )
        return OffboardResult(
            username=username,
            removed_groups=removed,
            sync=report,
            residual_truenas=residual_nas,
            residual_proxmox={int(p.split("/")[-1]): r for p, r in residual_pve.items()},
            device=device.to_dict() if device is not None else None,
        )

    # ------------------------------------------------------------------ #
    # Authentication
    # ------------------------------------------------------------------ #
    def login(self, username: str, password: str, *, now: float | None = None) -> str:
        try:
            token = self.okta.authenticate(username, password, now=now)
        except PermissionError:
            self.audit.record(username, "auth.login", username, "okta", outcome="denied")
            raise
        self.audit.record(username, "auth.login", username, "okta", outcome="success")
        return token

    # ------------------------------------------------------------------ #
    # Access resolution & decisions
    # ------------------------------------------------------------------ #
    def resolve_access(self, username: str) -> AccessReport:
        """Walk the full chain and report everything a user can touch."""
        user = self.okta.get_user(username)
        active = bool(user and user.active)
        groups = self.ad.effective_groups(username)
        nas = {name: str(level) for name, level in self.truenas.shares_for(groups).items()}
        pve = {
            self.proxmox.get_vm(vmid).path: str(role)
            for vmid, role in self.proxmox.objects_for(groups).items()
        }
        return AccessReport(
            username=username,
            active=active,
            okta_groups=sorted(self.okta.groups_for(username)),
            ad_effective_groups=sorted(groups),
            truenas=nas,
            proxmox=pve,
        )

    def check_truenas(self, username: str, share: str, action: str = "read", *, actor: str | None = None) -> AccessDecision:
        """Audited allow/deny for a TrueNAS share access."""
        required = {"read": AccessLevel.READ, "modify": AccessLevel.MODIFY, "full": AccessLevel.FULL}[action]
        groups = self.ad.effective_groups(username)
        granted, via = self.truenas.level_for(share, groups)
        allowed = granted >= required
        decision = AccessDecision(
            username=username,
            system="truenas",
            resource=share,
            action=action,
            allowed=allowed,
            granted=str(granted),
            required=str(required),
            via_groups=via,
            reason=(f"granted via {', '.join(via)}" if allowed else "no group grants sufficient access"),
        )
        self.audit.record(
            actor or username, "access.check", f"truenas:{share}", "truenas",
            outcome="success" if allowed else "denied", detail=f"{action} -> {granted}",
        )
        return decision

    def check_proxmox(self, username: str, vmid: int, privilege: str = "vm.console", *, actor: str | None = None) -> AccessDecision:
        """Audited allow/deny for a Proxmox privilege on a VM."""
        groups = self.ad.effective_groups(username)
        allowed, granted, required, via = self.proxmox.can(vmid, groups, privilege)
        decision = AccessDecision(
            username=username,
            system="proxmox",
            resource=f"/vms/{vmid}",
            action=privilege,
            allowed=allowed,
            granted=str(granted),
            required=str(required),
            via_groups=via,
            reason=(f"role {granted} via {', '.join(via)}" if allowed else f"role {granted} < required {required}"),
        )
        self.audit.record(
            actor or username, "access.check", f"proxmox:/vms/{vmid}", "proxmox",
            outcome="success" if allowed else "denied", detail=f"{privilege} -> {granted}",
        )
        return decision

    # ------------------------------------------------------------------ #
    # Access review
    # ------------------------------------------------------------------ #
    def access_review(self, *, actor: str = "it-admin") -> dict:
        """Quarterly-style review: entitlements per user, plus flagged anomalies."""
        entitlements: dict[str, dict] = {}
        flags: list[str] = []
        for username, user in self.okta.users.items():
            report = self.resolve_access(username)
            entitlements[username] = report.to_dict()

            # Anomaly checks a reviewer cares about.
            if not user.active and (report.truenas or report.proxmox):
                flags.append(f"{username}: INACTIVE but still has downstream access")
            for share_name, level in report.truenas.items():
                share = self.truenas.get_share(share_name)
                if share.sensitive and level == str(AccessLevel.FULL):
                    flags.append(f"{username}: FULL access to sensitive share {share_name!r}")
            for path, role in report.proxmox.items():
                if role == str(ProxmoxRole.PVE_ADMIN):
                    flags.append(f"{username}: PVEAdmin on {path}")
        self.audit.record(actor, "access.review", "*", "control-plane", detail=f"{len(flags)} flags")
        return {"entitlements": entitlements, "flags": flags}

    def sync(self, *, actor: str = "it-admin") -> SyncReport:
        return self.scim.reconcile(actor=actor)

    # ------------------------------------------------------------------ #
    # Serialisation (persist state between CLI invocations)
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "okta": self.okta.to_dict(),
            "ad": self.ad.to_dict(),
            "truenas": self.truenas.to_dict(),
            "proxmox": self.proxmox.to_dict(),
            "endpoints": self.endpoints.to_dict(),
            "audit": self.audit.to_list(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> ControlPlane:
        audit = AuditLog()
        audit.load(data.get("audit", []))
        cp = cls(
            okta=OktaDirectory.from_dict(data["okta"]),
            ad=ActiveDirectory.from_dict(data["ad"]),
            truenas=TrueNAS.from_dict(data["truenas"]),
            proxmox=Proxmox.from_dict(data["proxmox"]),
            endpoints=DeviceFleet.from_dict(data.get("endpoints", {})),
            audit=audit,
        )
        return cp
