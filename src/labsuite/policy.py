"""Provisioning policy: the source of truth for *who gets what by default*.

Onboarding should never be a judgement call made from memory. A role blueprint
maps a (department, role) to the exact set of Okta groups a new hire receives, so
that "provision access cleanly" is deterministic, reviewable, and identical every
time. This is the object an IT owner edits when access should change -- not a
pile of manual group clicks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from labsuite.models import Department, DeviceImage

# Every employee is in this baseline group (SSO to the standard SaaS bundle, MFA
# enforcement, etc. in a real Okta org).
BASELINE_GROUP = "Everyone"

# Department -> the Okta group that represents "works in this team".
DEPARTMENT_GROUP: dict[Department, str] = {dept: dept.value for dept in Department}


@dataclass(frozen=True)
class RoleBlueprint:
    """The default entitlements for a named role."""

    role: str
    description: str
    extra_groups: tuple[str, ...] = field(default_factory=tuple)


# Named roles a new hire can be onboarded as. ``extra_groups`` are the Okta groups
# assigned *directly* on top of the baseline + department group. Note what is NOT
# here: resource-access groups like GPU-Cluster-Users and Lab-DAQ-Operators are
# *derived in Active Directory* by nesting the department groups inside them (see
# ``seed.build_lab``), so a researcher gets GPU access by virtue of being in
# Research -- no per-user grant to maintain. That is the whole point of AD nesting.
ROLE_BLUEPRINTS: dict[str, RoleBlueprint] = {
    "ml-scientist": RoleBlueprint(
        "ml-scientist",
        "ML research; GPU cluster + model-artifact storage (GPU via Data-Science nesting).",
    ),
    "research-scientist": RoleBlueprint(
        "research-scientist",
        "Bench scientist (cell bio / protein eng / immuno); ELN + sequencing/imaging.",
    ),
    "invivo-scientist": RoleBlueprint(
        "invivo-scientist",
        "In-vivo studies; IACUC-gated study data + surgical scheduling.",
    ),
    "vector-core": RoleBlueprint(
        "vector-core",
        "AAV vector core; vector-core LIMS + lot-record storage.",
    ),
    "platform-engineer": RoleBlueprint(
        "platform-engineer",
        "Research-platform automation; instrument/CI VMs (GPU via Platform nesting).",
        extra_groups=("CI-Operators",),
    ),
    "lab-technician": RoleBlueprint(
        "lab-technician",
        "Histology / bench tech; slide + imaging storage.",
        extra_groups=("Histology",),
    ),
    "lab-ops": RoleBlueprint(
        "lab-ops",
        "Lab operations; asset DB, inventory, procurement (no research data).",
    ),
    "compliance": RoleBlueprint(
        "compliance",
        "IACUC coordinator; read-only across study data + the training system.",
        extra_groups=("Compliance-Read",),
    ),
    "facilities": RoleBlueprint(
        "facilities",
        "Facilities & workplace ops; no research data.",
    ),
    "legal-counsel": RoleBlueprint(
        "legal-counsel",
        "Legal; access to the (sensitive) contracts share.",
        extra_groups=("Legal-Privileged",),
    ),
    "it-admin": RoleBlueprint(
        "it-admin",
        "IT administrator / admin of record; broad administrative access.",
        extra_groups=("Domain-Admins",),
    ),
}


def onboarding_groups(department: Department, role: str) -> list[str]:
    """The exact Okta groups a (department, role) new hire should receive."""
    if role not in ROLE_BLUEPRINTS:
        raise KeyError(f"unknown role {role!r}; known roles: {sorted(ROLE_BLUEPRINTS)}")
    groups = [BASELINE_GROUP, DEPARTMENT_GROUP[department]]
    groups.extend(ROLE_BLUEPRINTS[role].extra_groups)
    # Stable, de-duplicated order.
    seen: dict[str, None] = {}
    for g in groups:
        seen.setdefault(g, None)
    return list(seen)


# --------------------------------------------------------------------------- #
# Endpoint policy: which standardized laptop build (image) a role receives.
#
# This is the "laptops imaged and shipped on day one" half of onboarding -- the
# endpoint pillar. Each image is a full managed build: OS, disk encryption, an
# MDM, MFA, and a config-management agent (Iru for Mac, Ansible/Intune for the
# lab Windows machines), so a new hire's laptop is hardened and ready on arrival.
# --------------------------------------------------------------------------- #
_MAC_APPS = ["Slack", "GlobalProtect VPN", "1Password", "Chrome"]

IMAGE_CATALOG: dict[str, DeviceImage] = {
    "mac-standard": DeviceImage(
        name="mac-standard",
        platform="macOS",
        model="MacBook Pro 14",
        encryption="FileVault",
        mdm="Kandji",
        mfa="Okta Verify",
        config_mgmt="Iru",
        apps=_MAC_APPS,
    ),
    "mac-admin": DeviceImage(
        name="mac-admin",
        platform="macOS",
        model="MacBook Pro 16",
        encryption="FileVault",
        mdm="Kandji",
        mfa="Okta Verify",
        config_mgmt="Iru",
        apps=[*_MAC_APPS, "admin tooling"],
        notes="mac-standard + elevated IT admin tooling",
    ),
    "win-lab": DeviceImage(
        name="win-lab",
        platform="Windows",
        model="Dell Latitude 5550",
        encryption="BitLocker",
        mdm="Intune",
        mfa="Okta Verify",
        config_mgmt="Ansible",
        apps=["Acquisition software", "Slack", "GlobalProtect VPN", "1Password"],
        ad_joined=True,
    ),
}

# Role -> image. Lab-facing Windows systems get win-lab; IT gets the admin build;
# everyone else gets the standard managed Mac.
ROLE_IMAGE: dict[str, str] = {
    "ml-scientist": "mac-standard",
    "research-scientist": "mac-standard",
    "invivo-scientist": "mac-standard",
    "vector-core": "mac-standard",
    "platform-engineer": "mac-standard",
    "lab-technician": "win-lab",
    "lab-ops": "win-lab",
    "compliance": "mac-standard",
    "facilities": "mac-standard",
    "legal-counsel": "mac-standard",
    "it-admin": "mac-admin",
}


def image_for(role: str) -> str:
    """The image a role is provisioned with (defaults to the standard Mac build)."""
    return ROLE_IMAGE.get(role, "mac-standard")


# --------------------------------------------------------------------------- #
# Compliance policy: training-gated access.
#
# Group membership grants *eligibility*; for gated resources, access is only
# actually allowed if the required training is CURRENT. This unifies HR/compliance
# with IT access -- a lab-specific control that generic IdP tooling doesn't model.
# In a bio lab a scientist can't touch in-vivo study data until their IACUC +
# biosafety training is recorded and current, and access auto-revokes if it lapses.
# --------------------------------------------------------------------------- #
TRAININGS = ["Data-Handling", "Biosafety", "Chemical-Safety", "IACUC"]

# Shares that require *current* training on top of the group ACL.
GATED_SHARES: dict[str, list[str]] = {
    "lab-raw-signals": ["Biosafety"],
    "invivo-study-data": ["IACUC", "Biosafety"],
}

# Trainings a role must hold. Assigned pending (missing) at onboarding -- a new
# hire completes them before gated access unlocks.
ROLE_TRAININGS: dict[str, list[str]] = {
    "ml-scientist": ["Data-Handling"],
    "research-scientist": ["Data-Handling", "Biosafety"],
    "invivo-scientist": ["Data-Handling", "Biosafety", "IACUC"],
    "vector-core": ["Data-Handling", "Biosafety"],
    "platform-engineer": ["Data-Handling"],
    "lab-technician": ["Biosafety", "Chemical-Safety"],
    "lab-ops": ["Chemical-Safety"],
    "compliance": ["Data-Handling", "Biosafety", "IACUC"],
    "facilities": ["Chemical-Safety"],
    "legal-counsel": ["Data-Handling"],
    "it-admin": ["Data-Handling"],
}


def required_trainings(role: str) -> list[str]:
    """The trainings a role must hold (defaults to Data-Handling)."""
    return list(ROLE_TRAININGS.get(role, ["Data-Handling"]))
