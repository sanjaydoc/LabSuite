"""LabSuite -- an identity-to-infrastructure access-governance control plane.

LabSuite models one specific, common lab/enterprise identity stack and operates
it end-to-end:

    Okta (identity / login layer)
        |  syncs with  (SCIM)
        v
    Active Directory (users / groups / permissions)
        |  controls access to
        v
    TrueNAS (file storage / NAS)   +   Proxmox (VMs / internal servers)

The core is pure-Python and dependency-free. See ``labsuite.engine.ControlPlane``
for the orchestrator and ``labsuite.seed.build_lab`` for a realistic seeded org.
"""

from labsuite.engine import ControlPlane
from labsuite.models import AccessLevel, Department, ProxmoxRole

__version__ = "0.1.0"

__all__ = ["ControlPlane", "AccessLevel", "Department", "ProxmoxRole", "__version__"]
