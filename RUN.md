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

# Onboard / offboard
labsuite onboard --name "Nadia Rahman" --department Research --role research-scientist
labsuite offboard --user nrahman

# Inspect access
labsuite access --user anguyen
labsuite check  --user anguyen --system truenas --resource research-data --action modify
labsuite check  --user anguyen --system proxmox --resource 101 --action vm.power

# Operate
labsuite login   --user anguyen           # demo password is used by default
labsuite sync                             # run the SCIM reconcile
labsuite review                           # access review + anomaly flags
labsuite audit --tail 20                  # the audit trail
```

**Roles** (`--role`): `research-scientist`, `lab-technician`, `platform-engineer`,
`operations`, `legal-counsel`, `security-analyst`, `it-admin`.
**Departments** (`--department`): `Research`, `Platform`, `Lab`, `Operations`,
`Legal`, `People`, `Security`, `IT`.

### Persisting state between runs

By default each command starts from the freshly-seeded lab in memory. Add
`--state PATH` to load/save a JSON snapshot so changes persist:

```bash
labsuite --state lab.json onboard --name "Nadia Rahman" --department Research --role research-scientist
labsuite --state lab.json access --user nrahman     # sees the onboarded user
labsuite --state lab.json offboard --user nrahman
```

## The live API + GUI (optional)

```bash
pip install -e ".[api]"
labsuite serve                 # http://127.0.0.1:8000
```

- `http://127.0.0.1:8000/`        — the web dashboard (GUI)
- `http://127.0.0.1:8000/docs`    — interactive OpenAPI docs
- `POST /oauth/token`             — password grant → session token
- `GET  /scim/v2/Users`           — the SCIM 2.0 user feed
- `GET  /me`                      — resolve the bearer token's identity + access
- `POST /admin/onboard` / `offboard`, `POST /sync`, `GET /access/{user}`, `GET /review`

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
