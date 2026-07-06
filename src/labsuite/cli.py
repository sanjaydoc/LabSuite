"""``labsuite`` -- the command-line control plane.

Run ``labsuite demo`` for a scripted end-to-end story, or drive individual
operations (``onboard``, ``offboard``, ``access``, ``check``, ``review``,
``sync``, ``audit``). Pass ``--state PATH`` to any mutating command to persist
the control plane between invocations (otherwise each run starts from the seeded
lab in memory).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from labsuite.crypto import TokenError
from labsuite.engine import ControlPlane
from labsuite.models import Department
from labsuite.policy import IMAGE_CATALOG
from labsuite.seed import DEMO_PASSWORD, build_lab

# --------------------------------------------------------------------------- #
# Small presentation helpers (stdlib only -- no rich/tabulate dependency)
# --------------------------------------------------------------------------- #
BOLD, DIM, GREEN, RED, CYAN, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[36m", "\033[0m"


def _c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}" if sys.stdout.isatty() else text


def _header(text: str) -> None:
    print(f"\n{_c(text, BOLD)}")
    print(_c("-" * len(text), DIM))


def _yes_no(value: bool) -> str:
    return _c("ALLOW", GREEN) if value else _c("DENY", RED)


def _parse_department(raw: str) -> Department:
    for dept in Department:
        if dept.value.lower() == raw.lower() or dept.name.lower() == raw.lower():
            return dept
    valid = ", ".join(d.value for d in Department)
    raise SystemExit(f"unknown department {raw!r}; valid: {valid}")


# --------------------------------------------------------------------------- #
# State management
# --------------------------------------------------------------------------- #
def _load_or_build(state: str | None) -> ControlPlane:
    if state and Path(state).exists():
        return ControlPlane.from_dict(json.loads(Path(state).read_text()))
    return build_lab()


def _save(cp: ControlPlane, state: str | None) -> None:
    if state:
        Path(state).write_text(json.dumps(cp.to_dict(), indent=2))


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_org(args: argparse.Namespace) -> int:
    """Print the seeded org: Okta users/groups, TrueNAS shares, and Proxmox guests."""
    cp = _load_or_build(args.state)
    _header("Okta users")
    for user in sorted(cp.okta.list_users(), key=lambda u: u.username):
        status = "active" if user.active else _c("DISABLED", RED)
        print(f"  {user.username:10} {user.display_name:16} {user.department.value:11} {status}")
    _header("Okta groups")
    for group in sorted(cp.okta.list_groups(), key=lambda g: g.name):
        print(f"  {group.name:22} {DIM}{len(group.members)} members{RESET}")
    _header("TrueNAS shares")
    for share in cp.truenas.list_shares():
        tag = _c(" [sensitive]", RED) if share.sensitive else ""
        acl = ", ".join(f"{g}:{lvl}" for g, lvl in share.acl.items())
        print(f"  {share.name:18} {DIM}{share.dataset}{RESET}{tag}\n      {DIM}{acl}{RESET}")
    _header("Proxmox guests")
    for vm in cp.proxmox.list_vms():
        print(f"  {vm.vmid}  {vm.name:16} node={vm.node:11} pool={vm.pool}")
    return 0


def cmd_onboard(args: argparse.Namespace) -> int:
    """Provision a new hire across the whole stack and print the result."""
    cp = _load_or_build(args.state)
    result = cp.onboard(args.name, _parse_department(args.department), args.role, title=args.title or "")
    _header(f"Onboarded {args.name}")
    print(f"  username : {_c(result.username, CYAN)}")
    print(f"  email    : {result.email}")
    print(f"  password : {_c(result.temp_password, CYAN)}  {DIM}(temporary){RESET}")
    print(f"  groups   : {', '.join(result.okta_groups)}")
    print(f"  sync     : {result.sync.summary()}")
    _header("Access provisioned")
    for share, level in sorted(result.truenas_access.items()):
        print(f"  TrueNAS  {share:18} {level}")
    for vmid, role in sorted(result.proxmox_access.items()):
        print(f"  Proxmox  /vms/{vmid:<12} {role}")
    if not result.truenas_access and not result.proxmox_access:
        print(f"  {DIM}(no downstream access for this role){RESET}")
    if result.device:
        d = result.device
        img = IMAGE_CATALOG[d["image"]]
        _header("Device imaged & shipped (day one)")
        print(f"  {d['model']} · image {_c(d['image'], CYAN)} · {img.security_summary()}")
        print(f"  {DIM}asset {d['asset_tag']} · {d['platform']} · MDM {img.mdm}{RESET}")
    if result.trainings:
        _header("Compliance (training gates sensitive access)")
        print(f"  required : {', '.join(result.trainings)} {DIM}(pending){RESET}")
        for share, missing in sorted(result.gated.items()):
            print(f"  {_c('gated', RED)} {share:18} until {', '.join(missing)} complete")
    if result.saas:
        _header("SaaS provisioned")
        print(f"  {', '.join(result.saas)}")
    _save(cp, args.state)
    return 0


def cmd_offboard(args: argparse.Namespace) -> int:
    """Deprovision a user same-day and verify that zero residual access remains."""
    cp = _load_or_build(args.state)
    try:
        result = cp.offboard(args.user)
    except KeyError as exc:
        raise SystemExit(str(exc)) from exc
    _header(f"Offboarded {args.user}")
    print(f"  removed from groups : {', '.join(result.removed_groups) or '(none)'}")
    print(f"  sync                : {result.sync.summary()}")
    verdict = _c("CLEAN -- zero residual access", GREEN) if result.clean else _c("RESIDUAL ACCESS REMAINS", RED)
    print(f"  verification        : {verdict}")
    if not result.clean:
        print(f"    TrueNAS: {result.residual_truenas}")
        print(f"    Proxmox: {result.residual_proxmox}")
    if result.device:
        d = result.device
        print(f"  device              : {d['asset_tag']} ({d['model']}) -> {_c('wipe & return', RED)}")
    if result.saas_revoked:
        print(f"  SaaS seats reclaimed: {', '.join(result.saas_revoked)}")
    _save(cp, args.state)
    return 0 if result.clean else 1


def cmd_login(args: argparse.Namespace) -> int:
    """Authenticate a user against Okta and print the issued session token."""
    cp = _load_or_build(args.state)
    password = args.password or DEMO_PASSWORD
    try:
        token = cp.login(args.user, password)
    except PermissionError:
        print(_c("authentication failed", RED))
        return 1
    print(_c("authentication succeeded", GREEN))
    print(f"  session token (JWT): {token[:48]}...")
    return 0


def cmd_access(args: argparse.Namespace) -> int:
    """Show everything a user can touch: groups, shares, VMs, MFA, and compliance."""
    cp = _load_or_build(args.state)
    report = cp.resolve_access(args.user)
    _header(f"Access report for {args.user}")
    print(f"  active              : {report.active}")
    print(f"  Okta groups         : {', '.join(report.okta_groups) or '(none)'}")
    print(f"  AD effective groups : {', '.join(report.ad_effective_groups) or '(none)'}")
    _header("TrueNAS")
    for share, level in sorted(report.truenas.items()):
        print(f"  {share:18} {level}")
    if not report.truenas:
        print(f"  {DIM}(none){RESET}")
    _header("Proxmox")
    for path, role in sorted(report.proxmox.items()):
        print(f"  {path:14} {role}")
    if not report.proxmox:
        print(f"  {DIM}(none){RESET}")
    mfa = _c("enrolled", GREEN) if report.mfa_enrolled else _c("not enrolled", RED)
    print(f"\n  MFA (Okta Verify)   : {mfa}")
    if report.trainings or report.blocked:
        _header("Compliance / training")
        for training, status in sorted(report.trainings.items()):
            colour = GREEN if status == "current" else RED
            print(f"  {training:16} {_c(status, colour)}")
        for share, missing in sorted(report.blocked.items()):
            print(f"  {_c('BLOCKED', RED)} {share} — needs {', '.join(missing)}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Make one audited allow/deny decision for a user against a TrueNAS/Proxmox resource."""
    cp = _load_or_build(args.state)
    if args.system == "truenas":
        decision = cp.check_truenas(args.user, args.resource, args.action)
    else:
        decision = cp.check_proxmox(args.user, int(args.resource), args.action)
    print(
        f"{_yes_no(decision.allowed)}  {args.user} -> {args.system}:{decision.resource} "
        f"[{decision.action}]  granted={decision.granted} required={decision.required}"
    )
    print(f"  {DIM}{decision.reason}{RESET}")
    return 0 if decision.allowed else 1


def cmd_review(args: argparse.Namespace) -> int:
    """Run the quarterly access review and print per-user entitlements plus anomaly flags."""
    cp = _load_or_build(args.state)
    review = cp.access_review()
    _header("Quarterly access review")
    for username, ent in sorted(review["entitlements"].items()):
        nas = len(ent["truenas"])
        pve = len(ent["proxmox"])
        state = "active" if ent["active"] else _c("DISABLED", RED)
        print(f"  {username:10} {state:8} {nas} shares, {pve} VMs")
    _header(f"Flags ({len(review['flags'])})")
    if not review["flags"]:
        print(_c("  none -- estate is clean", GREEN))
    for flag in review["flags"]:
        print(f"  {_c('!', RED)} {flag}")
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    """Record a training as complete (or --expire it), unlocking/revoking gated access."""
    cp = _load_or_build(args.state)
    if args.expire:
        cp.expire_training(args.user, args.training)
        print(f"{args.training} for {args.user}: {_c('EXPIRED', RED)} — gated access revoked")
    else:
        cp.complete_training(args.user, args.training)
        print(f"{args.training} for {args.user}: {_c('current', GREEN)} — gated access unlocked")
    _save(cp, args.state)
    return 0


def cmd_mfa(args: argparse.Namespace) -> int:
    """Show a user's MFA status, or --enroll them in Okta Verify to unlock CA resources."""
    cp = _load_or_build(args.state)
    if args.enroll:
        cp.enroll_mfa(args.user)
        print(f"{args.user}: MFA {_c('enrolled', GREEN)} (Okta Verify) — conditional-access resources unlocked")
        _save(cp, args.state)
    else:
        status = _c("enrolled", GREEN) if cp.okta.is_mfa_enrolled(args.user) else _c("not enrolled", RED)
        print(f"{args.user}: MFA {status}")
    return 0


def cmd_compliance(args: argparse.Namespace) -> int:
    """Print every user's training records (current vs lapsed)."""
    cp = _load_or_build(args.state)
    _header("Training records")
    records = cp.compliance.all_records()
    for user in sorted(records):
        parts = []
        for training, status in sorted(records[user].items()):
            colour = GREEN if status == "current" else RED
            parts.append(f"{training}:{_c(status, colour)}")
        print(f"  {user:10} {' '.join(parts)}")
    if not records:
        print(f"  {DIM}(no records){RESET}")
    return 0


def cmd_request(args: argparse.Namespace) -> int:
    """File an access request for a user to join a group (pending approval)."""
    cp = _load_or_build(args.state)
    try:
        req = cp.request_access(args.user, args.group, args.why or "")
    except KeyError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"{_c(req.id, CYAN)} — {args.user} requests {args.group} {DIM}({req.status}){RESET}")
    _save(cp, args.state)
    return 0


def cmd_requests(args: argparse.Namespace) -> int:
    """List all access requests and their approval status."""
    cp = _load_or_build(args.state)
    _header("Access requests")
    for r in cp.requests.all():
        colour = GREEN if r.status == "approved" else RED if r.status == "denied" else CYAN
        by = f" by {r.decided_by}" if r.decided_by else ""
        print(f"  {r.id}  {r.requester:10} -> {r.group:18} {_c(r.status, colour)}{by}  {DIM}{r.justification}{RESET}")
    if not cp.requests.requests:
        print(f"  {DIM}(no requests){RESET}")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    """Approve a pending access request, granting the requested group."""
    cp = _load_or_build(args.state)
    try:
        req = cp.approve_request(args.id)
    except KeyError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"{req.id}: {_c('approved', GREEN)} — {req.requester} granted {req.group}")
    _save(cp, args.state)
    return 0


def cmd_deny(args: argparse.Namespace) -> int:
    """Deny a pending access request, optionally recording a note."""
    cp = _load_or_build(args.state)
    try:
        req = cp.deny_request(args.id, note=args.note or "")
    except KeyError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"{req.id}: {_c('denied', RED)}")
    _save(cp, args.state)
    return 0


def cmd_devices(args: argparse.Namespace) -> int:
    """List the managed laptop fleet with image and assignment status."""
    cp = _load_or_build(args.state)
    _header("Managed device fleet")
    devices = sorted(cp.endpoints.list_devices(), key=lambda d: d.asset_tag)
    for d in devices:
        flag = _c("wipe & return", RED) if d.status.value == "wipe & return" else _c(d.status.value, GREEN)
        print(f"  {d.asset_tag}  {d.model:18} {d.image:12} {d.assignee or '-':10} {flag}")
    if not devices:
        print(f"  {DIM}(no devices){RESET}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Export a report (access | saas | audit) as CSV to stdout or a file."""
    cp = _load_or_build(args.state)
    try:
        body = cp.export_csv(args.kind)
    except KeyError as exc:
        raise SystemExit(str(exc)) from exc
    if args.out:
        Path(args.out).write_text(body)
        print(f"wrote {args.out} ({len(body)} bytes)")
    else:
        sys.stdout.write(body)
    return 0


def cmd_readiness(args: argparse.Namespace) -> int:
    """Show onboarding readiness: one hire's full checklist, or the day-one-ready summary."""
    cp = _load_or_build(args.state)
    if args.user:
        try:
            c = cp.onboarding_checklist(args.user)
        except KeyError as exc:
            raise SystemExit(str(exc)) from exc
        status = _c("READY", GREEN) if c["ready"] else _c(f"{c['completion_pct']}%", RED)
        _header(f"Onboarding readiness: {c['display_name']} ({args.user}) — {status}")
        for i in c["items"]:
            box = _c("[x]", GREEN) if i["done"] else _c("[ ]", RED)
            req = "" if i["required"] else f" {DIM}(optional){RESET}"
            print(f"  {box} {i['item']}{req}  {DIM}{i['detail']}{RESET}")
        return 0

    s = cp.readiness_summary()
    _header(f"Onboarding readiness — {s['ready_count']}/{s['total']} day-one ready")
    for r in s["rows"]:
        status = _c("ready", GREEN) if r["ready"] else _c(f"{r['completion_pct']}%", RED)
        print(f"  {r['username']:10} {status:8}  {DIM}{r['display_name']}{RESET}")
    return 0


def cmd_alerts(args: argparse.Namespace) -> int:
    """Print the action center: every outstanding flag across the estate in one feed."""
    cp = _load_or_build(args.state)
    ac = cp.action_center()
    _header("Action center")
    c = ac["counts"]
    print(f"  {_c(str(c['high']) + ' high', RED)}   "
          f"{_c(str(c['medium']) + ' medium', CYAN)}   "
          f"{DIM}{c['info']} info{RESET}")
    if not ac["alerts"]:
        print(_c("  all clear -- nothing needs attention", GREEN))
        return 0
    sev_colour = {"high": RED, "medium": CYAN, "info": DIM}
    for a in ac["alerts"]:
        mark = _c("●", sev_colour.get(a["severity"], DIM))
        detail = f"  {DIM}{a['detail']}{RESET}" if a["detail"] else ""
        print(f"  {mark} [{a['category']:9}] {a['title']}{detail}")
    return 0


def cmd_campaign(args: argparse.Namespace) -> int:
    """Drive an access-review campaign: start it, or certify/revoke a user, else show status."""
    cp = _load_or_build(args.state)
    if args.start:
        p = cp.start_review_campaign(args.name or "Access review")
        print(f"Started campaign {_c(p['name'], CYAN)} over {p['total']} users")
        _save(cp, args.state)
        return 0
    if args.certify:
        try:
            cp.certify_user(args.certify, reviewer=args.reviewer or "it-admin")
        except KeyError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"{args.certify}: {_c('certified', GREEN)}")
        _save(cp, args.state)
        return 0
    if args.revoke:
        try:
            cp.revoke_user(args.revoke, reviewer=args.reviewer or "it-admin", note=args.note or "")
        except KeyError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"{args.revoke}: {_c('revoked', RED)} — entitlements stripped")
        _save(cp, args.state)
        return 0

    st = cp.campaign_status()
    p = st["progress"]
    _header(f"Access-review campaign: {p['name'] or '(none started)'}")
    if not p["total"]:
        print(f"  {DIM}no campaign — run `labsuite campaign --start`{RESET}")
        return 0
    print(f"  {p['completion_pct']}% reviewed  "
          f"({_c(str(p['certified']) + ' certified', GREEN)}, "
          f"{_c(str(p['revoked']) + ' revoked', RED)}, {p['pending']} pending)")
    for r in st["rows"]:
        colour = GREEN if r["status"] == "certified" else RED if r["status"] == "revoked" else DIM
        print(f"  {r['username']:10} {_c(r['status'], colour):8}  {r['shares']} shares, {r['vms']} VMs")
    return 0


def cmd_jit(args: argparse.Namespace) -> int:
    """Break-glass admin: grant/revoke time-bound elevation, sweep lapsed grants, or show status."""
    cp = _load_or_build(args.state)
    if args.grant:
        user, group = args.grant
        try:
            g = cp.grant_jit(user, group, args.minutes, args.reason or "", actor=args.actor or "it-admin")
        except KeyError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"{_c(g.id, CYAN)} — {user} elevated to {group} for {args.minutes}m")
        _save(cp, args.state)
        return 0
    if args.revoke:
        try:
            cp.revoke_jit(args.revoke, actor=args.actor or "it-admin")
        except KeyError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"{args.revoke}: {_c('revoked', RED)} — elevation reclaimed")
        _save(cp, args.state)
        return 0
    if args.sweep:
        expired = cp.sweep_jit(actor=args.actor or "it-admin")
        print(f"swept {len(expired)} expired grant(s)")
        _save(cp, args.state)
        return 0

    st = cp.jit_status()
    _header("Break-glass (just-in-time) admin")
    if not st["active"] and not st["expired_unswept"]:
        print(_c("  no active elevations", GREEN))
        return 0
    for g in st["active"]:
        print(f"  {_c(g['id'], CYAN)} {g['username']:10} -> {g['group']:18} "
              f"{_c(str(g['remaining_minutes']) + 'm left', GREEN)}  {DIM}{g['reason']}{RESET}")
    for g in st["expired_unswept"]:
        print(f"  {_c(g['id'], RED)} {g['username']:10} -> {g['group']:18} "
              f"{_c('EXPIRED — run --sweep', RED)}")
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    """Run a backup now (--run RESOURCE) or report backup/DR health across datasets and VMs."""
    cp = _load_or_build(args.state)
    if args.run:
        rec = cp.run_backup(args.run)
        if rec is None:
            raise SystemExit(f"unknown resource {args.run!r}")
        print(f"{args.run}: backed up ({_c('now current', GREEN)})")
        _save(cp, args.state)
        return 0
    h = cp.backup_health()
    _header(f"Backup / DR health — {h['protected_pct']}% protected ({h['stale']} stale)")
    for r in h["records"]:
        flag = _c("STALE", RED) if r["status"] == "stale" else _c("ok", GREEN)
        print(f"  {r['resource']:16} {r['kind']:8} {r['schedule']:7} "
              f"last {r['last_backup_hours']:>4}h  {r['target']:12} {flag}")
    return 0


def cmd_net(args: argparse.Namespace) -> int:
    """Network view: check east-west reachability, move a device's VLAN, or list segments."""
    cp = _load_or_build(args.state)
    # A single reachability check?
    if args.check:
        try:
            src, dst = args.check
        except ValueError as exc:  # pragma: no cover - argparse enforces nargs=2
            raise SystemExit("--check takes SRC DST") from exc
        r = cp.check_segmentation(src, dst)
        print(f"{src} -> {dst}: {_yes_no(r['allowed'])}  {DIM}{r['reason']}{RESET}")
        _save(cp, args.state)
        return 0
    if args.move:
        name, segment = args.move
        dev = cp.move_device(name, segment)
        if dev is None:
            raise SystemExit(f"unknown device or segment: {name!r} -> {segment!r}")
        print(f"{name}: moved to {_c(segment, CYAN)}")
        _save(cp, args.state)
        return 0

    s = cp.network_summary()
    _header("Network segments (VLANs)")
    for seg in s["segments"]:
        net_flag = "internet" if seg["internet"] else _c("no-internet", DIM)
        print(f"  VLAN {seg['vlan_id']:<3} {seg['name']:6} {seg['cidr']:16} "
              f"trust={seg['trust']:6} {net_flag}  {DIM}{seg['purpose']}{RESET}")
    _header("Devices")
    for d in s["devices"]:
        owner = f" ({d['owner']})" if d["owner"] else ""
        print(f"  {d['name']:20} {d['kind']:12} {d['segment']:6} {DIM}{d['ip']}{owner}{RESET}")
    _header("Segmentation flags")
    if s["flags"]:
        for f in s["flags"]:
            print(f"  {_c('!', RED)} {f}")
    else:
        print(_c("  clean -- every device is on its expected segment", GREEN))
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    """Print cost analytics: SaaS spend by department vs budget, plus vendor spend by category."""
    cp = _load_or_build(args.state)
    c = cp.cost_analytics()
    _header("Cost analytics")
    print(f"  SaaS: {_c('$' + format(c['saas_monthly_total'], ',.0f') + '/mo', CYAN)} "
          f"(${c['saas_annual_total']:,.0f}/yr)   "
          f"Vendors: {_c('$' + format(c['vendor_annual_total'], ',.0f') + '/yr', CYAN)}")
    if c["orphaned_monthly"]:
        print(f"  {_c('!', RED)} ${c['orphaned_monthly']:,.0f}/mo reclaimable from orphaned seats")
    print(f"\n  {BOLD}SaaS by department (monthly vs budget){RESET}")
    for d in c["by_department"]:
        budget = f"/ ${d['budget']:,.0f}" if d["budget"] is not None else ""
        flag = _c("OVER", RED) if d["over_budget"] else _c("ok", GREEN)
        print(f"    {d['department']:14} ${d['monthly']:>7,.0f} {budget:10} {flag}")
    print(f"\n  {BOLD}Vendor spend by category (annual){RESET}")
    for v in c["vendor_by_category"]:
        print(f"    {v['category']:22} ${v['annual']:>10,.0f}")
    return 0


def cmd_ops(args: argparse.Namespace) -> int:
    """Print the operations dashboard: SaaS spend plus every outstanding ops flag."""
    cp = _load_or_build(args.state)
    s = cp.ops_summary()
    _header("Operations dashboard")
    spend = f"${s['monthly_saas_spend']:,.0f}/mo"
    print(f"  SaaS spend       : {_c(spend, CYAN)} across {s['saas_apps']} apps")
    print(f"  Equipment        : {s['equipment']} assets")

    def _flags(title, items, colour=RED):
        mark = _c("!", colour)
        if items:
            print(f"  {title}:")
            for item in items:
                print(f"    {mark} {item}")

    _flags("Orphaned SaaS seats (inactive users)", s["orphaned_seats"])
    _flags("Overdue maintenance", s["overdue_equipment"])
    _flags("Maintenance due soon", s["due_equipment"], CYAN)
    _flags("Low stock", s["low_stock"])
    _flags("Upcoming renewals (<=60d)", s["upcoming_renewals"], CYAN)
    _flags("Open safety issues", s["open_safety"])
    clean = not any(s[k] for k in ("orphaned_seats", "overdue_equipment", "low_stock", "open_safety"))
    if clean:
        print(_c("  all clear", GREEN))
    return 0


def cmd_saas(args: argparse.Namespace) -> int:
    """List SaaS licences with per-seat and total monthly/annual cost."""
    cp = _load_or_build(args.state)
    _header("SaaS licences")
    for app in sorted(cp.ops.saas.values(), key=lambda a: a.name):
        seats = len(app.assignees)
        monthly = app.monthly_cost_per_seat * seats
        print(f"  {app.name:18} {seats:2} seats  ${app.monthly_cost_per_seat:>5.1f}/seat  = ${monthly:>7.0f}/mo")
    print(_c(f"  total: ${cp.ops.monthly_spend():,.0f}/mo  (${cp.ops.annual_saas_cost():,.0f}/yr)", CYAN))
    return 0


def cmd_assets(args: argparse.Namespace) -> int:
    """List equipment/assets with maintenance status (overdue, due soon, or ok)."""
    cp = _load_or_build(args.state)
    _header("Equipment / assets")
    for e in cp.ops.equipment:
        if e.maintenance_in_days < 0:
            m = _c(f"overdue {-e.maintenance_in_days}d", RED)
        elif e.maintenance_in_days <= 14:
            m = _c(f"due {e.maintenance_in_days}d", CYAN)
        else:
            m = f"{e.maintenance_in_days}d"
        print(f"  {e.asset_tag}  {e.name:30} {e.location:14} maint {m}")
    return 0


def cmd_inventory(args: argparse.Namespace) -> int:
    """List reagent/consumable inventory, flagging items below their reorder point."""
    cp = _load_or_build(args.state)
    _header("Inventory")
    for i in cp.ops.inventory:
        flag = _c("LOW", RED) if i.low else "ok"
        print(f"  {i.sku:8} {i.name:22} {i.qty:3} {i.unit:8} (reorder {i.reorder_point})  {flag}")
    return 0


def cmd_vendors(args: argparse.Namespace) -> int:
    """List vendor contracts by renewal date, flagging renewals due soon."""
    cp = _load_or_build(args.state)
    _header("Vendors / contracts")
    for v in sorted(cp.ops.vendors, key=lambda x: x.renewal_in_days):
        soon = _c("renews soon", CYAN) if v.renewal_in_days <= 60 else ""
        print(f"  {v.name:16} {v.category:22} renews {v.renewal_in_days:3}d  ${v.annual_cost:>8,.0f}/yr  {soon}")
    return 0


def cmd_safety(args: argparse.Namespace) -> int:
    """List facility safety checks, marking open issues vs passing ones."""
    cp = _load_or_build(args.state)
    _header("Facility safety checks")
    for s in cp.ops.safety:
        mark = _c("OPEN", RED) if s.status == "open" else _c("pass", GREEN)
        note = f"{DIM} — {s.note}{RESET}" if s.note else ""
        print(f"  {mark:5} {s.area:16} {s.check}{note}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """Run the SCIM reconcile from Okta to Active Directory and print what changed."""
    cp = _load_or_build(args.state)
    report = cp.sync()
    _header("SCIM reconcile: Okta -> Active Directory")
    print(f"  {report.summary()}")
    print(f"  changed: {report.changed}")
    _save(cp, args.state)
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    """Print the tail of the audit trail (default last 20 events)."""
    cp = _load_or_build(args.state)
    _header(f"Audit log (last {args.tail})")
    for event in cp.audit.tail(args.tail):
        outcome = _c(event.outcome, GREEN if event.outcome in ("success", "noop") else RED)
        print(f"  {event.system:14} {event.action:18} {event.target:22} {outcome}  {DIM}{event.detail}{RESET}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """A scripted end-to-end story that exercises the whole stack."""
    print(_c("LabSuite -- Okta -> Active Directory -> TrueNAS + Proxmox", BOLD))
    cp = build_lab()

    _header("1. The lab is seeded and Okta is synced to AD")
    print(f"  {len(cp.okta.list_users())} users, {len(cp.okta.list_groups())} Okta groups, "
          f"{len(cp.truenas.list_shares())} TrueNAS shares, {len(cp.proxmox.list_vms())} Proxmox guests")

    _header("2. Onboard a new in-vivo scientist")
    hire = cp.onboard("Nadia Rahman", Department.INVIVO, "invivo-scientist", title="In-Vivo Scientist")
    print(f"  created {_c(hire.username, CYAN)} ({hire.email}), temp password {_c(hire.temp_password, CYAN)}")
    print(f"  Okta groups: {', '.join(hire.okta_groups)}  {DIM}(In-Vivo -> Research via AD nesting){RESET}")
    print(f"  -> TrueNAS: {hire.truenas_access}")
    if hire.device:
        img = IMAGE_CATALOG[hire.device["image"]]
        print(f"  -> Device: {hire.device['model']} · {hire.device['image']} · {img.security_summary()}")
    print(f"  -> Training required (pending): {', '.join(hire.trainings)}")
    for share, missing in hire.gated.items():
        print(f"     {_c('gated', RED)} {share} until {', '.join(missing)} complete")

    _header("3. She logs in through Okta")
    cp.okta.set_password(hire.username, DEMO_PASSWORD)
    token = cp.login(hire.username, DEMO_PASSWORD)
    print(f"  {_c('authenticated', GREEN)}; session JWT {token[:40]}...")

    _header("4. Access decisions (resolved Okta -> AD -> compliance -> resource)")
    checks = [
        ("truenas", "research-data", "modify", "write research data"),
        ("truenas", "invivo-study-data", "modify", "write in-vivo study data"),
        ("truenas", "legal-contracts", "read", "read legal contracts"),
    ]
    for _system, resource, action, note in checks:
        d = cp.check_truenas(hire.username, str(resource), action)
        print(f"  {_yes_no(d.allowed):22} {note:32} {DIM}({d.reason}){RESET}")

    _header("5. Compliance gate: she completes IACUC + biosafety training")
    cp.complete_training(hire.username, "IACUC")
    cp.complete_training(hire.username, "Biosafety")
    d = cp.check_truenas(hire.username, "invivo-study-data", "modify")
    print(f"  {_yes_no(d.allowed):22} {'in-vivo study data (now trained)':32} {DIM}({d.reason}){RESET}")

    _header("6. Offboard her -- same-day, verified")
    off = cp.offboard(hire.username)
    verdict = _c("CLEAN: zero residual access", GREEN) if off.clean else _c("RESIDUAL!", RED)
    print(f"  deprovisioned across Okta + AD + downstream -> {verdict}")
    print("  re-login attempt: ", end="")
    try:
        cp.login(hire.username, DEMO_PASSWORD)
        print(_c("SUCCEEDED (bug!)", RED))
    except PermissionError:
        print(_c("correctly rejected", GREEN))

    _header("7. Quarterly access review")
    review = cp.access_review()
    print(f"  reviewed {len(review['entitlements'])} identities; {len(review['flags'])} flag(s):")
    for flag in review["flags"][:6]:
        print(f"    {_c('!', RED)} {flag}")

    _header("8. Everything above was audited")
    print(f"  {len(cp.audit.events)} audit events recorded. Last few:")
    for event in cp.audit.tail(5):
        print(f"    {DIM}{event.system:12} {event.action:16} {event.target:20} {event.outcome}{RESET}")
    print()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Launch the live FastAPI control plane with uvicorn (needs the [api] extra)."""
    try:
        import uvicorn

        from labsuite.api import create_app
    except ImportError:
        raise SystemExit(
            "the live API needs the [api] extra:  pip install -e '.[api]'"
        ) from None
    app = create_app(build_lab())
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
# HOW TO ADD A NEW COMMAND
# 1. Write a `cmd_x(args)` function above that:
#      - loads state via `_load_or_build(args.state)` to get a ControlPlane;
#      - reads/mutates the control plane (call `control.*` / `cp.*` methods);
#      - calls `_save(cp, args.state)` if (and only if) it mutated state;
#      - prints its output and returns an int exit code (0 = ok).
# 2. Register it in `build_parser()` below:
#      sub.add_parser("x", help=...).set_defaults(func=cmd_x)
#    Add `p.add_argument(...)` lines for any flags before `set_defaults`.
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="labsuite",
        description="Identity-to-infrastructure access governance: Okta -> AD -> TrueNAS + Proxmox.",
    )
    parser.add_argument("--state", help="path to a JSON state file to load/save (persist between runs)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="run the scripted end-to-end story").set_defaults(func=cmd_demo)
    sub.add_parser("org", help="print the seeded org").set_defaults(func=cmd_org)

    p = sub.add_parser("onboard", help="provision a new hire across the stack")
    p.add_argument("--name", required=True)
    p.add_argument("--department", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--title", default="")
    p.set_defaults(func=cmd_onboard)

    p = sub.add_parser("offboard", help="deprovision a user same-day and verify")
    p.add_argument("--user", required=True)
    p.set_defaults(func=cmd_offboard)

    p = sub.add_parser("login", help="authenticate a user against Okta")
    p.add_argument("--user", required=True)
    p.add_argument("--password")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("access", help="show everything a user can touch")
    p.add_argument("--user", required=True)
    p.set_defaults(func=cmd_access)

    p = sub.add_parser("check", help="one audited allow/deny decision")
    p.add_argument("--user", required=True)
    p.add_argument("--system", required=True, choices=["truenas", "proxmox"])
    p.add_argument("--resource", required=True, help="share name (truenas) or vmid (proxmox)")
    p.add_argument("--action", required=True, help="read/modify/full or vm.power/vm.migrate/...")
    p.set_defaults(func=cmd_check)

    sub.add_parser("review", help="quarterly access review with anomaly flags").set_defaults(func=cmd_review)

    p = sub.add_parser("request", help="request access to a group")
    p.add_argument("--user", required=True)
    p.add_argument("--group", required=True)
    p.add_argument("--why", help="justification")
    p.set_defaults(func=cmd_request)

    sub.add_parser("requests", help="list access requests").set_defaults(func=cmd_requests)

    p = sub.add_parser("approve", help="approve an access request")
    p.add_argument("--id", required=True)
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("deny", help="deny an access request")
    p.add_argument("--id", required=True)
    p.add_argument("--note")
    p.set_defaults(func=cmd_deny)

    sub.add_parser("devices", help="list the managed laptop fleet").set_defaults(func=cmd_devices)
    sub.add_parser("compliance", help="show training records").set_defaults(func=cmd_compliance)

    p = sub.add_parser("mfa", help="show or --enroll a user's MFA (Okta Verify)")
    p.add_argument("--user", required=True)
    p.add_argument("--enroll", action="store_true")
    p.set_defaults(func=cmd_mfa)

    p = sub.add_parser("train", help="record a training as complete (or --expire it)")
    p.add_argument("--user", required=True)
    p.add_argument("--training", required=True)
    p.add_argument("--expire", action="store_true", help="mark the training lapsed instead of complete")
    p.set_defaults(func=cmd_train)

    p = sub.add_parser("net", help="network segments, devices, and segmentation checks")
    p.add_argument("--check", nargs=2, metavar=("SRC", "DST"), help="test east-west reachability between two segments")
    p.add_argument("--move", nargs=2, metavar=("DEVICE", "SEGMENT"), help="move a device onto another VLAN")
    p.set_defaults(func=cmd_net)

    p = sub.add_parser("campaign", help="access-review campaign (attestation): certify/revoke per user")
    p.add_argument("--start", action="store_true", help="open a new campaign over all active users")
    p.add_argument("--name", help="campaign name (with --start)")
    p.add_argument("--certify", metavar="USER", help="attest a user's access is appropriate")
    p.add_argument("--revoke", metavar="USER", help="revoke a user's entitlements")
    p.add_argument("--reviewer", help="who is making the decision")
    p.add_argument("--note", help="decision note")
    p.set_defaults(func=cmd_campaign)

    p = sub.add_parser("jit", help="break-glass (just-in-time) admin: time-bound elevation")
    p.add_argument("--grant", nargs=2, metavar=("USER", "GROUP"), help="elevate USER into GROUP")
    p.add_argument("--minutes", type=int, default=60, help="grant lifetime (with --grant)")
    p.add_argument("--reason", help="why (with --grant)")
    p.add_argument("--revoke", metavar="GRANT_ID", help="end a grant early")
    p.add_argument("--sweep", action="store_true", help="auto-expire lapsed grants")
    p.add_argument("--actor", help="who is performing the action")
    p.set_defaults(func=cmd_jit)

    p = sub.add_parser("export", help="export a report as CSV (access | saas | audit)")
    p.add_argument("kind", choices=["access", "saas", "audit"])
    p.add_argument("--out", help="write to a file instead of stdout")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("readiness", help="onboarding readiness checklist (day-one ready?)")
    p.add_argument("--user", help="show one hire's full checklist")
    p.set_defaults(func=cmd_readiness)

    sub.add_parser("alerts", help="action center -- every outstanding flag in one feed").set_defaults(func=cmd_alerts)
    sub.add_parser("ops", help="operations dashboard (SaaS spend + flags)").set_defaults(func=cmd_ops)
    sub.add_parser("cost", help="cost analytics: SaaS by dept vs budget + vendor spend").set_defaults(func=cmd_cost)
    sub.add_parser("saas", help="SaaS licences + cost").set_defaults(func=cmd_saas)
    sub.add_parser("assets", help="equipment + maintenance").set_defaults(func=cmd_assets)
    sub.add_parser("inventory", help="reagent / consumable inventory").set_defaults(func=cmd_inventory)
    sub.add_parser("vendors", help="vendor contracts + renewals").set_defaults(func=cmd_vendors)
    sub.add_parser("safety", help="facility safety checks").set_defaults(func=cmd_safety)

    p = sub.add_parser("backup", help="backup / DR health across datasets + VMs")
    p.add_argument("--run", metavar="RESOURCE", help="run a backup now (e.g. tank/invivo or vm/301)")
    p.set_defaults(func=cmd_backup)
    sub.add_parser("sync", help="run the SCIM reconcile now").set_defaults(func=cmd_sync)

    p = sub.add_parser("audit", help="print the audit trail")
    p.add_argument("--tail", type=int, default=20)
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("serve", help="launch the live FastAPI control plane (needs [api] extra)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except TokenError as exc:  # pragma: no cover - defensive
        print(_c(f"token error: {exc}", RED))
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
