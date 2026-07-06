# From simulator to production: mapping LabSuite to real IT tooling

LabSuite implements the *logic* of the Okta → AD → TrueNAS + Proxmox chain in a
single pure-Python control plane so it can run and be tested anywhere. In a real
environment you would drive the actual systems, and the day-to-day toolkit for
that is **PowerShell** (imperative, native to AD/Windows/Okta), **Ansible**
(declarative config management — the JD names it for Linux), and **Terraform**
(declarative infrastructure-as-code).

This page shows, operation by operation, the real command each LabSuite call
stands in for. It is the bridge from "here's the model" to "here's how I'd run it
against production."

## Two paradigms, one outcome

| | Style | Where it fits this stack |
|---|---|---|
| **PowerShell** | Imperative — do this, then this | AD administration (`ActiveDirectory` module), Okta (`Okta.PowerShell` / REST), quick operational scripts |
| **Ansible** | Declarative config management (YAML playbooks) | Enforcing state on AD-joined Linux servers, TrueNAS/Proxmox via their APIs, repeatable onboarding runbooks |
| **Terraform** | Declarative infrastructure-as-code (HCL) | Managing Okta objects and Proxmox ACLs/pools as version-controlled, reviewable state |

Provider / module maturity varies by layer — Okta and Proxmox have solid
Terraform providers; TrueNAS is usually driven via its REST API from Ansible or
scripts; AD is most commonly PowerShell or the `microsoft.ad` Ansible collection.

---

## Onboard a hire

**LabSuite**
```bash
labsuite onboard --name "Nadia Rahman" --department Research --role research-scientist
```

**PowerShell** — create in AD and assign the department group (Okta via REST):
```powershell
# Active Directory
New-ADUser -Name "Nadia Rahman" -SamAccountName nrahman `
  -UserPrincipalName nrahman@lab.local -Department Research `
  -AccountPassword (Read-Host -AsSecureString) -Enabled $true
Add-ADGroupMember -Identity "Research" -Members nrahman

# Okta (REST; the Okta.PowerShell module wraps the same API)
$body = @{ profile = @{ firstName="Nadia"; lastName="Rahman";
  login="nrahman@lab.local"; email="nrahman@lab.local" } } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$OktaOrg/api/v1/users?activate=true" `
  -Headers @{ Authorization = "SSWS $OktaToken" } -ContentType application/json -Body $body
```

**Ansible** — declarative, idempotent (`microsoft.ad` collection):
```yaml
- name: Onboard research scientist
  hosts: dc
  tasks:
    - microsoft.ad.user:
        name: nrahman
        firstname: Nadia
        surname: Rahman
        department: Research
        groups: { add: [Research] }   # department group; GPU access is inherited via nesting
        state: present
        password: "{{ vault_temp_password }}"
```

**Terraform** — Okta as reviewable state (`okta/okta` provider):
```hcl
resource "okta_user" "nrahman" {
  first_name = "Nadia"
  last_name  = "Rahman"
  login      = "nrahman@lab.local"
  email      = "nrahman@lab.local"
}
resource "okta_group_memberships" "nrahman_groups" {
  group_id = okta_group.research.id
  users    = [okta_user.nrahman.id]
}
```

---

## Offboard same-day (deprovision + verify)

**LabSuite**
```bash
labsuite offboard --user nrahman     # deactivates, strips groups, verifies zero residual
```

**PowerShell** — disable and strip every membership (the cascade LabSuite verifies):
```powershell
Disable-ADAccount -Identity nrahman
Get-ADPrincipalGroupMembership nrahman |
  Where-Object Name -ne "Domain Users" |
  ForEach-Object { Remove-ADGroupMember -Identity $_ -Members nrahman -Confirm:$false }

# Okta deactivate (revokes SSO immediately)
Invoke-RestMethod -Method Post -Uri "$OktaOrg/api/v1/users/$id/lifecycle/deactivate" `
  -Headers @{ Authorization = "SSWS $OktaToken" }
```

**Ansible**
```yaml
- microsoft.ad.user:
    name: nrahman
    account_disabled: true
    groups: { set: [] }     # remove from all groups
    state: present
```

---

## SCIM sync (Okta → AD)

**LabSuite**
```bash
labsuite sync     # idempotent reconcile
```

**In production** this is usually *native*: the **Okta AD agent** (or Azure AD
Connect) provisions users/groups from Okta into AD continuously via SCIM, so you
configure it rather than script it. When you do script a reconcile, it is a
scheduled PowerShell/Ansible job that compares source-of-truth to AD and applies
the diff — exactly what `ScimSync.reconcile()` models. The property that makes it
safe on a schedule (idempotency: a no-op when nothing changed) is the same one
LabSuite tests assert.

---

## AD nested groups

**LabSuite**
```python
ad.nest_group("GPU-Cluster-Users", "Research")   # Research is a member of GPU-Cluster-Users
```

**PowerShell** — adding a *group* as a member of another group *is* nesting:
```powershell
Add-ADGroupMember -Identity "GPU-Cluster-Users" -Members "Research"
# resolve effective (transitive) membership:
Get-ADGroupMember -Identity "GPU-Cluster-Users" -Recursive
```

**Ansible**
```yaml
- microsoft.ad.group:
    name: GPU-Cluster-Users
    members: { add: [Research, Platform] }   # nested member groups
    scope: global
```

---

## TrueNAS share ACL

**LabSuite**
```python
truenas.grant("research-data", "Research", AccessLevel.MODIFY)
```

**TrueNAS API** — via the on-box `midclt` CLI or the REST API:
```bash
# set an ACL entry granting the AD group MODIFY on the dataset
midclt call filesystem.setacl '{"path": "/mnt/tank/research",
  "dacl": [{"tag": "GROUP", "id": "<Research-SID>", "perms": {"BASIC": "MODIFY"},
            "type": "ALLOW"}]}'
```

**Ansible** — drive the same REST endpoint with the generic `uri` module:
```yaml
- ansible.builtin.uri:
    url: "https://nas01.lab.local/api/v2.0/filesystem/setacl"
    method: POST
    headers: { Authorization: "Bearer {{ truenas_api_key }}" }
    body_format: json
    body: { path: "/mnt/tank/research",
            dacl: [ { tag: GROUP, id: "{{ research_gid }}",
                      perms: { BASIC: MODIFY }, type: ALLOW } ] }
```

---

## Proxmox role assignment

**LabSuite**
```python
proxmox.grant("/pool/research", "GPU-Cluster-Users", ProxmoxRole.PVE_VM_USER)
```

**Proxmox CLI** — `pveum` maps a role to a group at a path (with inheritance):
```bash
pveum acl modify /pool/research --groups gpu-cluster-users --roles PVEVMUser
```

**Terraform** — `bpg/proxmox` provider:
```hcl
resource "proxmox_virtual_environment_acl" "research_pool" {
  path      = "/pool/research"
  group_id  = "gpu-cluster-users"
  role_id   = "PVEVMUser"
  propagate = true
}
```

**Ansible** — `community.general.proxmox_access_acl` (or `uri` to `/api2/json/access/acl`):
```yaml
- community.general.proxmox_access_acl:
    path: /pool/research
    type: group
    ugid: gpu-cluster-users
    roleid: PVEVMUser
    state: present
```

---

## Access review

**LabSuite**
```bash
labsuite review     # entitlements per user + anomaly flags
```

**PowerShell** — the recurring report an access review actually is:
```powershell
Get-ADGroup -Filter * | ForEach-Object {
  [pscustomobject]@{
    Group   = $_.Name
    Members = (Get-ADGroupMember $_ -Recursive | Select-Object -Expand SamAccountName) -join ', '
  }
} | Export-Csv access-review.csv -NoTypeInformation
```

Wrap it in a scheduled task and you have the quarterly review the JD asks for —
which is exactly what `ControlPlane.access_review()` produces, plus the anomaly
flags (stale access, FULL on sensitive shares, datacenter-wide admin).

---

### The point

LabSuite isn't a replacement for these tools — it's proof of understanding the
*model* they operate on. Every `labsuite` verb above corresponds to a concrete,
real command an IT owner runs, and the adapter layer
([`adapters/live.py`](../src/labsuite/adapters/live.py)) is where those real API
calls would be wired in behind the same interface.
