"""Optional live HTTP API over the control plane (needs the ``[api]`` extra).

This exposes the same operations the CLI does, but over HTTP, in shapes that
mirror the real systems:

* ``POST /oauth/token``     -- password grant -> a signed session token (Okta-ish)
* ``GET  /scim/v2/Users``   -- the SCIM 2.0 user feed (what a provisioner reads)
* ``GET  /me``              -- resolve the bearer token's identity
* ``POST /admin/onboard``   -- provision a new hire across the stack
* ``POST /admin/offboard``  -- deprovision same-day, with verification
* ``POST /sync``            -- run the SCIM reconcile
* ``GET  /access/{user}``   -- everything a user can touch
* ``GET  /review``          -- the access review
* ``GET  /``                -- the web GUI dashboard (served from docs/, live mode)

Import is deliberately at module top: ``labsuite.api`` is only imported when you
actually serve (``labsuite serve``) or test with the extra installed.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from labsuite.crypto import TokenError, verify_token
from labsuite.engine import ControlPlane
from labsuite.models import Department
from labsuite.seed import build_lab

# The web GUI + marketing site live in the repo's docs/ directory (also served
# as-is by GitHub Pages). Locate it relative to this file so `labsuite serve`
# can host the live dashboard when run from a checkout.
_DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"


class TokenRequest(BaseModel):
    username: str
    password: str


class OnboardRequest(BaseModel):
    name: str
    department: str
    role: str
    title: str = ""


def create_app(cp: ControlPlane | None = None) -> FastAPI:
    """Build a FastAPI app bound to ``cp`` (defaults to the seeded lab)."""
    control = cp or build_lab()
    app = FastAPI(
        title="LabSuite",
        version="0.1.0",
        description="Identity-to-infrastructure access governance: Okta -> AD -> TrueNAS + Proxmox.",
    )

    def current_claims(authorization: str = Header(default="")) -> dict:
        if not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        token = authorization.split(" ", 1)[1]
        try:
            return verify_token(token, control.okta.signing_secret)  # type: ignore[attr-defined]
        except TokenError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    # --------------------------------------------------------------- #
    # Identity / login (Okta layer)
    # --------------------------------------------------------------- #
    @app.post("/oauth/token")
    def issue_token(req: TokenRequest) -> dict:
        try:
            token = control.login(req.username, req.password)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail="authentication failed") from exc
        return {"access_token": token, "token_type": "bearer"}

    @app.get("/me")
    def me(claims: dict = Depends(current_claims)) -> dict:
        report = control.resolve_access(claims["sub"])
        return {"identity": claims, "access": report.to_dict()}

    @app.get("/scim/v2/Users")
    def scim_users() -> dict:
        users = control.okta.scim_users()  # type: ignore[attr-defined]
        return {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": len(users),
            "Resources": users,
        }

    # --------------------------------------------------------------- #
    # Admin operations (control plane)
    # --------------------------------------------------------------- #
    @app.post("/admin/onboard")
    def onboard(req: OnboardRequest) -> dict:
        try:
            department = next(d for d in Department if d.value.lower() == req.department.lower())
        except StopIteration as exc:
            raise HTTPException(status_code=400, detail=f"unknown department {req.department!r}") from exc
        try:
            result = control.onboard(req.name, department, req.role, title=req.title)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result.to_dict()

    @app.post("/admin/offboard")
    def offboard(username: str = Body(..., embed=True)) -> dict:
        try:
            result = control.offboard(username)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return result.to_dict()

    @app.post("/sync")
    def sync() -> dict:
        return control.sync().to_dict()

    # --------------------------------------------------------------- #
    # Access requests + approvals
    # --------------------------------------------------------------- #
    @app.get("/requests")
    def list_requests() -> dict:
        return {"requests": [r.to_dict() for r in control.requests.all()]}

    @app.post("/requests")
    def create_request(requester: str = Body(...), group: str = Body(...), justification: str = Body(default="")) -> dict:
        try:
            return control.request_access(requester, group, justification).to_dict()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/requests/approve")
    def approve(request_id: str = Body(..., embed=True)) -> dict:
        try:
            return control.approve_request(request_id).to_dict()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/requests/deny")
    def deny(request_id: str = Body(...), note: str = Body(default="")) -> dict:
        try:
            return control.deny_request(request_id, note=note).to_dict()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/access/{username}")
    def access(username: str) -> dict:
        return control.resolve_access(username).to_dict()

    @app.get("/review")
    def review() -> dict:
        return control.access_review()

    @app.get("/audit")
    def audit(limit: int = 50) -> dict:
        events = [e.to_dict() for e in control.audit.tail(limit)]
        events.reverse()  # most-recent first, matching the UI
        return {"events": events}

    @app.get("/devices")
    def devices() -> dict:
        return {"devices": [d.to_dict() for d in control.endpoints.list_devices()]}

    @app.get("/compliance")
    def compliance() -> dict:
        return {"records": control.compliance.all_records()}

    @app.post("/compliance/complete")
    def complete_training(username: str = Body(...), training: str = Body(...)) -> dict:
        control.complete_training(username, training)
        return {"username": username, "training": training, "status": "current"}

    @app.post("/compliance/expire")
    def expire_training(username: str = Body(...), training: str = Body(...)) -> dict:
        control.expire_training(username, training)
        return {"username": username, "training": training, "status": "expired"}

    # --------------------------------------------------------------- #
    # Operations (SaaS / equipment / inventory / vendors / safety)
    # --------------------------------------------------------------- #
    @app.get("/ops")
    def ops() -> dict:
        return control.ops_summary()

    @app.get("/saas")
    def saas() -> dict:
        return {
            "monthly_spend": control.ops.monthly_spend(),
            "annual_cost": control.ops.annual_saas_cost(),
            "apps": [a.to_dict() for a in control.ops.saas.values()],
        }

    @app.get("/assets")
    def assets() -> dict:
        return {"equipment": [e.to_dict() for e in control.ops.equipment]}

    @app.get("/inventory")
    def inventory() -> dict:
        return {"inventory": [i.to_dict() for i in control.ops.inventory]}

    @app.get("/vendors")
    def vendors() -> dict:
        return {"vendors": [v.to_dict() for v in control.ops.vendors]}

    @app.get("/safety")
    def safety() -> dict:
        return {"safety": [s.to_dict() for s in control.ops.safety]}

    # ---- operations mutations (the fully-operable GUI) ------------- #
    @app.post("/assets/maintenance")
    def do_maintenance(asset_tag: str = Body(..., embed=True)) -> dict:
        e = control.complete_maintenance(asset_tag)
        if e is None:
            raise HTTPException(status_code=404, detail=f"unknown asset {asset_tag!r}")
        return e.to_dict()

    @app.post("/inventory/reorder")
    def do_reorder(sku: str = Body(..., embed=True)) -> dict:
        i = control.reorder_inventory(sku)
        if i is None:
            raise HTTPException(status_code=404, detail=f"unknown sku {sku!r}")
        return i.to_dict()

    @app.post("/safety/resolve")
    def do_resolve(area: str = Body(...), check: str = Body(...)) -> dict:
        s = control.resolve_safety(area, check)
        if s is None:
            raise HTTPException(status_code=404, detail="unknown safety check")
        return s.to_dict()

    @app.post("/vendors/renew")
    def do_renew(name: str = Body(..., embed=True)) -> dict:
        v = control.renew_vendor(name)
        if v is None:
            raise HTTPException(status_code=404, detail=f"unknown vendor {name!r}")
        return v.to_dict()

    @app.post("/saas/grant")
    def do_saas_grant(username: str = Body(...), app_name: str = Body(...)) -> dict:
        control.grant_saas_seat(username, app_name)
        return {"username": username, "app": app_name, "granted": True}

    @app.post("/saas/revoke")
    def do_saas_revoke(username: str = Body(...), app_name: str = Body(...)) -> dict:
        return {"username": username, "app": app_name, "revoked": control.revoke_saas_seat(username, app_name)}

    # --------------------------------------------------------------- #
    # Web GUI + marketing site
    # --------------------------------------------------------------- #
    if _DOCS_DIR.is_dir():
        # Serve the polished vanilla-JS dashboard + landing page from docs/.
        # The GUI's live-mode probe hits /scim/v2/Users on this same origin.
        app.mount("/site", StaticFiles(directory=str(_DOCS_DIR), html=True), name="site")

        @app.get("/")
        def root() -> RedirectResponse:
            return RedirectResponse(url="/site/app/")
    else:  # pragma: no cover - fallback when installed without the docs/ tree

        @app.get("/", response_class=HTMLResponse)
        def dashboard() -> str:
            rows = "".join(
                f"<tr><td>{u.username}</td><td>{u.display_name}</td>"
                f"<td>{u.department.value}</td><td>{'active' if u.active else 'disabled'}</td></tr>"
                for u in sorted(control.okta.list_users(), key=lambda x: x.username)
            )
            return f"""<!doctype html><html><head><title>LabSuite</title>
<style>body{{font-family:system-ui;margin:2rem;color:#0f172a}}
h1{{color:#2563eb}} table{{border-collapse:collapse}} td,th{{padding:.4rem .8rem;border-bottom:1px solid #e2e8f0}}</style>
</head><body><h1>LabSuite</h1>
<p>Okta &rarr; Active Directory &rarr; TrueNAS + Proxmox &mdash; {len(control.okta.list_users())} identities.</p>
<p>API docs at <a href="/docs">/docs</a>.</p>
<table><tr><th>User</th><th>Name</th><th>Dept</th><th>Status</th></tr>{rows}</table>
</body></html>"""

    return app
