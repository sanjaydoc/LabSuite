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

    @app.get("/access/{username}")
    def access(username: str) -> dict:
        return control.resolve_access(username).to_dict()

    @app.get("/review")
    def review() -> dict:
        return control.access_review()

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
