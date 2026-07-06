# Running LabSuite

The core is pure-Python and dependency-free. You only need a third-party package
(FastAPI) if you want the optional live HTTP API + GUI.

## Setup — macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

pip install -e .            # core only (CLI + engine, zero third-party deps)
# or, for the live API + web GUI and the test tooling:
pip install -e ".[api]"     # adds fastapi + uvicorn
pip install -e ".[dev]"     # adds pytest + ruff + httpx too
```

## Setup — Windows

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

> Windows tip: call pip as `python -m pip ...` (the `pip.exe` shim can be blocked
> by Device Guard on locked-down machines).

## The CLI

Everything runs through the `labsuite` command (installed by `pip install -e .`),
or `python -m labsuite.cli` if you prefer not to install the entry point.

```bash
labsuite demo              # scripted end-to-end story (start here)
labsuite org               # print the seeded lab

# Onboard / offboard (identity + device + training + SaaS, all in one)
labsuite onboard --name "Nadia Rahman" --department In-Vivo --role invivo-scientist
labsuite offboard --user nrahman

# Inspect access
labsuite access --user anguyen
labsuite check  --user talvarez --system truenas --resource invivo-study-data --action modify
labsuite check  --user rpatel   --system proxmox --resource 101 --action vm.power

# Compliance-gated access
labsuite train  --user nrahman --training IACUC          # complete a training
labsuite train  --user nrahman --training Biosafety --expire   # lapse it (revokes gated access)
labsuite compliance                                       # all training records

# MFA + conditional access (sensitive shares require Okta Verify)
labsuite mfa --user nrahman                # show enrollment status
labsuite mfa --user nrahman --enroll       # enroll -> unlocks conditional-access data

# Access requests + approvals (self-service governance)
labsuite request --user lpark --group Data-Science --why "ML side-project"
labsuite requests                         # the request queue
labsuite approve --id REQ-0001            # grant it (Okta -> AD)
labsuite deny --id REQ-0002 --note "not justified"

# Network segmentation (VLANs + IoT)
labsuite net                              # segments, devices, segmentation flags
labsuite net --check IoT Corp             # test east-west reachability (default-deny)
labsuite net --move cam-server-room IoT   # re-place a device onto another VLAN

# Governance + operations
labsuite login   --user anguyen           # demo password is used by default
labsuite sync                             # run the SCIM reconcile
labsuite review                           # access review + anomaly flags
labsuite alerts                           # action center — every flag in one feed
labsuite devices                          # managed laptop fleet
labsuite ops                              # ops dashboard (SaaS spend + flags)
labsuite saas                             # SaaS licences + cost
labsuite assets                           # equipment + maintenance
labsuite inventory                        # reagent / consumable stock
labsuite vendors                          # vendor contracts + renewals
labsuite safety                           # facility safety checks
labsuite audit --tail 20                  # the audit trail
```

**Roles** (`--role`): `ml-scientist`, `research-scientist`, `invivo-scientist`,
`vector-core`, `platform-engineer`, `lab-technician`, `lab-ops`, `compliance`,
`facilities`, `legal-counsel`, `it-admin`.
**Departments** (`--department`): `Bio`, `In-Vivo`, `Delivery`, `Platform`,
`Data-Science`, `Lab`, `Lab-Ops`, `Compliance`, `Facilities`, `Legal`, `IT`.

### Persisting state between runs

By default each command starts from the freshly-seeded lab in memory. Add
`--state PATH` to load/save a JSON snapshot so changes persist:

```bash
labsuite --state lab.json onboard --name "Nadia Rahman" --department In-Vivo --role invivo-scientist
labsuite --state lab.json train --user nrahman --training IACUC
labsuite --state lab.json access --user nrahman     # sees the onboarded user + training
labsuite --state lab.json offboard --user nrahman
```

## The live API + GUI (optional)

```bash
pip install -e ".[api]"
labsuite serve                 # http://127.0.0.1:8000
```

- `http://127.0.0.1:8000/`        — the web dashboard (GUI, all modules)
- `http://127.0.0.1:8000/docs`    — interactive OpenAPI docs
- `POST /oauth/token`             — password grant → session token
- `GET  /scim/v2/Users`           — the SCIM 2.0 user feed
- `GET  /me`                      — resolve the bearer token's identity + access
- `POST /admin/onboard` / `offboard`, `POST /sync`, `GET /access/{user}`, `GET /review`
- `GET /devices`, `GET /compliance`, `POST /compliance/complete` · `/expire`
- `POST /mfa/enroll` — enroll a user in MFA (Okta Verify)
- `GET /alerts` — the action center (every outstanding flag, with counts)
- `GET /network`, `POST /network/check` · `/network/move` — VLAN segmentation
- `GET /ops`, `GET /saas`, `GET /assets`, `GET /inventory`, `GET /vendors`, `GET /safety`
- `POST /assets/maintenance`, `/inventory/reorder`, `/safety/resolve`, `/vendors/renew`, `/saas/grant`, `/saas/revoke`
- `GET /requests`, `POST /requests` · `/requests/approve` · `/requests/deny`

The web GUI is **fully operable** — onboard/offboard, complete/lapse training,
grant/revoke SaaS seats, mark maintenance done, reorder stock, renew vendors, and
resolve safety issues are all buttons; every mutation is audited.

Example:

```bash
curl -s -XPOST localhost:8000/oauth/token \
  -H 'content-type: application/json' \
  -d '{"username":"anguyen","password":"Winter-Harbor-2026"}'
```

## Tests & linting

```bash
pip install -e ".[dev]"
ruff check src tests
pytest -q
```

CI runs both on Python 3.10, 3.11, and 3.12 (see `.github/workflows/ci.yml`).
