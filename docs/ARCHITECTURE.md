# LabSuite architecture

LabSuite operates one identity stack end-to-end:

```
Okta  ──SCIM──▶  Active Directory  ──group ACLs──▶  TrueNAS + Proxmox
```

This document explains each layer, the data that flows between them, and the two
algorithms that matter (SCIM reconcile and access resolution).

## The four layers

### 1 · Okta — identity / login (`okta.py`)

The authoritative source of identities. It owns:

- **users** (the source of truth for who exists and their attributes),
- **groups** and their **authoritative membership**,
- **authentication** — verifies a password (PBKDF2) and issues a signed HS256
  session token carrying the user's groups.

Nothing downstream invents a user; everything is provisioned *from* here.

### The SCIM sync engine (`scim.py`)

`ScimSync.reconcile()` is the **Okta → AD arrow**. On each run it:

1. upserts every active Okta user into AD;
2. deactivates in AD any user who was deactivated or removed in Okta
   (the event that must cascade to a *same-day deprovision*);
3. mirrors Okta group membership onto the matching AD security groups.

It is **idempotent**: with no intervening change, a second run reports zero
changes. That property is what makes the sync safe to run on a schedule.

### 2 · Active Directory — users / groups / permissions (`active_directory.py`)

AD receives the synced users and groups, but it is not a passive mirror — it owns
the permission structure. Its distinctive feature is **nested groups**: a group
can contain other groups, and membership is inherited transitively.

`effective_groups(user)` computes the transitive closure: a member of `Research`
is effectively a member of every group that (directly or through a chain)
contains `Research`. This is the resolution TrueNAS and Proxmox rely on.

### 3a · TrueNAS — file storage (`truenas.py`)

Shares expose datasets; each share's ACL maps an **AD group** to an access level
(`read` < `modify` < `full`). A user's level on a share is the strongest level
granted to any of their effective AD groups. TrueNAS asks AD for effective groups
and never sees Okta.

### 3b · Proxmox — VMs / servers (`proxmox.py`)

Proxmox assigns **roles** to **groups** at a **path**. Paths are hierarchical
(`/` ⊃ `/pool/<name>` ⊃ `/vms/<vmid>`) and a grant on a parent is inherited by
its children. A user's role on an object is the strongest role any of their
effective groups holds on that object or an ancestor path. Roles map to concrete
privileges (`vm.power`, `vm.migrate`, `node.admin`, …).

## The control plane (`engine.py`)

`ControlPlane` wires the four layers together and exposes the operations:

| Operation | What it does |
|---|---|
| `onboard` | create in Okta → assign policy groups → SCIM sync → report downstream access |
| `offboard` | deactivate in Okta → strip groups → SCIM sync → **verify** zero residual access |
| `login` | authenticate against Okta → session token |
| `resolve_access` | walk Okta → AD → TrueNAS + Proxmox, return everything a user can touch |
| `check_truenas` / `check_proxmox` | one audited allow/deny decision, with the granting groups |
| `access_review` | per-user entitlements + anomaly flags |
| `sync` | run the SCIM reconcile |

Every operation writes to the append-only `AuditLog`.

## Access resolution (the "upward" path)

To decide *"can user U do action A on resource R?"*:

1. `groups = ad.effective_groups(U)` — direct AD groups plus every group that
   transitively nests them (skips the user entirely if they are inactive).
2. **TrueNAS:** `level = max(share.acl[g] for g in groups)`; allow if
   `level >= required`.
3. **Proxmox:** collect ACL entries whose path is `/`, the VM's pool path, or the
   VM's own path, restricted to `groups`; `role = max(role for those)`; allow if
   `role >= required_role(A)`.

The decision records which groups produced the granting level/role, so an
auditor can see *why* access was allowed.

## Provisioning policy (`policy.py`)

Onboarding is deterministic, not a judgement call. A **role blueprint** maps a
`(department, role)` to the exact Okta groups a new hire receives. Resource-access
groups (e.g. `GPU-Cluster-Users`) are *not* assigned per user — they are composed
in AD by nesting the department groups, so membership is inherited and there is no
per-user grant to maintain or forget to revoke.

## Swappable adapters (`adapters/`)

`adapters/base.py` defines four abstract providers — `IdentityProvider`,
`DirectoryProvider`, `StorageProvider`, `ComputeProvider`. The control plane and
SCIM engine depend only on these. The in-memory classes (`OktaDirectory`,
`ActiveDirectory`, `TrueNAS`, `Proxmox`) are the reference adapters;
`adapters/live.py` holds skeletons that document the real API calls for a
production swap. No control-plane code changes when you swap an adapter.

## Beyond access: endpoints, compliance, operations

The control plane also owns the rest of day-to-day IT/ops, so onboarding and
offboarding are complete and the platform is the single source of truth.

- **Endpoints (`endpoints.py`).** Onboarding assigns a managed laptop *image* by
  role (`policy.IMAGE_CATALOG`: OS, disk encryption, MDM, MFA, config-management
  agent). Offboarding flags the device **wipe & return**. Swap `DeviceFleet` for
  an MDM-backed adapter (`adapters.live.MdmEndpointProvider`).
- **Compliance (`compliance.py`).** A register of training status (current /
  expired / missing). Gated shares (`policy.GATED_SHARES`) require the relevant
  training to be *current*: `resolve_access` and `check_truenas` withhold a share
  the user's groups otherwise grant, and restore it when the training is
  completed — auto-revoking if it lapses. This unifies HR compliance with IT
  access.
- **Operations (`operations.py`).** The ops source of truth: SaaS licences (who
  has a seat + monthly/annual spend + orphaned-seat detection), equipment
  (maintenance/calibration due + overdue), inventory (low-stock reorder), vendors
  (upcoming renewals + annual cost), and facility safety (open issues).
  Onboarding grants baseline + role SaaS seats; offboarding reclaims them.

All of these persist in the control-plane snapshot and write to the same audit log.

## State & persistence

The whole control plane serialises to JSON (`ControlPlane.to_dict` / `from_dict`),
so the CLI can persist state between invocations with `--state PATH`. This is a
convenience of the in-memory reference adapters; a live deployment's state lives
in the real systems.
