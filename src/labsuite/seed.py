"""A realistic seeded Merge-Labs-style org, so the whole thing is demonstrable.

``build_lab()`` returns a fully-wired :class:`ControlPlane` modelling Merge's real
team structure (from the job postings): the Bioengineering functional teams (Bio,
In-Vivo, Delivery, Platform, Data-Science) plus G&A (Lab-Ops, Compliance,
Facilities, Legal, IT). Everyone is onboarded through the real ``onboard`` path --
so their access, device, and training are produced by the same code a live hire
would go through -- and established staff have their required training completed.
"""

from __future__ import annotations

from labsuite.engine import ControlPlane
from labsuite.models import AccessLevel, Department, ProxmoxRole
from labsuite.operations import Equipment, InventoryItem, SafetyCheck, Vendor
from labsuite.policy import required_trainings

DEMO_PASSWORD = "Winter-Harbor-2026"  # noqa: S105 (demo-only shared password)

# (display_name, department, role, title)
SEED_PEOPLE: list[tuple[str, Department, str, str]] = [
    ("Alice Nguyen", Department.BIO, "research-scientist", "Scientist – Cell Biology"),
    ("Grace Kim", Department.BIO, "research-scientist", "Scientist – Protein Engineering"),
    ("Ravi Patel", Department.DATA_SCIENCE, "ml-scientist", "ML Research Scientist – De Novo Design"),
    ("Mei Chen", Department.DATA_SCIENCE, "ml-scientist", "ML Research Scientist – Bayesian Optimization"),
    ("Tom Alvarez", Department.INVIVO, "invivo-scientist", "Scientist – In Vivo Operations"),
    ("Nadia Osei", Department.INVIVO, "invivo-scientist", "Research Associate – In Vivo"),
    ("Bob Okafor", Department.PLATFORM, "platform-engineer", "Automation Engineer"),
    ("Carol Diaz", Department.LAB, "lab-technician", "Histology Technician"),
    ("Dev Rao", Department.DELIVERY, "vector-core", "Research Associate – Vector Core"),
    ("Lena Park", Department.LAB_OPS, "lab-ops", "Lab Operations Associate"),
    ("Ivan Petrov", Department.COMPLIANCE, "compliance", "IACUC Coordinator"),
    ("Fiona Reed", Department.FACILITIES, "facilities", "Facilities Operations"),
    ("Erin Park", Department.LEGAL, "legal-counsel", "Counsel"),
    ("Sanjay Anbu", Department.IT, "it-admin", "IT Systems Engineer (Admin of Record)"),
]


def _wire_truenas(cp: ControlPlane) -> None:
    nas = cp.truenas
    nas.add_share("home", "tank/home", "Per-user home directories")
    nas.grant("home", "Everyone", AccessLevel.READ)

    nas.add_share("research-data", "tank/research", "Shared research datasets")
    nas.grant("research-data", "Research", AccessLevel.MODIFY)
    nas.grant("research-data", "Domain-Admins", AccessLevel.FULL)

    nas.add_share("eln", "tank/eln", "Electronic lab notebook exports")
    nas.grant("eln", "Research", AccessLevel.MODIFY)

    nas.add_share("sequencing-imaging", "tank/seq", "Sequencing + microscopy data")
    nas.grant("sequencing-imaging", "Bio", AccessLevel.MODIFY)
    nas.grant("sequencing-imaging", "Research", AccessLevel.READ)

    nas.add_share("gpu-scratch", "tank/scratch", "Scratch space for the GPU cluster")
    nas.grant("gpu-scratch", "GPU-Cluster-Users", AccessLevel.MODIFY)  # via AD nesting

    nas.add_share("model-artifacts", "tank/models", "Trained model artifacts")
    nas.grant("model-artifacts", "Data-Science", AccessLevel.MODIFY)
    nas.grant("model-artifacts", "Research", AccessLevel.READ)

    nas.add_share("invivo-study-data", "tank/invivo", "In-vivo study data (IACUC-controlled)", sensitive=True)
    nas.grant("invivo-study-data", "In-Vivo", AccessLevel.MODIFY)
    nas.grant("invivo-study-data", "Compliance-Read", AccessLevel.READ)
    nas.grant("invivo-study-data", "Domain-Admins", AccessLevel.FULL)

    nas.add_share("vector-core-lims", "tank/vector", "AAV vector-core lot records / LIMS")
    nas.grant("vector-core-lims", "Delivery", AccessLevel.MODIFY)
    nas.grant("vector-core-lims", "Research", AccessLevel.READ)

    nas.add_share("lab-raw-signals", "tank/lab", "Raw acquisition data from the lab rigs")
    nas.grant("lab-raw-signals", "Lab", AccessLevel.MODIFY)
    nas.grant("lab-raw-signals", "Research", AccessLevel.READ)
    nas.grant("lab-raw-signals", "Domain-Admins", AccessLevel.FULL)

    nas.add_share("platform-artifacts", "tank/platform", "Instrument images + build artifacts")
    nas.grant("platform-artifacts", "Platform", AccessLevel.MODIFY)
    nas.grant("platform-artifacts", "CI-Operators", AccessLevel.READ)
    nas.grant("platform-artifacts", "Domain-Admins", AccessLevel.FULL)

    nas.add_share("asset-db", "tank/assets", "Lab-ops asset & inventory database")
    nas.grant("asset-db", "Lab-Ops", AccessLevel.MODIFY)

    nas.add_share("legal-contracts", "tank/legal", "Executed contracts (sensitive)", sensitive=True)
    nas.grant("legal-contracts", "Legal-Privileged", AccessLevel.FULL)


def _wire_proxmox(cp: ControlPlane) -> None:
    pve = cp.proxmox
    pve.add_vm(101, "train-a100-01", "pve-gpu-01", pool="research")
    pve.add_vm(102, "train-a100-02", "pve-gpu-02", pool="research")
    pve.add_vm(201, "daq-primary", "pve-lab-01", pool="lab")
    pve.add_vm(301, "ci-runner-01", "pve-ci-01", pool="ci")
    pve.add_vm(900, "dc01", "pve-lab-01", pool="infra")  # the domain controller

    pve.grant("/", "Domain-Admins", ProxmoxRole.PVE_ADMIN)  # full datacenter
    pve.grant("/", "Compliance-Read", ProxmoxRole.PVE_AUDITOR)  # read-only audit
    pve.grant("/pool/research", "GPU-Cluster-Users", ProxmoxRole.PVE_VM_USER)
    pve.grant("/pool/research", "Platform", ProxmoxRole.PVE_VM_ADMIN)  # platform runs the cluster
    pve.grant("/pool/lab", "Lab-DAQ-Operators", ProxmoxRole.PVE_VM_USER)
    pve.grant("/pool/ci", "CI-Operators", ProxmoxRole.PVE_VM_ADMIN)


def _wire_ad_nesting(cp: ControlPlane) -> None:
    ad = cp.ad
    # "Research" is composed of the scientific teams, so any scientist is
    # effectively in Research without a per-user grant.
    ad.ensure_group("Research", "All research staff", ou="OU=ResourceGroups")
    for team in ("Bio", "In-Vivo", "Delivery", "Platform", "Data-Science"):
        ad.nest_group("Research", team)
    # GPU cluster = ML + automation.
    ad.ensure_group("GPU-Cluster-Users", "GPU training cluster access", ou="OU=ResourceGroups")
    ad.nest_group("GPU-Cluster-Users", "Data-Science")
    ad.nest_group("GPU-Cluster-Users", "Platform")
    # Lab data-acquisition operators.
    ad.ensure_group("Lab-DAQ-Operators", "Lab data-acquisition operators", ou="OU=ResourceGroups")
    ad.nest_group("Lab-DAQ-Operators", "Lab")


def _wire_operations(cp: ControlPlane) -> None:
    ops = cp.ops
    # Equipment (maintenance_in_days: negative = overdue, small = due soon).
    ops.equipment = [
        Equipment("EQ-001", "Leica Cryostat CM1950", "Histology", "Lab", 5),
        Equipment("EQ-002", "Illumina NextSeq 2000", "Sequencing", "Bio", 40),
        Equipment("EQ-003", "Zeiss LSM 980 Confocal", "Microscopy", "Bio", -3),  # overdue
        Equipment("EQ-004", "Thermo -80C Freezer A", "Cold Storage", "Lab-Ops", 12),
        Equipment("EQ-005", "Hamilton STAR Liquid Handler", "Automation", "Platform", 60),
        Equipment("EQ-006", "Sartorius Analytical Balance", "Lab", "Lab-Ops", -10),  # overdue cal
        Equipment("EQ-007", "Stereotaxic Surgery Rig", "In-Vivo Suite", "In-Vivo", 20),
    ]
    ops.inventory = [
        InventoryItem("RG-DMEM", "DMEM media", "Cold Room 2", 8, 10, "bottles"),  # low
        InventoryItem("RG-FBS", "Fetal Bovine Serum", "-20C Freezer", 25, 10, "bottles"),
        InventoryItem("RG-AAV9", "AAV9 packaging kit", "Vector Core", 2, 3, "kits"),  # low
        InventoryItem("RG-PBS", "PBS 1x", "Bench 4", 40, 12, "bottles"),
        InventoryItem("RG-ISO", "Isoflurane", "In-Vivo Suite", 4, 6, "bottles"),  # low
        InventoryItem("RG-TIP", "Filter tips 200uL", "Consumables", 60, 20, "boxes"),
    ]
    ops.vendors = [
        Vendor("Okta", "Identity", 210, 42000),
        Vendor("Benchling", "ELN / LIMS", 35, 66000),  # upcoming renewal
        Vendor("Illumina", "Sequencing reagents", 120, 180000),
        Vendor("Charles River", "Animal supply", 200, 240000),
        Vendor("Kandji", "MDM", 15, 9000),  # upcoming renewal
        Vendor("Slack", "Comms", 320, 15000),
    ]
    ops.safety = [
        SafetyCheck("BSL-2 Lab", "Biosafety cabinet certification current", "pass"),
        SafetyCheck("Chem Store", "Flammables cabinet grounded", "pass"),
        SafetyCheck("In-Vivo Suite", "Sharps container <75% full", "open", "overfull at rig 2"),
        SafetyCheck("Cold Room 2", "Emergency egress clear", "open", "boxes blocking exit"),
        SafetyCheck("Loading Dock", "Eyewash station flushed weekly", "pass"),
    ]


def build_lab() -> ControlPlane:
    """Build and return a fully-populated control plane for the demo lab."""
    cp = ControlPlane()
    _wire_truenas(cp)
    _wire_proxmox(cp)
    _wire_ad_nesting(cp)
    _wire_operations(cp)

    for display_name, department, role, title in SEED_PEOPLE:
        result = cp.onboard(display_name, department, role, title=title)
        cp.okta.set_password(result.username, DEMO_PASSWORD)
        # Established employees have completed training and enrolled MFA, so their
        # gated + conditional-access resources are live. A *fresh* onboard starts
        # with neither -- which is what demonstrates the gating.
        for training in required_trainings(role):
            cp.complete_training(result.username, training, actor="system")
        cp.enroll_mfa(result.username, actor="system")

    return cp
