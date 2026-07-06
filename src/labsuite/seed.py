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


def _wire_network(cp: ControlPlane) -> None:
    net = cp.network
    # VLAN segments: trust descends Corp > Lab > IoT, Guest is untrusted.
    net.add_segment("Corp", 10, "10.10.0.0/16", "Staff laptops & workstations", trust="high")
    net.add_segment("Lab", 20, "10.20.0.0/16", "Lab instruments & DAQ", trust="medium")
    net.add_segment("IoT", 30, "10.30.0.0/16", "Cameras, sensors, badge readers", trust="low", internet=False)
    net.add_segment("Guest", 40, "10.40.0.0/16", "Guest Wi-Fi (internet only)", trust="none")
    net.add_segment("Mgmt", 99, "10.99.99.0/24", "Infra management (Proxmox/TrueNAS/DC)", trust="high", internet=False)

    # Default-deny firewall; only these east-west flows are permitted.
    net.allow("Corp", "Lab")  # staff reach instruments
    net.allow("Corp", "IoT")  # IT manages IoT controllers
    net.allow("Corp", "Mgmt")  # admins reach the infra plane
    net.allow("Lab", "Mgmt")  # instruments push acquisitions to storage
    # IoT and Guest originate nothing internal -- that's the segmentation.

    # Staff laptops on Corp.
    net.add_device("anguyen-mbp", "02:00:00:00:00:01", "laptop", "Corp", owner="anguyen", ip="10.10.4.21")
    net.add_device("rpatel-mbp", "02:00:00:00:00:02", "laptop", "Corp", owner="rpatel", ip="10.10.4.22")
    net.add_device("gpu-ws-01", "02:00:00:00:00:03", "workstation", "Corp", ip="10.10.5.10")
    # Lab instruments on Lab.
    net.add_device("nextseq-2000", "02:00:00:00:00:11", "instrument", "Lab", ip="10.20.1.5")
    net.add_device("confocal-lsm980", "02:00:00:00:00:12", "instrument", "Lab", ip="10.20.1.6")
    net.add_device("daq-rig-2", "02:00:00:00:00:13", "instrument", "Lab", ip="10.20.1.7")
    net.add_device("lab-printer-1", "02:00:00:00:00:14", "printer", "Lab", ip="10.20.2.9")
    # IoT endpoints on IoT.
    net.add_device("cam-vivarium-1", "02:00:00:00:00:21", "camera", "IoT", ip="10.30.7.11")
    net.add_device("cam-loading-dock", "02:00:00:00:00:22", "camera", "IoT", ip="10.30.7.12")
    net.add_device("badge-main-door", "02:00:00:00:00:23", "badge-reader", "IoT", ip="10.30.8.4")
    net.add_device("freezer-temp-1", "02:00:00:00:00:24", "sensor", "IoT", ip="10.30.9.30")
    # Guest device.
    net.add_device("visitor-phone", "02:00:00:00:00:31", "guest", "Guest", ip="10.40.1.55")
    # A deliberate misconfiguration: a camera cabled into the Corp VLAN -- the
    # exact IoT-segmentation failure the review is meant to surface.
    net.add_device("cam-server-room", "02:00:00:00:00:25", "camera", "Corp", ip="10.10.9.99")


def _wire_backups(cp: ControlPlane) -> None:
    bk = cp.backups
    # TrueNAS datasets: hourly/daily snapshots replicated off-box. A couple are
    # deliberately stale to demonstrate the DR flag.
    bk.add("tank/research", "dataset", "hourly", 2, retention_days=30, target="replication")
    bk.add("tank/invivo", "dataset", "hourly", 9, retention_days=90, target="replication")  # STALE (>6h)
    bk.add("tank/eln", "dataset", "daily", 20, retention_days=30, target="pbs")
    bk.add("tank/models", "dataset", "daily", 30, retention_days=30, target="cloud")
    bk.add("tank/legal", "dataset", "daily", 50, retention_days=365, target="cloud")  # STALE (>36h)
    bk.add("tank/lab", "dataset", "daily", 12, retention_days=30, target="replication")
    # Proxmox VMs: nightly PBS backups.
    bk.add("vm/101", "vm", "daily", 10, retention_days=14, target="pbs")
    bk.add("vm/201", "vm", "daily", 22, retention_days=14, target="pbs")
    bk.add("vm/900", "vm", "hourly", 3, retention_days=7, target="pbs")  # the domain controller
    bk.add("vm/301", "vm", "weekly", 210, retention_days=30, target="pbs")  # STALE (>192h)


def build_lab() -> ControlPlane:
    """Build and return a fully-populated control plane for the demo lab."""
    cp = ControlPlane()
    _wire_truenas(cp)
    _wire_proxmox(cp)
    _wire_ad_nesting(cp)
    _wire_operations(cp)
    _wire_network(cp)
    _wire_backups(cp)

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
