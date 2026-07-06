"""The operations layer: the "centralized source of truth" for day-to-day ops.

Beyond identity and access, an IT/ops owner runs the SaaS stack, the equipment
and inventory, vendor renewals, and safety. This module models those as simple,
auditable registries with the smart flags a reviewer actually wants:

* **SaaS & licenses** -- who has a seat in what, and *what it costs*; flags
  orphaned seats (inactive users still licensed) and total monthly spend.
* **Equipment** -- lab instruments with maintenance/calibration cadence; flags
  overdue and soon-due.
* **Inventory** -- reagents/consumables with reorder points; flags low stock.
* **Vendors** -- contracts with renewal dates + annual cost; flags upcoming
  renewals.
* **Safety** -- facility checklist items; flags open issues.

Onboarding grants each hire their baseline + role SaaS seats; offboarding
reclaims them (same-day licence deprovisioning).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# app -> monthly cost per seat (USD)
SAAS_CATALOG: dict[str, float] = {
    "Slack": 12.5,
    "Google-Workspace": 18.0,
    "1Password": 8.0,
    "Benchling": 55.0,  # ELN / LIMS
    "GitHub": 21.0,
    "Coupa": 30.0,  # procurement
    "Kandji": 6.0,  # MDM admin
}
SAAS_BASELINE = ["Slack", "Google-Workspace", "1Password"]
SAAS_ROLE_APPS: dict[str, list[str]] = {
    "ml-scientist": ["Benchling", "GitHub"],
    "research-scientist": ["Benchling"],
    "invivo-scientist": ["Benchling"],
    "vector-core": ["Benchling"],
    "platform-engineer": ["GitHub"],
    "lab-technician": ["Benchling"],
    "lab-ops": ["Coupa"],
    "compliance": [],
    "facilities": ["Coupa"],
    "legal-counsel": [],
    "it-admin": ["Kandji", "GitHub"],
}


def saas_apps_for_role(role: str) -> list[str]:
    """Baseline SaaS + the role's add-on apps, de-duplicated."""
    seen: dict[str, None] = {}
    for app in [*SAAS_BASELINE, *SAAS_ROLE_APPS.get(role, [])]:
        seen.setdefault(app, None)
    return list(seen)


@dataclass
class SaasApp:
    name: str
    monthly_cost_per_seat: float
    assignees: set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "monthly_cost_per_seat": self.monthly_cost_per_seat,
            "assignees": sorted(self.assignees),
        }


@dataclass
class Equipment:
    asset_tag: str
    name: str
    location: str
    owner_team: str
    maintenance_in_days: int  # days until next maintenance (negative = overdue)

    def to_dict(self) -> dict:
        return {
            "asset_tag": self.asset_tag,
            "name": self.name,
            "location": self.location,
            "owner_team": self.owner_team,
            "maintenance_in_days": self.maintenance_in_days,
        }


@dataclass
class InventoryItem:
    sku: str
    name: str
    location: str
    qty: int
    reorder_point: int
    unit: str = "unit"

    @property
    def low(self) -> bool:
        return self.qty <= self.reorder_point

    def to_dict(self) -> dict:
        return {
            "sku": self.sku,
            "name": self.name,
            "location": self.location,
            "qty": self.qty,
            "reorder_point": self.reorder_point,
            "unit": self.unit,
            "low": self.low,
        }


@dataclass
class Vendor:
    name: str
    category: str
    renewal_in_days: int
    annual_cost: float

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "renewal_in_days": self.renewal_in_days,
            "annual_cost": self.annual_cost,
        }


@dataclass
class SafetyCheck:
    area: str
    check: str
    status: str  # "pass" | "open"
    note: str = ""

    def to_dict(self) -> dict:
        return {"area": self.area, "check": self.check, "status": self.status, "note": self.note}


class Operations:
    """The ops source of truth: SaaS, equipment, inventory, vendors, safety."""

    def __init__(self) -> None:
        self.saas: dict[str, SaasApp] = {}
        self.equipment: list[Equipment] = []
        self.inventory: list[InventoryItem] = []
        self.vendors: list[Vendor] = []
        self.safety: list[SafetyCheck] = []

    # ---- SaaS ------------------------------------------------------- #
    def ensure_app(self, name: str) -> SaasApp:
        app = self.saas.get(name)
        if app is None:
            app = SaasApp(name=name, monthly_cost_per_seat=SAAS_CATALOG.get(name, 0.0))
            self.saas[name] = app
        return app

    def grant_saas(self, username: str, app: str) -> None:
        self.ensure_app(app).assignees.add(username)

    def provision_role_apps(self, username: str, role: str) -> list[str]:
        apps = saas_apps_for_role(role)
        for app in apps:
            self.grant_saas(username, app)
        return apps

    def revoke_saas_all(self, username: str) -> list[str]:
        removed = [name for name, app in self.saas.items() if username in app.assignees]
        for app in self.saas.values():
            app.assignees.discard(username)
        return sorted(removed)

    def apps_for(self, username: str) -> list[str]:
        return sorted(name for name, app in self.saas.items() if username in app.assignees)

    def monthly_spend(self) -> float:
        return round(sum(app.monthly_cost_per_seat * len(app.assignees) for app in self.saas.values()), 2)

    def orphaned_seats(self, is_active) -> list[tuple[str, str]]:
        """(app, username) seats held by inactive/unknown users -- wasted spend."""
        return sorted(
            (name, u)
            for name, app in self.saas.items()
            for u in app.assignees
            if not is_active(u)
        )

    # ---- Flags ------------------------------------------------------ #
    def overdue_equipment(self) -> list[Equipment]:
        return [e for e in self.equipment if e.maintenance_in_days < 0]

    def due_equipment(self, within: int = 14) -> list[Equipment]:
        return [e for e in self.equipment if 0 <= e.maintenance_in_days <= within]

    def low_stock(self) -> list[InventoryItem]:
        return [i for i in self.inventory if i.low]

    def upcoming_renewals(self, within: int = 60) -> list[Vendor]:
        return [v for v in self.vendors if v.renewal_in_days <= within]

    def open_safety(self) -> list[SafetyCheck]:
        return [s for s in self.safety if s.status == "open"]

    def annual_saas_cost(self) -> float:
        return round(self.monthly_spend() * 12, 2)

    def summary(self, is_active) -> dict:
        return {
            "saas_apps": len(self.saas),
            "monthly_saas_spend": self.monthly_spend(),
            "orphaned_seats": [f"{a}:{u}" for a, u in self.orphaned_seats(is_active)],
            "equipment": len(self.equipment),
            "overdue_equipment": [e.name for e in self.overdue_equipment()],
            "due_equipment": [e.name for e in self.due_equipment()],
            "low_stock": [i.name for i in self.low_stock()],
            "upcoming_renewals": [f"{v.name} ({v.renewal_in_days}d)" for v in self.upcoming_renewals()],
            "open_safety": [f"{s.area}: {s.check}" for s in self.open_safety()],
        }

    # ---- Serialisation --------------------------------------------- #
    def to_dict(self) -> dict:
        return {
            "saas": [a.to_dict() for a in self.saas.values()],
            "equipment": [e.to_dict() for e in self.equipment],
            "inventory": [i.to_dict() for i in self.inventory],
            "vendors": [v.to_dict() for v in self.vendors],
            "safety": [s.to_dict() for s in self.safety],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Operations:
        ops = cls()
        for a in data.get("saas", []):
            ops.saas[a["name"]] = SaasApp(a["name"], a["monthly_cost_per_seat"], set(a.get("assignees", [])))
        ops.equipment = [
            Equipment(e["asset_tag"], e["name"], e["location"], e["owner_team"], e["maintenance_in_days"])
            for e in data.get("equipment", [])
        ]
        ops.inventory = [
            InventoryItem(i["sku"], i["name"], i["location"], i["qty"], i["reorder_point"], i.get("unit", "unit"))
            for i in data.get("inventory", [])
        ]
        ops.vendors = [
            Vendor(v["name"], v["category"], v["renewal_in_days"], v["annual_cost"])
            for v in data.get("vendors", [])
        ]
        ops.safety = [
            SafetyCheck(s["area"], s["check"], s["status"], s.get("note", ""))
            for s in data.get("safety", [])
        ]
        return ops
