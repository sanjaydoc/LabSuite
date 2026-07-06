<div align="center">

# 🔐 LabSuite

**A from-scratch IT & operations control plane for a research lab — identity, endpoints, compliance, and day-to-day ops in one place, wired the way a real lab is: Okta → Active Directory → TrueNAS + Proxmox.**

*Author: Dr. Sanjay Anbu*

*Onboard a hire across identity, a managed laptop, training, and SaaS in one call · deprovision same-day and prove it · gate sensitive lab data on current training · track licence spend, equipment, inventory, vendors, and safety — all audited.*

![CI](https://github.com/sanjaydoc/LabSuite/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000)
![Deps](https://img.shields.io/badge/core%20deps-zero-success)

[Live demo ↗](https://sanjaydoc.github.io/LabSuite/app/) · [Website ↗](https://sanjaydoc.github.io/LabSuite/) · [Architecture](#the-stack) · [Dashboard](#the-web-dashboard) · [Quickstart](#quickstart) · [How it maps to the real stack](#how-it-maps-to-the-real-stack)

</div>

<p align="center">
  <img src="docs/media/dashboard-onboard.png" alt="LabSuite dashboard — onboarding a hire across Okta, AD, TrueNAS and Proxmox in one action" width="900" />
</p>
<p align="center"><sub><b>Onboard once, provisioned everywhere.</b> One action creates the hire in Okta, assigns policy groups, syncs to AD, and resolves their exact TrueNAS + Proxmox access — <a href="https://sanjaydoc.github.io/LabSuite/app/">try the live demo</a>.</sub></p>

---

## Why this exists

Every research lab runs on the same quiet machinery: an identity provider people
log into, a directory that decides what they can touch, and the storage and
compute that actually holds the work. When that chain is wired well, nobody
thinks about it. When it isn't, a departing employee keeps access for weeks, a
new hire waits two days for a share, and no one can answer *"who can read the
contracts folder?"* without a meeting.

**LabSuite is that chain, built from scratch and made operable.** It models one
specific, extremely common stack —

> **Okta** (identity / login) → **Active Directory** (users / groups / permissions) → **TrueNAS** (file storage) + **Proxmox** (VMs / servers)

— and exposes the *operations* an IT owner actually performs: onboard, offboard,
sync, resolve access, decide a single allow/deny, run an access review. Every
step is audited.

The core is **pure-Python and dependency-free** — it runs anywhere, in CI, with
no cloud and no external services — and every layer sits behind a **swappable
adapter** so the in-memory reference can be replaced with the real Okta / AD /
TrueNAS / Proxmox APIs without touching the control plane.

> **What this is, honestly:** a faithful **reference implementation / simulator**
> of the stack and the processes around it, not a wrapper over a live tenant. It
> exists to demonstrate the identity→infrastructure *logic* end-to-end — and to
> be the exact shape you'd fill in with real API calls. See
> [How it maps to the real stack](#how-it-maps-to-the-real-stack).

## The stack

```mermaid
flowchart TB
    subgraph CP["ControlPlane — the orchestrator (engine.py)"]
        direction LR
        OP["onboard · offboard · login · resolve_access · check_access · access_review"]
    end

    subgraph OKTA["1 · OKTA — identity / login layer"]
        OU["Users — source of truth"]
        OG["Groups — authoritative membership"]
        OA["authenticate → signed session token"]
    end

    subgraph SYNC["SCIM sync engine"]
        RC["reconcile() — idempotent<br/>create · update · deactivate · mirror groups"]
    end

    subgraph AD["2 · ACTIVE DIRECTORY — users / groups / permissions"]
        ADG["Mirrored security groups"]
        ADN["Nested resource groups<br/>GPU-Cluster-Users ⊇ Research, Platform"]
        ADE["effective_groups() — transitive closure"]
    end

    subgraph NAS["3a · TrueNAS — file storage"]
        SH["Shares / datasets<br/>ACL: group → read/modify/full"]
    end

    subgraph PVE["3b · Proxmox — VMs / servers"]
        VM["Nodes · pools · VMs<br/>ACL: (path, group) → PVE role"]
    end

    AUDIT["Audit log — append-only:<br/>every mutation + every access decision"]

    CP --> OKTA
    OKTA -- "provisioned via SCIM" --> SYNC
    SYNC --> AD
    ADG --> ADN --> ADE
    ADE -- "effective groups" --> NAS
    ADE -- "effective groups" --> PVE
    CP -.writes.-> AUDIT

    classDef okta fill:#635bff,stroke:#4b45c6,color:#fff;
    classDef ad fill:#2563eb,stroke:#1e40af,color:#fff;
    classDef nas fill:#059669,stroke:#047857,color:#fff;
    classDef pve fill:#d97706,stroke:#b45309,color:#fff;
    classDef sync fill:#7c3aed,stroke:#5b21b6,color:#fff;
    classDef audit fill:#475569,stroke:#334155,color:#fff;
    class OKTA,OU,OG,OA okta;
    class AD,ADG,ADN,ADE ad;
    class NAS,SH nas;
    class PVE,VM pve;
    class SYNC,RC sync;
    class AUDIT audit;
```

**Read it two ways.** *Downward* is provisioning: a hire is created in Okta →
SCIM pushes them into AD → their group membership decides their ACLs on TrueNAS
and Proxmox. *Upward* is a decision: "can she start VM 101?" resolves her
**effective AD groups** (following nesting) → checks the Proxmox ACL on that VM's
path → allow/deny, written to the audit log. TrueNAS and Proxmox never see Okta —
they only ask AD for effective groups, exactly as the real systems are wired.

## Modules — one platform every team uses

LabSuite started as identity→infrastructure access and grew into the **central
day-to-day ops tool** for a lab, modelled on Merge Labs' real teams (Bio, In-Vivo,
Delivery, Platform, Data-Science + Lab-Ops, Compliance, Facilities, Legal, IT):

| Module | What it does | Who it serves |
|---|---|---|
| **Identity & access** | Onboard/offboard, SCIM sync, nested-group ACLs, audited allow/deny, access reviews | IT, everyone |
| **Endpoints / devices** | Images + ships a managed laptop per role (encryption, MDM, MFA, Iru/Ansible); wipe & return at offboard | IT |
| **Compliance-gated access** | Training records (IACUC, biosafety…) that gate sensitive lab data; access auto-revokes when training lapses | Compliance, In-Vivo, IT |
| **SaaS & cost** | Who has a seat in what and **what it costs**; orphaned-seat detection; provisioned on onboard, reclaimed on offboard | IT, Finance |
| **Equipment & maintenance** | Instrument registry with calibration/maintenance cadence; overdue + due-soon flags | Lab-Ops, Facilities |
| **Inventory** | Reagents/consumables with reorder points; low-stock flags | Lab-Ops, Vector Core |
| **Vendors & renewals** | Contracts with renewal dates + annual cost; upcoming-renewal flags | Lab-Ops, IT |
| **Facility safety** | Walkthrough checklists; open-issue flags | Facilities |

Every mutation and decision across all modules lands in the same append-only
audit log.

## The web dashboard

A polished, **zero-build web GUI** (vanilla HTML/CSS/JS — no framework) ships with
the project. Run `labsuite serve` for the live, API-backed version, or open the
[**live demo**](https://sanjaydoc.github.io/LabSuite/app/) — it runs entirely in
your browser as a faithful mirror of the Python engine, so you can click through
onboarding, access resolution, and reviews with nothing installed.

<table>
  <tr>
    <td width="50%"><img src="docs/media/dashboard-explorer.png" alt="Access explorer resolving a user's effective AD groups and access, and testing an allow/deny decision" /></td>
    <td width="50%"><img src="docs/media/dashboard-review.png" alt="Access review board with anomaly flags" /></td>
  </tr>
  <tr>
    <td align="center"><sub><b>Access explorer</b> — resolve Okta → AD (with nested groups ⤴) → TrueNAS + Proxmox, and test a single decision with its reason.</sub></td>
    <td align="center"><sub><b>Access review</b> — quarterly-style entitlements with flags for stale access, FULL on sensitive shares, and datacenter admin.</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/media/dashboard-overview.png" alt="Dashboard overview with stat tiles and the identity chain" /></td>
    <td width="50%"><img src="docs/media/dashboard-onboard.png" alt="Onboard form and the provisioned result" /></td>
  </tr>
  <tr>
    <td align="center"><sub><b>Overview</b> — the seeded lab at a glance.</sub></td>
    <td align="center"><sub><b>Onboard</b> — the form and the resolved downstream access it produces.</sub></td>
  </tr>
</table>

## What makes it more than a toy

| | Typical script | **LabSuite** |
|---|---|---|
| Scope | One system | **The whole chain**, Okta → AD → TrueNAS + Proxmox, as one control plane |
| Sync | Fire-and-forget | **Idempotent SCIM reconcile** — second run is a provable no-op |
| Groups | Flat | **Nested AD groups** with transitive-closure resolution |
| Offboarding | "removed the account" | **Same-day deprovision that *verifies* zero residual access** |
| Decisions | Ad hoc | **Audited allow/deny** with the exact groups that granted it |
| Governance | — | **Quarterly access review** with anomaly flags (stale access, FULL on sensitive, datacenter admin) |
| Coupling | Hard-wired | **Swappable adapters** — drop in real Okta/AD/TrueNAS/Proxmox behind the same interface |
| Deps | Varies | **Zero** for the core; runs in CI on 3.10–3.12 |

## Quickstart

**Requires Python 3.10, 3.11, or 3.12.** The core has **zero third-party
dependencies**; the `.[api]` extra (FastAPI + Uvicorn) is only for the live HTTP
API and web GUI.

<details open>
<summary><b>macOS</b></summary>

```bash
git clone https://github.com/sanjaydoc/LabSuite.git
cd LabSuite

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"          # core + API + test tooling  (or just: pip install -e .)

labsuite demo                    # see the whole thing work end-to-end
```
</details>

<details>
<summary><b>Linux</b></summary>

```bash
git clone https://github.com/sanjaydoc/LabSuite.git
cd LabSuite

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"          # core + API + test tooling  (or just: pip install -e .)

labsuite demo
```
> If `python3 -m venv` fails, install the venv package first: `sudo apt install python3-venv`.
</details>

<details>
<summary><b>Windows</b> (PowerShell)</summary>

Need Python 3.12? Install it with `winget install -e --id Python.Python.3.12`, then **reopen PowerShell**.

```powershell
git clone https://github.com/sanjaydoc/LabSuite.git
cd LabSuite

py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass   # allow venv activation (this window only)
.\.venv\Scripts\Activate.ps1                                 # prompt now starts with (.venv)
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"                            # core + API + tests  (or just: -e .)

labsuite demo
```

**Reopening later** — in a fresh PowerShell window, reactivate the venv before using LabSuite:
```powershell
cd "C:\path\to\LabSuite"                                     # e.g. "C:\Users\ADMIN\Desktop\All Apps\LabSuite"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```
> - `Set-ExecutionPolicy -Scope Process ...` only affects that one window (it resets when you close it), so it's safe.
> - Quote any path containing spaces: `cd "C:\Users\ADMIN\Desktop\All Apps\LabSuite"`.
> - On `cmd.exe` instead of PowerShell, activate with `.\.venv\Scripts\activate.bat` (no execution-policy step needed).
</details>

Drive individual operations:

```bash
labsuite org                                        # the seeded lab
labsuite onboard --name "Nadia Rahman" --department In-Vivo --role invivo-scientist
labsuite access --user nrahman                      # everything she can touch (+ training status)
labsuite check --user nrahman --system truenas --resource invivo-study-data --action modify
labsuite train --user nrahman --training IACUC      # complete a training (unlocks gated access)
labsuite offboard --user nrahman                    # same-day, verified clean

# governance + operations
labsuite review                                     # access review + anomaly flags
labsuite compliance                                 # training records
labsuite devices                                    # managed laptop fleet
labsuite ops                                        # ops dashboard (SaaS spend + flags)
labsuite saas | assets | inventory | vendors | safety
labsuite audit --tail 15                            # the audit trail
labsuite serve                                      # live FastAPI API + GUI (needs .[api])
```

**Departments:** Bio, In-Vivo, Delivery, Platform, Data-Science, Lab, Lab-Ops,
Compliance, Facilities, Legal, IT.
**Roles:** ml-scientist, research-scientist, invivo-scientist, vector-core,
platform-engineer, lab-technician, lab-ops, compliance, facilities,
legal-counsel, it-admin.

Add `--state lab.json` to any command to persist the control plane between runs.

## What the demo shows

`labsuite demo` runs a scripted story and prints each step:

```
2. Onboard a new in-vivo scientist
   created nrahman (nrahman@lab.local), temp password bright-quartz-1227
   Okta groups: Everyone, In-Vivo  (In-Vivo -> Research via AD nesting)
   -> TrueNAS: {'home','research-data','eln','sequencing-imaging','model-artifacts','vector-core-lims'}
   -> Device: MacBook Pro 14 · mac-standard · FileVault + Okta Verify + Iru
   -> Training required (pending): Data-Handling, Biosafety, IACUC
      gated invivo-study-data until IACUC, Biosafety complete

4. Access decisions (resolved Okta -> AD -> compliance -> resource)
   ALLOW    write research data          (granted via Research)
   DENY     write in-vivo study data     (BLOCKED — training not current: IACUC, Biosafety)
   DENY     read legal contracts         (no group grants sufficient access)

5. Compliance gate: she completes IACUC + biosafety training
   ALLOW    in-vivo study data (now trained)   (granted via In-Vivo)

6. Offboard her -- same-day, verified
   deprovisioned across Okta + AD + downstream -> CLEAN: zero residual access
```

Two tells here. **Nesting:** nobody grants `In-Vivo` staff the `Research` shares
directly — `In-Vivo` is *nested inside* `Research`, so the entitlement is
inherited with no per-user grant to forget. **Compliance gating:** her group
membership makes her *eligible* for in-vivo study data, but access is withheld
until IACUC + biosafety training is current — and it would auto-revoke the moment
that training lapses.

## Swappable by design

Every layer is reached through an abstract **provider interface** (`adapters/base.py`).
The control plane and the SCIM engine depend *only* on those interfaces:

```
IdentityProvider   ← OktaDirectory (in-memory)    →  OktaApiIdentityProvider   (real Okta REST API)
DirectoryProvider  ← ActiveDirectory (in-memory)  →  LdapDirectoryProvider     (real AD via LDAP)
StorageProvider    ← TrueNAS (in-memory)          →  TrueNasApiStorageProvider (real TrueNAS API)
ComputeProvider    ← Proxmox (in-memory)          →  ProxmoxApiComputeProvider (real PVE API)
```

The real-system skeletons live in [`adapters/live.py`](src/labsuite/adapters/live.py):
each method documents the exact API call / cmdlet it would make. Going live is
"fill in these bodies and construct `ControlPlane(okta=..., ad=..., ...)`" — no
change anywhere else.

## How it maps to the real stack

LabSuite is a reference implementation; here is where each simulated piece meets
reality, and where the boundaries are drawn honestly:

| LabSuite | Real system | Swap point |
|---|---|---|
| `OktaDirectory` HMAC session token | Okta OIDC / `/api/v1` + SCIM | `OktaApiIdentityProvider` |
| `ActiveDirectory` nested groups | AD security groups, `LDAP_MATCHING_RULE_IN_CHAIN` | `LdapDirectoryProvider` |
| `TrueNAS` share ACLs | TrueNAS `/api/v2.0` dataset ACLs (AD SIDs) | `TrueNasApiStorageProvider` |
| `Proxmox` path ACLs / roles | PVE `/api2/json/access/acl` | `ProxmoxApiComputeProvider` |
| `DeviceFleet` laptop images | MDM — Kandji / Jamf / Intune | `MdmEndpointProvider` |

**Endpoint / devices:** onboarding also **images and ships a managed laptop** per
role (`mac-standard`, `win-lab`, `mac-admin` — OS, disk encryption, MDM, Okta MFA,
and Iru/Ansible config management), and offboarding flags it **wipe & return** —
the "laptops imaged and shipped on day one, deprovisioned same-day" half of the
brief.

**Still out of scope, on purpose:** networking (VLANs, Wi-Fi) and SaaS-contract /
license-cost tracking — candidate future modules, not simulated here.

## In production: PowerShell / Ansible / Terraform

LabSuite implements the *logic* of the chain in one place so it runs and tests
anywhere. In a real environment you drive the actual systems with the standard IT
toolkit — imperative **PowerShell** (AD/Okta), declarative **Ansible** (config
management), and **Terraform** (infrastructure-as-code). Every `labsuite` verb
maps to a concrete real command:

| Operation | Real-world equivalent |
|---|---|
| `labsuite onboard` | `New-ADUser` + `Add-ADGroupMember` · `microsoft.ad.user` · `okta_user` (Terraform) |
| `labsuite offboard` | `Disable-ADAccount` + strip memberships · Okta `/lifecycle/deactivate` |
| `ad.nest_group(...)` | `Add-ADGroupMember -Identity GPU-Cluster-Users -Members Research` |
| `truenas.grant(...)` | TrueNAS `filesystem.setacl` (REST / `midclt`) |
| `proxmox.grant(...)` | `pveum acl modify /pool/research --groups ... --roles PVEVMUser` |
| `labsuite review` | `Get-ADGroupMember -Recursive` report, scheduled |

**→ See [`docs/REAL_TOOLING.md`](docs/REAL_TOOLING.md)** for the full side-by-side
command reference for every operation.

## How it fits the "first IT hire" brief

| What the role asks for | Where it lives in LabSuite |
|---|---|
| **Onboarding/offboarding end-to-end; deprovision same-day** | `ControlPlane.onboard` / `offboard` — the latter *verifies* zero residual access |
| **Laptops imaged & shipped day one; wiped on exit** | `endpoints.py` — image-per-role fleet, wipe & return at offboard |
| **Inherit and document Okta → AD → TrueNAS + Proxmox, then operate it** | The entire project + [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| **Scalable, auditable processes** | Append-only `AuditLog`; every mutation and decision recorded |
| **Okta admin: SSO, SCIM, group design** | `okta.py` + the idempotent `ScimSync` engine |
| **AD admin: group policy, permissions, nesting** | `active_directory.py` — nested groups + transitive resolution |
| **Source of truth for who has access to what and what it costs** | `operations.py` SaaS module — seats + monthly/annual spend + orphaned seats |
| **Baseline security hygiene; quarterly access reviews** | `access_review` anomaly flags + compliance-gated access (`compliance.py`) |
| **Bias toward automation; document what you build** | One-command `onboard`/`offboard`; typed, tested, CI-green, documented |

## Project layout

```
labsuite/
├── src/labsuite/
│   ├── okta.py              # 1 · identity / login (source of truth, auth, SCIM source)
│   ├── active_directory.py  # 2 · users / groups / permissions (nested-group resolution)
│   ├── scim.py              #     the Okta -> AD sync engine (idempotent reconcile)
│   ├── truenas.py           # 3a · file storage (group-based share ACLs)
│   ├── proxmox.py           # 3b · VMs / servers (hierarchical path ACLs + roles)
│   ├── endpoints.py         # managed laptop fleet (image per role, wipe & return)
│   ├── compliance.py        # training records that gate sensitive access
│   ├── operations.py        # SaaS+cost · equipment · inventory · vendors · safety
│   ├── engine.py            # the ControlPlane — onboard/offboard/resolve/check/review/ops
│   ├── policy.py            # role blueprints, image catalog, training + gating rules
│   ├── audit.py             # append-only audit log
│   ├── crypto.py            # PBKDF2 passwords + HS256 session tokens (stdlib only)
│   ├── seed.py              # the seeded Merge-Labs org, onboarded through the real path
│   ├── cli.py               # the `labsuite` command-line control plane
│   ├── api.py               # optional FastAPI: OIDC-ish + SCIM + admin + ops + GUI
│   └── adapters/            # base.py (interfaces) · live.py (real-system skeletons)
├── tests/                   # crypto · SCIM · nesting · access · devices · compliance · ops · e2e · API
├── docs/                    # ARCHITECTURE.md · website (index.html) · GUI (app/)
└── .github/workflows/       # CI (ruff + pytest on 3.10 / 3.11 / 3.12)
```

## License

Code: **MIT** (see [`LICENSE`](LICENSE)). This is an independent portfolio
project and is not affiliated with Okta, TrueNAS, Proxmox, or Microsoft.
