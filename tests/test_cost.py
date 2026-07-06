"""Cost analytics: SaaS spend by department vs budget, vendor spend, orphans."""

from labsuite.models import Department
from labsuite.seed import build_lab


def test_saas_total_matches_ops_monthly_spend():
    cp = build_lab()
    c = cp.cost_analytics()
    assert c["saas_monthly_total"] == cp.ops.monthly_spend()
    assert c["saas_annual_total"] == round(c["saas_monthly_total"] * 12, 2)


def test_department_rows_carry_budget_and_over_flag():
    cp = build_lab()
    ds = next(r for r in cp.cost_analytics()["by_department"] if r["department"] == "Data-Science")
    # The seed tunes the Data-Science budget below its actual spend.
    assert ds["budget"] is not None
    assert ds["over_budget"] is True
    assert "Data-Science" in cp.cost_analytics()["over_budget_departments"]


def test_over_budget_raises_a_cost_alert():
    cp = build_lab()
    cats = {(a["category"], a["title"]) for a in cp.action_center()["alerts"]}
    assert any(cat == "cost" and "Data-Science" in title for cat, title in cats)


def test_vendor_spend_by_category_sums_to_total():
    cp = build_lab()
    c = cp.cost_analytics()
    assert round(sum(v["annual"] for v in c["vendor_by_category"]), 2) == c["vendor_annual_total"]


def test_orphaned_seats_appear_after_a_user_goes_inactive():
    cp = build_lab()
    assert cp.cost_analytics()["orphaned_monthly"] == 0.0
    # Deactivate WITHOUT the normal offboard (which would reclaim seats), so the
    # seats become orphaned and the savings show up.
    cp.okta.deactivate_user("mchen")
    c = cp.cost_analytics()
    assert c["orphaned_monthly"] > 0
    assert c["orphaned_annual"] == round(c["orphaned_monthly"] * 12, 2)


def test_every_department_with_users_appears():
    cp = build_lab()
    cp.onboard("Zed Young", Department.IT, "it-admin")
    depts = {r["department"] for r in cp.cost_analytics()["by_department"]}
    assert "IT" in depts
