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
from labsuite.campaigns import ReviewCampaign
from labsuite.compliance import ComplianceRegistry
from labsuite.endpoints import DeviceFleet
from labsuite.models import AccessDecision, AccessLevel, Department, ProxmoxRole
from labsuite.network import Network
from labsuite.okta import OktaDirectory
from labsuite.operations import Operations
from labsuite.policy import image_for, onboarding_groups, required_trainings
from labsuite.proxmox import Proxmox
from labsuite.requests import AccessRequest, RequestQueue
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
    trainings: dict[str, str] = field(default_factory=dict)
    gated: dict[str, list[str]] = field(default_factory=dict)
    saas: list[str] = field(default_factory=list)

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
            "trainings": self.trainings,
            "gated": self.gated,
            "saas": self.saas,
        }


@dataclass
class OffboardResult:
    username: str
    removed_groups: list[str]
    sync: SyncReport
    residual_truenas: dict[str, str] = field(default_factory=dict)
    residual_proxmox: dict[int, str] = field(default_factory=dict)
    device: dict | None = None
    saas_revoked: list[str] = field(default_factory=list)

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
            "saas_revoked": self.saas_revoked,
        }


@dataclass
class AccessReport:
    username: str
    active: bool
    okta_groups: list[str]
    ad_effective_groups: list[str]
    truenas: dict[str, str]
    proxmox: dict[str, str]
    trainings: dict[str, str] = field(default_factory=dict)
    blocked: dict[str, list[str]] = field(default_factory=dict)  # share -> missing requirements
    mfa_enrolled: bool = False

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "active": self.active,
            "okta_groups": self.okta_groups,
            "ad_effective_groups": self.ad_effective_groups,
            "truenas": self.truenas,
            "proxmox": self.proxmox,
            "trainings": self.trainings,
            "blocked": self.blocked,
            "mfa_enrolled": self.mfa_enrolled,
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
        compliance: ComplianceRegistry | None = None,
        operations: Operations | None = None,
        network: Network | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        # Any of these may be a real-system adapter; the control plane only ever
        # speaks to the provider interfaces.
        self.okta: IdentityProvider = okta or OktaDirectory()
        self.ad: DirectoryProvider = ad or ActiveDirectory()
        self.truenas: StorageProvider = truenas or TrueNAS()
        self.proxmox: ComputeProvider = proxmox or Proxmox()
        self.endpoints: EndpointProvider = endpoints or DeviceFleet()
        self.compliance = compliance or ComplianceRegistry()
        self.ops = operations or Operations()
        self.network = network or Network()
        self.requests = RequestQueue()
        self.campaign = ReviewCampaign("")  # empty until a review campaign is started
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

        # Compliance half: register required trainings (pending), which gates the
        # sensitive lab shares until they are completed.
        trainings = required_trainings(role)
        self.compliance.assign_required(username, trainings)
        self.audit.record(
            actor, "training.assign", username, "compliance",
            detail=f"pending: {', '.join(trainings)}",
        )

        # SaaS half: grant the baseline + role app seats.
        saas = self.ops.provision_role_apps(username, role)
        self.audit.record(actor, "saas.provision", username, "saas", detail=", ".join(saas))

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
            trainings=access.trainings,
            gated=access.blocked,
            saas=saas,
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

        # SaaS half: reclaim all licence seats (same-day).
        saas_revoked = self.ops.revoke_saas_all(username)
        if saas_revoked:
            self.audit.record(actor, "saas.revoke", username, "saas", detail=", ".join(saas_revoked))

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
            saas_revoked=saas_revoked,
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
        mfa = self.okta.is_mfa_enrolled(username)

        # Group membership grants eligibility; gated shares also require current
        # training, and sensitive shares require MFA (conditional access). A share
        # the user is eligible for but not cleared for is moved to `blocked`, with
        # the requirements still outstanding.
        nas: dict[str, str] = {}
        blocked: dict[str, list[str]] = {}
        for name, level in self.truenas.shares_for(groups).items():
            missing = self.compliance.missing_for_share(username, name) if active else []
            if active and self.truenas.get_share(name).sensitive and not mfa:
                missing = [*missing, "MFA (Okta Verify)"]
            if missing:
                blocked[name] = missing
            else:
                nas[name] = str(level)

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
            trainings=self.compliance.trainings_for(username),
            blocked=blocked,
            mfa_enrolled=mfa,
        )

    def check_truenas(self, username: str, share: str, action: str = "read", *, actor: str | None = None) -> AccessDecision:
        """Audited allow/deny for a TrueNAS share access."""
        required = {"read": AccessLevel.READ, "modify": AccessLevel.MODIFY, "full": AccessLevel.FULL}[action]
        groups = self.ad.effective_groups(username)
        granted, via = self.truenas.level_for(share, groups)
        allowed = granted >= required

        # Compliance gate (training) + conditional access (MFA on sensitive data).
        missing = self.compliance.missing_for_share(username, share)
        needs_mfa = self.truenas.get_share(share).sensitive and not self.okta.is_mfa_enrolled(username)
        if allowed and missing:
            allowed = False
            reason = f"BLOCKED — training not current: {', '.join(missing)}"
        elif allowed and needs_mfa:
            allowed = False
            reason = "BLOCKED — conditional access: MFA (Okta Verify) required for sensitive data"
        elif allowed:
            reason = f"granted via {', '.join(via)}"
        else:
            reason = "no group grants sufficient access"

        decision = AccessDecision(
            username=username,
            system="truenas",
            resource=share,
            action=action,
            allowed=allowed,
            granted=str(granted),
            required=str(required),
            via_groups=via,
            reason=reason,
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
    # Compliance / training
    # ------------------------------------------------------------------ #
    def complete_training(self, username: str, training: str, *, actor: str = "compliance") -> None:
        """Record a training as completed -- unlocks any access it was gating."""
        self.compliance.complete(username, training)
        self.audit.record(actor, "training.complete", username, "compliance", detail=training)

    def expire_training(self, username: str, training: str, *, actor: str = "compliance") -> None:
        """Mark a training lapsed -- gated access relying on it auto-revokes."""
        self.compliance.expire(username, training)
        self.audit.record(actor, "training.expire", username, "compliance", outcome="denied", detail=training)

    def enroll_mfa(self, username: str, *, actor: str | None = None) -> None:
        """Enroll a user in MFA (Okta Verify) -- unlocks conditional-access resources."""
        self.okta.enroll_mfa(username)
        self.audit.record(actor or username, "mfa.enroll", username, "okta", detail="Okta Verify")

    # ------------------------------------------------------------------ #
    # Access review
    # ------------------------------------------------------------------ #
    def access_review(self, *, actor: str = "it-admin") -> dict:
        """Quarterly-style review: entitlements per user, plus flagged anomalies."""
        entitlements: dict[str, dict] = {}
        flags: list[str] = []
        for user in self.okta.list_users():
            username = user.username
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
            # Compliance flag: a lapsed training is a genuine anomaly (access to a
            # gated resource was silently revoked). Being *blocked pending* training
            # is correct behaviour, not a flag.
            if user.active:
                for training, status in report.trainings.items():
                    if status == "expired":
                        flags.append(f"{username}: EXPIRED training {training!r}")
        self.audit.record(actor, "access.review", "*", "control-plane", detail=f"{len(flags)} flags")
        return {"entitlements": entitlements, "flags": flags}

    def sync(self, *, actor: str = "it-admin") -> SyncReport:
        return self.scim.reconcile(actor=actor)

    # ------------------------------------------------------------------ #
    # Access-review campaigns (attestation)
    # ------------------------------------------------------------------ #
    def start_review_campaign(self, name: str = "Access review", *, actor: str = "it-admin") -> dict:
        """Open an attestation campaign over every active user."""
        subjects = [u.username for u in self.okta.list_users() if u.active]
        self.campaign = ReviewCampaign(name)
        self.campaign.start(subjects)
        self.audit.record(actor, "campaign.start", name, "control-plane", detail=f"{len(subjects)} subjects")
        return self.campaign.progress()

    def certify_user(self, username: str, *, reviewer: str = "it-admin", note: str = "") -> dict:
        """Attest that a user's current access is still appropriate (keep it)."""
        self.campaign.decide(username, "certified", reviewer, note)
        self.audit.record(reviewer, "campaign.certify", username, "control-plane", detail=note)
        return self.campaign.progress()

    def revoke_user(self, username: str, *, reviewer: str = "it-admin", note: str = "") -> dict:
        """Reviewer revokes a user's entitlements -- strip groups, cascade to AD."""
        removed = self.okta.remove_from_all_groups(username)
        self.scim.reconcile(actor=reviewer)
        self.campaign.decide(username, "revoked", reviewer, note)
        self.audit.record(reviewer, "campaign.revoke", username, "control-plane",
                          outcome="denied", detail=f"removed {', '.join(removed) or 'none'}")
        return self.campaign.progress()

    def campaign_status(self) -> dict:
        """Progress plus a per-user row (decision + live access counts)."""
        rows = []
        for username in self.campaign.subjects:
            report = self.resolve_access(username)
            d = self.campaign.decisions.get(username, {})
            rows.append({
                "username": username,
                "status": d.get("status", "pending"),
                "reviewer": d.get("reviewer", ""),
                "shares": len(report.truenas),
                "vms": len(report.proxmox),
            })
        return {"progress": self.campaign.progress(), "rows": rows}

    # ------------------------------------------------------------------ #
    # Action center -- one inbox aggregating every operational flag
    # ------------------------------------------------------------------ #
    def action_center(self) -> dict:
        """Roll up every outstanding flag across the platform into one feed.

        Each alert is ``{severity, category, title, detail, view}`` where ``view``
        names the GUI panel that resolves it. Severity is ``high`` (act now),
        ``medium`` (this week), or ``info`` (awareness). Returns the alert list
        plus per-severity counts so the Overview can show a live badge.
        """
        alerts: list[dict] = []

        def add(severity: str, category: str, title: str, view: str, detail: str = "") -> None:
            alerts.append({"severity": severity, "category": category, "title": title, "view": view, "detail": detail})

        # Identity / compliance: lapsed training and missing MFA on active staff.
        for user in self.okta.list_users():
            if not user.active:
                # An inactive account that still resolves to access is a real leak.
                report = self.resolve_access(user.username)
                if report.truenas or report.proxmox:
                    add("high", "access", f"{user.username}: inactive but still has access", "review")
                continue
            for training, status in self.compliance.trainings_for(user.username).items():
                if status == "expired":
                    add("high", "compliance", f"{user.username}: {training} training lapsed", "compliance",
                        "gated access auto-revoked")
            if not self.okta.is_mfa_enrolled(user.username):
                add("medium", "mfa", f"{user.username}: not enrolled in MFA", "explorer",
                    "conditional-access resources are blocked")

        # Operations: maintenance, stock, renewals, orphaned seats, safety.
        ops = self.ops.summary(self._is_active)
        for name in ops["overdue_equipment"]:
            add("high", "equipment", f"Maintenance overdue: {name}", "ops")
        for name in ops["low_stock"]:
            add("medium", "inventory", f"Low stock: {name}", "ops")
        for item in ops["open_safety"]:
            add("high", "safety", f"Safety: {item}", "ops")
        for seat in ops["orphaned_seats"]:
            add("medium", "saas", f"Orphaned SaaS seat: {seat}", "saas")
        for renewal in ops["upcoming_renewals"]:
            add("info", "vendor", f"Renewal due: {renewal}", "ops")

        # Networking: segmentation anomalies.
        for flag in self.network.flags():
            sev = "high" if "MISPLACED" in flag else "medium"
            add(sev, "network", flag, "network")

        # Governance: access requests awaiting a decision.
        pending = self.requests.pending()
        if pending:
            add("info", "requests", f"{len(pending)} access request(s) awaiting approval", "requests")

        # Governance: an access-review campaign still awaiting attestations.
        if self.campaign.open and self.campaign.subjects:
            prog = self.campaign.progress()
            if prog["pending"]:
                add("medium", "campaign", f"{prog['pending']} user(s) not yet reviewed ({prog['completion_pct']}% done)", "review")

        order = {"high": 0, "medium": 1, "info": 2}
        alerts.sort(key=lambda a: order.get(a["severity"], 3))
        counts = {sev: sum(1 for a in alerts if a["severity"] == sev) for sev in ("high", "medium", "info")}
        return {"alerts": alerts, "counts": counts, "total": len(alerts)}

    # ------------------------------------------------------------------ #
    # Access requests + approvals (self-service governance)
    # ------------------------------------------------------------------ #
    def request_access(self, requester: str, group: str, justification: str = "") -> AccessRequest:
        """A user requests membership in a group (creates a pending request)."""
        if self.okta.get_user(requester) is None:
            raise KeyError(f"no such user: {requester!r}")
        req = self.requests.create(requester, group, justification)
        self.audit.record(requester, "access.request", f"{req.id}:{group}", "control-plane", detail=justification)
        return req

    def approve_request(self, request_id: str, *, approver: str = "it-admin") -> AccessRequest:
        """Approve a request -- grant the group through the normal Okta -> AD path."""
        req = self.requests.get(request_id)
        if req is None:
            raise KeyError(f"no such request: {request_id!r}")
        if req.status != "pending":
            return req
        self.okta.ensure_group(req.group)
        self.okta.add_to_group(req.requester, req.group)
        self.scim.reconcile(actor=approver)
        req.status = "approved"
        req.decided_by = approver
        self.audit.record(approver, "request.approve", f"{req.id}:{req.group}", "control-plane",
                          detail=f"granted {req.group} to {req.requester}")
        return req

    def deny_request(self, request_id: str, *, approver: str = "it-admin", note: str = "") -> AccessRequest:
        req = self.requests.get(request_id)
        if req is None:
            raise KeyError(f"no such request: {request_id!r}")
        if req.status != "pending":
            return req
        req.status = "denied"
        req.decided_by = approver
        req.note = note
        self.audit.record(approver, "request.deny", f"{req.id}:{req.group}", "control-plane",
                          outcome="denied", detail=note)
        return req

    # ------------------------------------------------------------------ #
    # Operations (SaaS / equipment / inventory / vendors / safety)
    # ------------------------------------------------------------------ #
    def _is_active(self, username: str) -> bool:
        user = self.okta.get_user(username)
        return bool(user and user.active)

    def ops_summary(self) -> dict:
        """SaaS spend + flags across equipment, inventory, vendors, and safety."""
        return self.ops.summary(self._is_active)

    def complete_maintenance(self, asset_tag: str, *, actor: str = "lab-ops"):
        e = self.ops.complete_maintenance(asset_tag)
        if e is not None:
            self.audit.record(actor, "equipment.maintenance", asset_tag, "operations", detail="serviced")
        return e

    def reorder_inventory(self, sku: str, *, actor: str = "lab-ops"):
        i = self.ops.reorder(sku)
        if i is not None:
            self.audit.record(actor, "inventory.reorder", sku, "operations", detail=f"qty -> {i.qty}")
        return i

    def resolve_safety(self, area: str, check: str, *, actor: str = "facilities"):
        s = self.ops.resolve_safety(area, check)
        if s is not None:
            self.audit.record(actor, "safety.resolve", f"{area}:{check}", "operations")
        return s

    def renew_vendor(self, name: str, *, actor: str = "it-admin"):
        v = self.ops.renew_vendor(name)
        if v is not None:
            self.audit.record(actor, "vendor.renew", name, "operations")
        return v

    def grant_saas_seat(self, username: str, app: str, *, actor: str = "it-admin") -> None:
        self.ops.grant_saas(username, app)
        self.audit.record(actor, "saas.grant", username, "saas", detail=app)

    def revoke_saas_seat(self, username: str, app: str, *, actor: str = "it-admin") -> bool:
        ok = self.ops.revoke_saas_seat(username, app)
        if ok:
            self.audit.record(actor, "saas.revoke", username, "saas", detail=app)
        return ok

    # ------------------------------------------------------------------ #
    # Networking (VLAN segmentation + IoT)
    # ------------------------------------------------------------------ #
    def network_summary(self) -> dict:
        return self.network.summary()

    def check_segmentation(self, src: str, dst: str, *, actor: str = "it-admin") -> dict:
        """Audited east-west reachability check between two VLAN segments."""
        allowed, reason = self.network.can_reach(src, dst)
        self.audit.record(
            actor, "network.check", f"{src}->{dst}", "network",
            outcome="success" if allowed else "denied", detail=reason,
        )
        return {"src": src, "dst": dst, "allowed": allowed, "reason": reason}

    def move_device(self, name: str, segment: str, *, actor: str = "it-admin"):
        """Re-place a network device onto a different VLAN (e.g. quarantine)."""
        dev = self.network.move_device(name, segment)
        if dev is not None:
            self.audit.record(actor, "network.move", name, "network", detail=f"-> {segment}")
        return dev

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
            "compliance": self.compliance.to_dict(),
            "operations": self.ops.to_dict(),
            "network": self.network.to_dict(),
            "requests": self.requests.to_dict(),
            "campaign": self.campaign.to_dict(),
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
            compliance=ComplianceRegistry.from_dict(data.get("compliance", {})),
            operations=Operations.from_dict(data.get("operations", {})),
            network=Network.from_dict(data.get("network", {})),
            audit=audit,
        )
        cp.requests = RequestQueue.from_dict(data.get("requests", {}))
        cp.campaign = ReviewCampaign.from_dict(data.get("campaign", {}))
        return cp
