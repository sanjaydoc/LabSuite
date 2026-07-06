"""A realistic seeded lab, so the whole thing is demonstrable in one call.

``build_lab()`` returns a fully-wired :class:`ControlPlane`:

* TrueNAS shares and Proxmox guests with group-based ACLs;
* Active Directory *nesting* that composes resource-access groups out of the
  department groups (``GPU-Cluster-Users`` contains ``Research`` and
  ``Platform``; ``Lab-DAQ-Operators`` contains ``Lab``);
* a small org onboarded through the real ``onboard`` path, so their access is
  produced by the same code a live hire would go through.

For predictable login demos, every seeded account's password is set to
``DEMO_PASSWORD`` after onboarding (clearly a demo convenience, not a pattern).
"""

from __future__ import annotations

from labsuite.engine import ControlPlane
from labsuite.models import AccessLevel, Department, ProxmoxRole

DEMO_PASSWORD = "Winter-Harbor-2026"  # noqa: S105 (demo-only shared password)

# (display_name, department, role, title)
SEED_PEOPLE: list[tuple[str, Department, str, str]] = [
    ("Alice Nguyen", Department.RESEARCH, "research-scientist", "Research Scientist"),
    ("Grace Kim", Department.RESEARCH, "research-scientist", "Research Scientist"),
    ("Bob Okafor", Department.PLATFORM, "platform-engineer", "Platform Engineer"),
    ("Carol Diaz", Department.LAB, "lab-technician", "Lab Technician"),
    ("Dan Levy", Department.OPERATIONS, "operations", "Operations Coordinator"),
    ("Erin Park", Department.LEGAL, "legal-counsel", "Counsel"),
    ("Frank Ito", Department.SECURITY, "security-analyst", "Security Analyst"),
    ("Sanjay Anbu", Department.IT, "it-admin", "IT Systems Engineer (Admin of Record)"),
]


def _wire_truenas(cp: ControlPlane) -> None:
    nas = cp.truenas
    nas.add_share("research-data", "tank/research", "Shared research datasets")
    nas.grant("research-data", "Research", AccessLevel.MODIFY)
    nas.grant("research-data", "Platform", AccessLevel.READ)
    nas.grant("research-data", "Domain-Admins", AccessLevel.FULL)

    nas.add_share("gpu-scratch", "tank/scratch", "Scratch space for the GPU cluster")
    nas.grant("gpu-scratch", "GPU-Cluster-Users", AccessLevel.MODIFY)  # reached via AD nesting

    nas.add_share("lab-raw-signals", "tank/lab", "Raw acquisition data from the lab rigs")
    nas.grant("lab-raw-signals", "Lab", AccessLevel.MODIFY)
    nas.grant("lab-raw-signals", "Research", AccessLevel.READ)
    nas.grant("lab-raw-signals", "Domain-Admins", AccessLevel.FULL)

    nas.add_share("platform-artifacts", "tank/platform", "Build artifacts and images")
    nas.grant("platform-artifacts", "Platform", AccessLevel.MODIFY)
    nas.grant("platform-artifacts", "CI-Operators", AccessLevel.READ)
    nas.grant("platform-artifacts", "Domain-Admins", AccessLevel.FULL)

    nas.add_share("legal-contracts", "tank/legal", "Executed contracts (sensitive)", sensitive=True)
    nas.grant("legal-contracts", "Legal-Privileged", AccessLevel.FULL)

    nas.add_share("home", "tank/home", "Per-user home directories")
    nas.grant("home", "Everyone", AccessLevel.READ)


def _wire_proxmox(cp: ControlPlane) -> None:
    pve = cp.proxmox
    pve.add_vm(101, "train-a100-01", "pve-gpu-01", pool="research")
    pve.add_vm(102, "train-a100-02", "pve-gpu-02", pool="research")
    pve.add_vm(201, "daq-primary", "pve-lab-01", pool="lab")
    pve.add_vm(301, "ci-runner-01", "pve-ci-01", pool="ci")
    pve.add_vm(900, "dc01", "pve-lab-01", pool="infra")  # the domain controller

    pve.grant("/", "Domain-Admins", ProxmoxRole.PVE_ADMIN)  # full datacenter
    pve.grant("/", "Security", ProxmoxRole.PVE_AUDITOR)  # read-only audit everywhere
    pve.grant("/pool/research", "GPU-Cluster-Users", ProxmoxRole.PVE_VM_USER)
    pve.grant("/pool/research", "Platform", ProxmoxRole.PVE_VM_ADMIN)  # platform runs the cluster
    pve.grant("/pool/lab", "Lab-DAQ-Operators", ProxmoxRole.PVE_VM_USER)
    pve.grant("/pool/ci", "CI-Operators", ProxmoxRole.PVE_VM_ADMIN)


def _wire_ad_nesting(cp: ControlPlane) -> None:
    ad = cp.ad
    # Resource-access groups are *composed in AD* from the department groups, so
    # membership is inherited -- no per-user grant to maintain.
    ad.ensure_group("GPU-Cluster-Users", "GPU training cluster access", ou="OU=ResourceGroups")
    ad.nest_group("GPU-Cluster-Users", "Research")
    ad.nest_group("GPU-Cluster-Users", "Platform")
    ad.ensure_group("Lab-DAQ-Operators", "Lab data-acquisition operators", ou="OU=ResourceGroups")
    ad.nest_group("Lab-DAQ-Operators", "Lab")


def build_lab() -> ControlPlane:
    """Build and return a fully-populated control plane for the demo lab."""
    cp = ControlPlane()
    _wire_truenas(cp)
    _wire_proxmox(cp)
    _wire_ad_nesting(cp)

    for display_name, department, role, title in SEED_PEOPLE:
        result = cp.onboard(display_name, department, role, title=title)
        cp.okta.set_password(result.username, DEMO_PASSWORD)

    return cp
