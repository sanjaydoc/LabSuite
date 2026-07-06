/* LabSuite operational dashboard — vanilla JS, no build step.
 *
 * Runs in two modes:
 *   • DEMO (default, e.g. on GitHub Pages): an in-browser engine mirrors the
 *     Python control plane using the generated data.js snapshot, so onboarding,
 *     offboarding, access resolution and reviews all work with no backend.
 *   • LIVE: if a LabSuite FastAPI server is reachable at the same origin, the
 *     UI calls the real /oauth/token, /admin/*, /access, /review endpoints.
 */
"use strict";

const D = window.LABSUITE_DATA;
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (sel, root = document) => root.querySelector(sel);
const byId = (id) => document.getElementById(id);

const ADJ = "brave calm clever bright quick warm keen bold".split(" ");
const NOUN = "otter falcon cedar delta harbor quartz meadow signal".split(" ");
const passphrase = () => `${ADJ[(Math.random() * ADJ.length) | 0]}-${NOUN[(Math.random() * NOUN.length) | 0]}-${1000 + ((Math.random() * 9000) | 0)}`;

/* ------------------------------------------------------------------ *
 * The in-browser engine — a faithful mirror of labsuite.engine
 * ------------------------------------------------------------------ */
class DemoEngine {
  constructor(data) {
    this.d = data;
    this.users = {};
    for (const u of data.users) this.users[u.username] = { ...u, okta_groups: [...u.okta_groups] };
    this.audit = [];
    // child -> [parents] from ad_nesting (parent -> [children])
    this.parentsOf = {};
    for (const parent in data.ad_nesting)
      for (const child of data.ad_nesting[parent]) (this.parentsOf[child] ||= []).push(parent);
    // managed device fleet
    this.devices = {};
    this.deviceByUser = {};
    this.deviceCounter = 0;
    for (const dev of data.devices || []) {
      this.devices[dev.asset_tag] = { ...dev };
      if (dev.assignee) this.deviceByUser[dev.assignee] = dev.asset_tag;
      const n = parseInt(dev.asset_tag.replace(/\D/g, ""), 10);
      if (n > this.deviceCounter) this.deviceCounter = n;
    }
    // compliance training records: username -> {training: status}
    this.trainings = {};
    for (const u in data.compliance_records || {}) this.trainings[u] = { ...data.compliance_records[u] };
    this.gated = data.gated_shares || {};
    // MFA (Okta Verify) enrollment -- conditional access on sensitive shares
    this.mfa = new Set(data.mfa_enrolled || []);
    // operations: SaaS + registries
    const ops = data.operations || {};
    this.saas = {};
    for (const a of ops.saas || []) this.saas[a.name] = { name: a.name, cost: a.monthly_cost_per_seat, assignees: new Set(a.assignees || []) };
    this.equipment = ops.equipment || [];
    this.inventory = ops.inventory || [];
    this.vendors = ops.vendors || [];
    this.safety = ops.safety || [];
    this.requests = (data.requests || []).map((r) => ({ ...r }));
    this.reqCounter = this._maxReq();
    // network: VLAN segments + devices + east-west policy
    const net = data.network || {};
    this.segments = {};
    for (const s of net.segments || []) this.segments[s.name] = { ...s };
    this.netDevices = (net.devices || []).map((d) => ({ ...d }));
    this.netPolicy = new Set((net.policy || []).map((p) => p[0] + "->" + p[1]));
    // access-review campaign (attestation)
    const cmp = data.campaign || {};
    this.campaign = { name: cmp.name || "", subjects: [...(cmp.subjects || [])], decisions: { ...(cmp.decisions || {}) }, open: cmp.open !== false };
    // break-glass (just-in-time) elevation ledger
    const j = data.jit || {};
    this.jit = { grants: (j.grants || []).map((g) => ({ ...g })), counter: j.counter || 0 };
    // backup / DR health ledger
    this.backups = ((data.backups || {}).records || []).map((r) => ({ ...r }));
  }

  // ---- backup / DR health ------------------------------------------
  _backupThreshold(sched) { return ({ hourly: 6, daily: 36, weekly: 192 })[sched] ?? 36; }
  _backupStale(r) { return r.last_backup_hours > this._backupThreshold(r.schedule); }
  backupHealth() {
    const recs = this.backups.slice()
      .sort((a, b) => (a.kind + a.resource).localeCompare(b.kind + b.resource))
      .map((r) => ({ ...r, status: this._backupStale(r) ? "stale" : "ok" }));
    const stale = recs.filter((r) => r.status === "stale");
    return {
      records: recs, total: recs.length, stale: stale.length,
      protected_pct: recs.length ? Math.round((100 * (recs.length - stale.length)) / recs.length) : 100,
      flags: stale.map((r) => `${r.resource} (${r.kind}): backup ${r.last_backup_hours}h old — ${r.schedule} schedule`),
    };
  }
  runBackup(resource) {
    const r = this.backups.find((x) => x.resource === resource);
    if (r) { r.last_backup_hours = 0; this.log("control-plane", "backup.run", resource, "success", r.target); }
    return r;
  }

  // ---- break-glass (just-in-time) admin ----------------------------
  _now() { return Date.now() / 1000; }
  grantJit(user, group, ttl, reason) {
    if (!this.users[user]) throw new Error(`no such user ${user}`);
    const now = this._now();
    const g = { id: `JIT-${String(++this.jit.counter).padStart(4, "0")}`, username: user, group, reason: reason || "",
      granted_by: "it-admin", granted_at: now, ttl_minutes: ttl, expires_at: now + ttl * 60, revoked: false };
    this.jit.grants.push(g);
    (this.users[user].okta_groups ||= []);
    if (!this.users[user].okta_groups.includes(group)) this.users[user].okta_groups.push(group);
    this.log("control-plane", "jit.grant", `${g.id}:${user}`, "success", `${group} for ${ttl}m`);
    return g;
  }
  _reclaimJit(g) {
    const still = this.jit.grants.some((x) => x.group === g.group && x.username === g.username && x.id !== g.id && !x.revoked);
    if (!still && this.users[g.username]) this.users[g.username].okta_groups = this.users[g.username].okta_groups.filter((x) => x !== g.group);
  }
  revokeJit(id) {
    const g = this.jit.grants.find((x) => x.id === id);
    if (!g || g.revoked) return;
    this._reclaimJit(g); g.revoked = true;
    this.log("control-plane", "jit.revoke", id, "denied", "reclaimed");
  }
  sweepJit() {
    const now = this._now();
    const exp = this.jit.grants.filter((g) => !g.revoked && now >= g.expires_at);
    for (const g of exp) { this._reclaimJit(g); g.revoked = true; this.log("control-plane", "jit.expire", g.id, "success", "auto-expired"); }
    return exp;
  }
  jitStatus() {
    const now = this._now();
    const rem = (g) => Math.max(0, Math.floor((g.expires_at - now) / 60));
    return {
      now,
      active: this.jit.grants.filter((g) => !g.revoked && now < g.expires_at).map((g) => ({ ...g, remaining_minutes: rem(g) })),
      expired_unswept: this.jit.grants.filter((g) => !g.revoked && now >= g.expires_at),
      all: this.jit.grants,
    };
  }

  // ---- access-review campaign --------------------------------------
  startCampaign(name) {
    const subjects = Object.values(this.users).filter((u) => u.active).map((u) => u.username);
    this.campaign = { name: name || "Access review", subjects, decisions: {}, open: true };
    for (const u of subjects) this.campaign.decisions[u] = { status: "pending", reviewer: "", note: "" };
    this.log("control-plane", "campaign.start", this.campaign.name, "success", `${subjects.length} subjects`);
    return this.campaignProgress();
  }
  certifyUser(u) {
    this.campaign.decisions[u] = { status: "certified", reviewer: "it-admin", note: "" };
    this.log("control-plane", "campaign.certify", u, "success");
    return this.campaignProgress();
  }
  revokeUser(u) {
    if (this.users[u]) this.users[u].okta_groups = [];  // strip entitlements
    this.campaign.decisions[u] = { status: "revoked", reviewer: "it-admin", note: "" };
    this.log("control-plane", "campaign.revoke", u, "denied", "entitlements stripped");
    return this.campaignProgress();
  }
  campaignProgress() {
    const c = this.campaign, total = c.subjects.length;
    const certified = Object.values(c.decisions).filter((d) => d.status === "certified").length;
    const revoked = Object.values(c.decisions).filter((d) => d.status === "revoked").length;
    return { name: c.name, open: c.open, total, certified, revoked, pending: total - certified - revoked,
      completion_pct: total ? Math.round((100 * (certified + revoked)) / total) : 100 };
  }
  campaignStatus() {
    const rows = this.campaign.subjects.map((u) => {
      const a = this.resolveAccess(u), d = this.campaign.decisions[u] || {};
      return { username: u, status: d.status || "pending", reviewer: d.reviewer || "",
        shares: Object.keys(a.truenas).length, vms: Object.keys(a.proxmox).length };
    });
    return { progress: this.campaignProgress(), rows };
  }

  // ---- network / segmentation --------------------------------------
  canReach(src, dst) {
    if (!this.segments[src] || !this.segments[dst]) return { allowed: false, reason: "unknown segment" };
    if (src === dst) return { allowed: true, reason: "same segment (intra-VLAN)" };
    if (this.netPolicy.has(src + "->" + dst)) return { allowed: true, reason: `firewall rule allows ${src} → ${dst}` };
    return { allowed: false, reason: `default-deny: no rule permits ${src} → ${dst}` };
  }
  checkSegmentation(src, dst) {
    const r = this.canReach(src, dst);
    this.log("network", "network.check", `${src}->${dst}`, r.allowed ? "success" : "denied", r.reason);
    return { src, dst, ...r };
  }
  moveDevice(name, segment) {
    const d = this.netDevices.find((x) => x.name === name);
    if (!d || !this.segments[segment]) return null;
    d.segment = segment;
    this.log("network", "network.move", name, "success", `-> ${segment}`);
    return d;
  }
  netFlags() {
    const expected = { laptop: "Corp", workstation: "Corp", instrument: "Lab", printer: "Lab",
      camera: "IoT", "badge-reader": "IoT", sensor: "IoT", guest: "Guest" };
    const out = [];
    for (const d of this.netDevices) {
      const exp = expected[d.kind];
      if (exp && this.segments[exp] && d.segment !== exp) {
        const seg = this.segments[d.segment];
        const sev = seg && (seg.trust === "high" || seg.trust === "medium") && exp === "IoT" ? "MISPLACED" : "off-segment";
        out.push(`${d.name} (${d.kind}): ${sev} on ${d.segment} — expected ${exp}`);
      }
    }
    return out;
  }
  networkSummary() {
    return {
      segments: Object.values(this.segments).sort((a, b) => a.vlan_id - b.vlan_id),
      devices: this.netDevices.slice().sort((a, b) => (a.segment + a.name).localeCompare(b.segment + b.name)),
      policy: [...this.netPolicy].map((p) => p.split("->")),
      flags: this.netFlags(),
      device_count: this.netDevices.length,
    };
  }

  // ---- cost analytics & budgets ------------------------------------
  costAnalytics() {
    const budgets = this.d.saas_budget || {};
    const r2 = (x) => Math.round(x * 100) / 100;
    const byDept = {}; let orphaned = 0;
    for (const name in this.saas) {
      const app = this.saas[name];
      for (const user of app.assignees) {
        const d = this.users[user] ? this.users[user].department : "Unknown";
        byDept[d] = (byDept[d] || 0) + app.cost;
        if (!this.isActive(user)) orphaned += app.cost;
      }
    }
    const deptRows = Object.keys(byDept).sort().map((d) => {
      const b = budgets[d], spend = byDept[d];
      return { department: d, monthly: r2(spend), annual: r2(spend * 12), budget: b ?? null, over_budget: b != null && spend > b };
    });
    const vendorByCat = {}; let vTotal = 0;
    for (const v of this.vendors) { vendorByCat[v.category] = (vendorByCat[v.category] || 0) + v.annual_cost; vTotal += v.annual_cost; }
    const saasMonthly = r2(Object.values(byDept).reduce((a, b) => a + b, 0));
    return {
      saas_monthly_total: saasMonthly, saas_annual_total: r2(saasMonthly * 12), by_department: deptRows,
      vendor_annual_total: r2(vTotal),
      vendor_by_category: Object.keys(vendorByCat).sort().map((c) => ({ category: c, annual: r2(vendorByCat[c]) })),
      orphaned_monthly: r2(orphaned), orphaned_annual: r2(orphaned * 12),
      over_budget_departments: deptRows.filter((r) => r.over_budget).map((r) => r.department),
    };
  }

  // ---- onboarding readiness checklist ------------------------------
  onboardingChecklist(username) {
    const u = this.users[username];
    if (!u) throw new Error(`no such user ${username}`);
    const groups = u.okta_groups || [];
    const devTag = this.deviceByUser[username];
    const device = devTag ? this.devices[devTag] : null;
    const trainings = this.trainingsFor(username);
    const tKeys = Object.keys(trainings);
    const trainingsDone = tKeys.length > 0 && tKeys.every((t) => trainings[t] === "current");
    const saas = Object.values(this.saas).filter((a) => a.assignees.has(username)).map((a) => a.name);
    const items = [
      { item: "Okta account active", done: !!u.active, required: true, detail: u.email },
      { item: "Security groups assigned", done: groups.length > 0, required: true, detail: `${groups.length} group(s)` },
      { item: "Synced to Active Directory", done: groups.length > 0, required: true, detail: "" },
      { item: "Laptop imaged & shipped", done: !!device && device.status === "assigned", required: true, detail: device ? `${device.asset_tag} · ${device.image}` : "no device" },
      { item: "MFA (Okta Verify) enrolled", done: this.isMfaEnrolled(username), required: true, detail: "" },
      { item: "SaaS seats provisioned", done: saas.length > 0, required: true, detail: `${saas.length} app(s)` },
      { item: "Required training complete", done: trainingsDone, required: false, detail: tKeys.length ? tKeys.map((t) => `${t}:${trainings[t]}`).join(", ") : "none required" },
    ];
    const required = items.filter((i) => i.required);
    const done = required.filter((i) => i.done).length;
    return { username, display_name: u.display_name, items, ready: done === required.length, completion_pct: Math.round((100 * done) / required.length) };
  }
  readinessSummary() {
    const rows = Object.values(this.users).filter((u) => u.active).map((u) => {
      const c = this.onboardingChecklist(u.username);
      return { username: u.username, display_name: u.display_name, ready: c.ready, completion_pct: c.completion_pct };
    });
    rows.sort((a, b) => (a.ready - b.ready) || a.username.localeCompare(b.username));
    return { rows, ready_count: rows.filter((r) => r.ready).length, total: rows.length };
  }

  // ---- action center: aggregate every outstanding flag --------------
  actionCenter() {
    const alerts = [];
    const add = (severity, category, title, view, detail = "") => alerts.push({ severity, category, title, view, detail });
    for (const username in this.users) {
      const u = this.users[username];
      if (!u.active) {
        const r = this.resolveAccess(username);
        if (Object.keys(r.truenas).length || Object.keys(r.proxmox).length)
          add("high", "access", `${username}: inactive but still has access`, "review");
        continue;
      }
      const tr = this.trainingsFor(username);
      for (const t in tr) if (tr[t] === "expired") add("high", "compliance", `${username}: ${t} training lapsed`, "compliance", "gated access auto-revoked");
      if (!this.isMfaEnrolled(username)) add("medium", "mfa", `${username}: not enrolled in MFA`, "explorer", "conditional-access resources are blocked");
    }
    const ops = this.opsSummary();
    for (const name of ops.overdue_equipment) add("high", "equipment", `Maintenance overdue: ${name}`, "ops");
    for (const name of ops.low_stock) add("medium", "inventory", `Low stock: ${name}`, "ops");
    for (const item of ops.open_safety) add("high", "safety", `Safety: ${item}`, "ops");
    for (const seat of ops.orphaned_seats) add("medium", "saas", `Orphaned SaaS seat: ${seat}`, "saas");
    for (const r of ops.upcoming_renewals) add("info", "vendor", `Renewal due: ${r}`, "ops");
    const costs = this.costAnalytics();
    for (const d of costs.over_budget_departments) {
      const row = costs.by_department.find((r) => r.department === d);
      add("medium", "cost", `${d} over SaaS budget ($${Math.round(row.monthly)} > $${Math.round(row.budget)})`, "cost");
    }
    if (costs.orphaned_monthly) add("medium", "cost", `$${Math.round(costs.orphaned_monthly)}/mo in orphaned SaaS seats reclaimable`, "cost");
    for (const flag of this.netFlags()) add(flag.includes("MISPLACED") ? "high" : "medium", "network", flag, "network");
    for (const r of this.backupHealth().records) if (r.status === "stale") add("high", "backup", `Stale backup: ${r.resource} (${r.last_backup_hours}h old)`, "backup");
    const pending = this.requests.filter((r) => r.status === "pending");
    if (pending.length) add("info", "requests", `${pending.length} access request(s) awaiting approval`, "requests");
    if (this.campaign.open && this.campaign.subjects.length) {
      const prog = this.campaignProgress();
      if (prog.pending) add("medium", "campaign", `${prog.pending} user(s) not yet reviewed (${prog.completion_pct}% done)`, "review");
    }
    const js = this.jitStatus();
    for (const g of js.active) add("info", "break-glass", `${g.username} is elevated to ${g.group} (${g.remaining_minutes}m left)`, "jit");
    for (const g of js.expired_unswept) add("high", "break-glass", `${g.username}: ${g.group} grant lapsed but not reclaimed — run a sweep`, "jit");
    const order = { high: 0, medium: 1, info: 2 };
    alerts.sort((a, b) => (order[a.severity] ?? 3) - (order[b.severity] ?? 3));
    const counts = { high: 0, medium: 0, info: 0 };
    for (const a of alerts) counts[a.severity]++;
    return { alerts, counts, total: alerts.length };
  }

  // ---- compliance ---------------------------------------------------
  missingForShare(username, share) {
    const req = this.gated[share] || [];
    const rec = this.trainings[username] || {};
    return req.filter((t) => rec[t] !== "current");
  }
  completeTraining(username, training) {
    (this.trainings[username] ||= {})[training] = "current";
    this.log("compliance", "training.complete", username, "success", training);
  }
  expireTraining(username, training) {
    (this.trainings[username] ||= {})[training] = "expired";
    this.log("compliance", "training.expire", username, "denied", training);
  }
  trainingsFor(username) { return { ...(this.trainings[username] || {}) }; }

  // ---- MFA / conditional access -------------------------------------
  isMfaEnrolled(username) { return this.mfa.has(username); }
  enrollMfa(username) {
    if (!this.users[username]) throw new Error(`no such user ${username}`);
    this.mfa.add(username);
    this.log("okta", "mfa.enroll", username, "success", "Okta Verify");
  }

  // ---- SaaS ---------------------------------------------------------
  saasAppsForRole(role) {
    const seen = {};
    for (const a of [...(this.d.saas_baseline || []), ...((this.d.saas_role_apps || {})[role] || [])]) seen[a] = 1;
    return Object.keys(seen);
  }
  grantSaas(username, role) {
    const apps = this.saasAppsForRole(role);
    for (const a of apps) {
      (this.saas[a] ||= { name: a, cost: (this.d.saas_catalog || {})[a] || 0, assignees: new Set() }).assignees.add(username);
    }
    this.log("saas", "saas.provision", username, "success", apps.join(", "));
    return apps;
  }
  revokeSaas(username) {
    const removed = [];
    for (const name in this.saas) if (this.saas[name].assignees.delete(username)) removed.push(name);
    if (removed.length) this.log("saas", "saas.revoke", username, "success", removed.join(", "));
    return removed.sort();
  }
  appsFor(username) { return Object.keys(this.saas).filter((n) => this.saas[n].assignees.has(username)).sort(); }
  monthlySpend() { return Object.values(this.saas).reduce((t, a) => t + a.cost * a.assignees.size, 0); }
  isActive(username) { const u = this.users[username]; return !!(u && u.active); }
  opsSummary() {
    return {
      monthly_saas_spend: this.monthlySpend(),
      saas_apps: Object.keys(this.saas).length,
      orphaned_seats: Object.values(this.saas).flatMap((a) => [...a.assignees].filter((u) => !this.isActive(u)).map((u) => `${a.name}:${u}`)),
      overdue_equipment: this.equipment.filter((e) => e.maintenance_in_days < 0).map((e) => e.name),
      low_stock: this.inventory.filter((i) => i.low).map((i) => i.name),
      upcoming_renewals: this.vendors.filter((v) => v.renewal_in_days <= 60).map((v) => `${v.name} (${v.renewal_in_days}d)`),
      open_safety: this.safety.filter((s) => s.status === "open").map((s) => `${s.area}: ${s.check}`),
    };
  }

  // ---- ops mutations (fully-operable GUI) ---------------------------
  completeMaintenance(tag) {
    const e = this.equipment.find((x) => x.asset_tag === tag);
    if (e) { e.maintenance_in_days = 90; this.log("operations", "equipment.maintenance", tag, "success", "serviced"); }
    return e;
  }
  reorder(sku) {
    const i = this.inventory.find((x) => x.sku === sku);
    if (i) { i.qty += i.reorder_point * 2; i.low = i.qty <= i.reorder_point; this.log("operations", "inventory.reorder", sku, "success", `qty -> ${i.qty}`); }
    return i;
  }
  resolveSafety(area, check) {
    const s = this.safety.find((x) => x.area === area && x.check === check);
    if (s) { s.status = "pass"; s.note = ""; this.log("operations", "safety.resolve", `${area}:${check}`, "success"); }
    return s;
  }
  renewVendor(name) {
    const v = this.vendors.find((x) => x.name === name);
    if (v) { v.renewal_in_days = 365; this.log("operations", "vendor.renew", name, "success"); }
    return v;
  }
  // ---- access requests + approvals ---------------------------------
  requestAccess(user, group, justification) {
    this.reqCounter = (this.reqCounter || this._maxReq()) + 1;
    const req = { id: "REQ-" + String(this.reqCounter).padStart(4, "0"), requester: user, group,
      justification: justification || "", status: "pending", decided_by: "", note: "" };
    this.requests.push(req);
    this.log(user, "access.request", `${req.id}:${group}`, "success", justification || "");
    return req;
  }
  _maxReq() {
    return this.requests.reduce((m, r) => Math.max(m, parseInt(r.id.replace(/\D/g, ""), 10) || 0), 0);
  }
  approveRequest(id) {
    const r = this.requests.find((x) => x.id === id);
    if (!r || r.status !== "pending") return r;
    const u = this.users[r.requester];
    if (u && !u.okta_groups.includes(r.group)) u.okta_groups.push(r.group);
    r.status = "approved"; r.decided_by = "it-admin";
    this.log("it-admin", "request.approve", `${r.id}:${r.group}`, "success", `granted ${r.group} to ${r.requester}`);
    return r;
  }
  denyRequest(id, note) {
    const r = this.requests.find((x) => x.id === id);
    if (!r || r.status !== "pending") return r;
    r.status = "denied"; r.decided_by = "it-admin"; r.note = note || "";
    this.log("it-admin", "request.deny", `${r.id}:${r.group}`, "denied", note || "");
    return r;
  }

  grantSaasSeat(username, app) {
    (this.saas[app] ||= { name: app, cost: (this.d.saas_catalog || {})[app] || 0, assignees: new Set() }).assignees.add(username);
    this.log("saas", "saas.grant", username, "success", app);
  }
  revokeSaasSeat(username, app) {
    const ok = this.saas[app] && this.saas[app].assignees.delete(username);
    if (ok) this.log("saas", "saas.revoke", username, "success", app);
    return !!ok;
  }

  assignDevice(username, role) {
    const imageName = this.d.role_image[role] || "mac-standard";
    const img = this.d.image_catalog[imageName];
    let tag = this.deviceByUser[username];
    if (!tag) { this.deviceCounter++; tag = "LT-" + String(this.deviceCounter).padStart(4, "0"); }
    const dev = { asset_tag: tag, model: img.model, platform: img.platform, image: imageName,
      assignee: username, status: "assigned", serial: "C02" + tag.replace(/-/g, "") };
    this.devices[tag] = dev; this.deviceByUser[username] = tag;
    return dev;
  }

  wipeDevice(username) {
    const tag = this.deviceByUser[username];
    if (!tag) return null;
    this.devices[tag].status = "wipe & return";
    return this.devices[tag];
  }

  listDevices() { return Object.values(this.devices); }

  log(system, action, target, outcome = "success", detail = "") {
    this.audit.push({ system, action, target, outcome, detail });
  }

  effectiveGroups(user) {
    if (!user || !user.active) return new Set();
    const eff = new Set(user.okta_groups);
    const frontier = [...eff];
    while (frontier.length) {
      const cur = frontier.pop();
      for (const p of this.parentsOf[cur] || []) if (!eff.has(p)) { eff.add(p); frontier.push(p); }
    }
    return eff;
  }

  truenasLevel(share, groups) {
    const acl = this.d.shares[share].acl;
    let best = 0, via = [];
    for (const g in acl) if (groups.has(g)) { via.push(g); if (acl[g] > best) best = acl[g]; }
    via = best > 0 ? via.filter((g) => acl[g] === best).sort() : [];
    return { level: best, name: this.d.access_levels[best], via };
  }

  proxmoxRole(vmid, groups) {
    const vm = this.d.vms[String(vmid)];
    const paths = ["/", vm.path];
    if (vm.pool) paths.push("/pool/" + vm.pool);
    let best = 0, via = [];
    for (const ace of this.d.proxmox_acl) {
      if (paths.includes(ace.path) && groups.has(ace.group)) {
        if (ace.role > best) { best = ace.role; via = [ace.group]; }
        else if (ace.role === best && best > 0) via.push(ace.group);
      }
    }
    return { role: best, name: this.d.proxmox_roles[best], via: [...new Set(via)].sort() };
  }

  resolveAccess(username) {
    const u = this.users[username];
    const active = !!(u && u.active);
    const groups = this.effectiveGroups(u);
    const mfa = this.isMfaEnrolled(username);
    const truenas = {}, proxmox = {}, blocked = {};
    for (const s in this.d.shares) {
      const r = this.truenasLevel(s, groups);
      if (r.level <= 0) continue;
      let missing = active ? this.missingForShare(username, s) : [];
      if (active && this.d.shares[s].sensitive && !mfa) missing = [...missing, "MFA (Okta Verify)"];
      if (missing.length) blocked[s] = missing;
      else truenas[s] = r.name;
    }
    for (const vmid in this.d.vms) { const r = this.proxmoxRole(vmid, groups); if (r.role > 0) proxmox[this.d.vms[vmid].path] = r.name; }
    const direct = new Set(u ? u.okta_groups : []);
    const nested = [...groups].filter((g) => !direct.has(g));
    return {
      username, active,
      okta_groups: u ? [...u.okta_groups].sort() : [],
      ad_effective_groups: [...groups].sort(), nested,
      truenas, proxmox,
      trainings: this.trainingsFor(username), blocked,
      mfa_enrolled: mfa,
    };
  }

  onboardingGroups(dept, role) {
    const g = [this.d.baseline_group, this.d.department_group[dept], ...this.d.role_blueprints[role].extra_groups];
    return [...new Set(g)];
  }

  uniqueUsername(name) {
    const parts = name.trim().toLowerCase().split(/\s+/);
    let base = parts.length >= 2 ? parts[0][0] + parts[parts.length - 1] : (parts[0] || "user");
    base = base.replace(/[^a-z0-9]/g, "");
    let u = base, n = 1;
    while (this.users[u]) u = base + ++n;
    return u;
  }

  onboard(name, dept, role) {
    const username = this.uniqueUsername(name);
    const email = `${username}@lab.local`;
    const groups = this.onboardingGroups(dept, role);
    this.users[username] = { username, display_name: name, email, department: dept, title: role, active: true, okta_groups: groups };
    this.log("okta", "user.create", username, "success", `role=${role}`);
    this.log("ad", "user.provision", username, "success", "SCIM create");
    const device = this.assignDevice(username, role);
    this.log("endpoint", "device.provision", device.asset_tag, "success", `${device.model} · ${device.image}`);
    // compliance: register required trainings (pending); SaaS: grant seats
    for (const t of (this.d.role_trainings || {})[role] || []) (this.trainings[username] ||= {})[t] ||= "missing";
    const saas = this.grantSaas(username, role);
    const access = this.resolveAccess(username);
    const proxmox = {};
    for (const vmid in this.d.vms) { const p = this.d.vms[vmid].path; if (access.proxmox[p]) proxmox[vmid] = access.proxmox[p]; }
    return { username, email, temp_password: passphrase(), okta_groups: groups, truenas: access.truenas, proxmox, device,
      trainings: access.trainings, gated: access.blocked, saas };
  }

  offboard(username) {
    const u = this.users[username];
    const removed = [...u.okta_groups];
    u.okta_groups = []; u.active = false;
    this.log("okta", "user.deactivate", username, "success", "offboard");
    this.log("ad", "user.deactivate", username, "success", "SCIM deactivate");
    const device = this.wipeDevice(username);
    if (device) this.log("endpoint", "device.wipe", device.asset_tag, "success", "wipe & return");
    const saasRevoked = this.revokeSaas(username);
    const a = this.resolveAccess(username);
    const clean = Object.keys(a.truenas).length === 0 && Object.keys(a.proxmox).length === 0;
    this.log("control-plane", "offboard.verify", username, clean ? "success" : "error", clean ? "zero residual" : "RESIDUAL");
    return { username, removed_groups: removed, clean, residual_truenas: a.truenas, residual_proxmox: a.proxmox, device, saas_revoked: saasRevoked };
  }

  checkTruenas(username, share, action) {
    const required = { read: 1, modify: 2, full: 3 }[action];
    const groups = this.effectiveGroups(this.users[username]);
    const r = this.truenasLevel(share, groups);
    let allowed = r.level >= required;
    const missing = this.missingForShare(username, share);
    const needsMfa = this.d.shares[share] && this.d.shares[share].sensitive && !this.isMfaEnrolled(username);
    let reason;
    if (allowed && missing.length) { allowed = false; reason = `BLOCKED — training not current: ${missing.join(", ")}`; }
    else if (allowed && needsMfa) { allowed = false; reason = "BLOCKED — conditional access: MFA (Okta Verify) required for sensitive data"; }
    else if (allowed) reason = `granted via ${r.via.join(", ")}`;
    else reason = "no group grants sufficient access";
    this.log(username, "access.check", `truenas:${share}`, allowed ? "success" : "denied", `${action} -> ${r.name}`);
    return { allowed, granted: r.name, required: this.d.access_levels[required], via: r.via, reason };
  }

  checkProxmox(username, vmid, priv) {
    const required = this.d.proxmox_privileges[priv];
    const groups = this.effectiveGroups(this.users[username]);
    const r = this.proxmoxRole(vmid, groups);
    const allowed = r.role >= required;
    this.log(username, "access.check", `proxmox:/vms/${vmid}`, allowed ? "success" : "denied", `${priv} -> ${r.name}`);
    return { allowed, granted: r.name, required: this.d.proxmox_roles[required], via: r.via,
      reason: allowed ? `role ${r.name} via ${r.via.join(", ")}` : `role ${r.name} < required ${this.d.proxmox_roles[required]}` };
  }

  review() {
    const entitlements = {}, flags = [];
    for (const username in this.users) {
      const a = this.resolveAccess(username);
      entitlements[username] = { active: a.active, truenas: a.truenas, proxmox: a.proxmox };
      if (!this.users[username].active && (Object.keys(a.truenas).length || Object.keys(a.proxmox).length))
        flags.push(`${username}: INACTIVE but still has downstream access`);
      for (const s in a.truenas) if (this.d.shares[s].sensitive && a.truenas[s] === "full")
        flags.push(`${username}: FULL access to sensitive share '${s}'`);
      for (const p in a.proxmox) if (a.proxmox[p] === "PVEAdmin") flags.push(`${username}: PVEAdmin on ${p}`);
    }
    return { entitlements, flags };
  }

  login(username, password) {
    const u = this.users[username];
    if (!u || !u.active || password !== this.d.demo_password) {
      this.log("okta", "auth.login", username, "denied");
      return { ok: false };
    }
    this.log("okta", "auth.login", username, "success");
    const claims = { sub: username, name: u.display_name, groups: [...u.okta_groups].sort() };
    const b64 = (o) => btoa(JSON.stringify(o)).replace(/=+$/, "");
    const token = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ ...claims, exp: 0 })}.demo-signature`;
    return { ok: true, token, claims };
  }

  directory() {
    return Object.values(this.users).map((u) => {
      const a = this.resolveAccess(u.username);
      return { username: u.username, display_name: u.display_name, department: u.department, active: u.active,
        shares: Object.keys(a.truenas).length, vms: Object.keys(a.proxmox).length };
    }).sort((x, y) => x.username.localeCompare(y.username));
  }

  stats() {
    const users = Object.values(this.users);
    const groups = new Set();
    for (const u of users) for (const g of u.okta_groups) groups.add(g);
    return { users: users.length, active: users.filter((u) => u.active).length,
      groups: groups.size, shares: Object.keys(this.d.shares).length, vms: Object.keys(this.d.vms).length };
  }
}

/* ------------------------------------------------------------------ *
 * Backend abstraction — demo (in-browser) or live (FastAPI)
 * ------------------------------------------------------------------ */
const engine = new DemoEngine(D);

const demoBackend = {
  mode: "demo",
  async stats() { return engine.stats(); },
  async directory() { return engine.directory(); },
  async resolve(u) { return engine.resolveAccess(u); },
  async onboard(name, dept, role) { return engine.onboard(name, dept, role); },
  async offboard(u) { return engine.offboard(u); },
  async checkTruenas(u, s, a) { return engine.checkTruenas(u, s, a); },
  async checkProxmox(u, v, p) { return engine.checkProxmox(u, v, p); },
  async review() { return engine.review(); },
  async login(u, p) { return engine.login(u, p); },
  async audit() { return engine.audit.slice().reverse(); },
  async devices() { return engine.listDevices(); },
  async complianceRecords() { return engine.trainings; },
  async completeTraining(u, t) { engine.completeTraining(u, t); },
  async expireTraining(u, t) { engine.expireTraining(u, t); },
  async saas() {
    return { monthly_spend: engine.monthlySpend(),
      apps: Object.values(engine.saas).map((a) => ({ name: a.name, monthly_cost_per_seat: a.cost, assignees: [...a.assignees].sort() })) };
  },
  async ops() { return engine.opsSummary(); },
  async assets() { return engine.equipment; },
  async inventory() { return engine.inventory; },
  async vendors() { return engine.vendors; },
  async safety() { return engine.safety; },
  async completeMaintenance(tag) { engine.completeMaintenance(tag); },
  async reorder(sku) { engine.reorder(sku); },
  async resolveSafety(area, check) { engine.resolveSafety(area, check); },
  async renewVendor(name) { engine.renewVendor(name); },
  async grantSaasSeat(u, app) { engine.grantSaasSeat(u, app); },
  async revokeSaasSeat(u, app) { engine.revokeSaasSeat(u, app); },
  async requestsList() { return engine.requests.slice(); },
  async createRequest(u, g, j) { engine.requestAccess(u, g, j); },
  async approveRequest(id) { engine.approveRequest(id); },
  async denyRequest(id, note) { engine.denyRequest(id, note); },
  async enrollMfa(u) { engine.enrollMfa(u); },
  async network() { return engine.networkSummary(); },
  async netCheck(src, dst) { return engine.checkSegmentation(src, dst); },
  async netMove(name, segment) { engine.moveDevice(name, segment); },
  async alerts() { return engine.actionCenter(); },
  async campaign() { return engine.campaignStatus(); },
  async startCampaign(name) { return engine.startCampaign(name); },
  async certifyUser(u) { engine.certifyUser(u); },
  async revokeUser(u) { engine.revokeUser(u); },
  async jit() { return engine.jitStatus(); },
  async jitGrant(u, g, ttl, reason) { engine.grantJit(u, g, ttl, reason); },
  async jitRevoke(id) { engine.revokeJit(id); },
  async jitSweep() { return engine.sweepJit(); },
  async readiness() { return engine.readinessSummary(); },
  async readinessUser(u) { return engine.onboardingChecklist(u); },
  async cost() { return engine.costAnalytics(); },
  async backup() { return engine.backupHealth(); },
  async backupRun(resource) { engine.runBackup(resource); },
  async usernames() { return Object.keys(engine.users).sort(); },
};

const liveBackend = {
  mode: "live",
  async _json(url, opts) { const r = await fetch(url, opts); if (!r.ok) throw new Error(r.status); return r.json(); },
  async stats() {
    const users = (await this._json("/scim/v2/Users")).Resources;
    return { users: users.length, active: users.filter((u) => u.active).length, groups: 0,
      shares: Object.keys(D.shares).length, vms: Object.keys(D.vms).length };
  },
  async directory() {
    const users = (await this._json("/scim/v2/Users")).Resources;
    const out = [];
    for (const u of users) {
      const a = await this._json(`/access/${u.userName}`);
      out.push({ username: u.userName, display_name: u.displayName, department: u.department, active: u.active,
        shares: Object.keys(a.truenas).length, vms: Object.keys(a.proxmox).length });
    }
    return out.sort((x, y) => x.username.localeCompare(y.username));
  },
  async resolve(u) { return this._json(`/access/${u}`); },
  async onboard(name, dept, role) {
    const r = await this._json("/admin/onboard", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name, department: dept, role }) });
    return { username: r.username, email: r.email, temp_password: r.temp_password, okta_groups: r.okta_groups, truenas: r.truenas_access, proxmox: r.proxmox_access, device: r.device };
  },
  async offboard(u) {
    const r = await this._json("/admin/offboard", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: u }) });
    return { username: u, removed_groups: r.removed_groups, clean: r.clean, residual_truenas: r.residual_truenas, residual_proxmox: r.residual_proxmox, device: r.device };
  },
  async checkTruenas() { throw new Error("live check via explorer resolve"); },
  async checkProxmox() { throw new Error("live check via explorer resolve"); },
  async review() { return this._json("/review"); },
  async login(u, p) {
    try { const r = await this._json("/oauth/token", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: u, password: p }) });
      const claims = JSON.parse(atob(r.access_token.split(".")[1])); return { ok: true, token: r.access_token, claims };
    } catch { return { ok: false }; }
  },
  async audit() { try { return (await this._json("/audit")).events; } catch { return []; } },
  async devices() { try { return (await this._json("/devices")).devices; } catch { return []; } },
  async complianceRecords() { try { return (await this._json("/compliance")).records; } catch { return {}; } },
  async completeTraining(u, t) { await this._json("/compliance/complete", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: u, training: t }) }); },
  async expireTraining(u, t) { await this._json("/compliance/expire", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: u, training: t }) }); },
  async saas() { try { return await this._json("/saas"); } catch { return { monthly_spend: 0, apps: [] }; } },
  async ops() { try { return await this._json("/ops"); } catch { return {}; } },
  async assets() { try { return (await this._json("/assets")).equipment; } catch { return []; } },
  async inventory() { try { return (await this._json("/inventory")).inventory; } catch { return []; } },
  async vendors() { try { return (await this._json("/vendors")).vendors; } catch { return []; } },
  async safety() { try { return (await this._json("/safety")).safety; } catch { return []; } },
  async _post(url, body) { return this._json(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }); },
  async completeMaintenance(tag) { await this._post("/assets/maintenance", { asset_tag: tag }); },
  async reorder(sku) { await this._post("/inventory/reorder", { sku }); },
  async resolveSafety(area, check) { await this._post("/safety/resolve", { area, check }); },
  async renewVendor(name) { await this._post("/vendors/renew", { name }); },
  async grantSaasSeat(u, app) { await this._post("/saas/grant", { username: u, app_name: app }); },
  async revokeSaasSeat(u, app) { await this._post("/saas/revoke", { username: u, app_name: app }); },
  async requestsList() { try { return (await this._json("/requests")).requests; } catch { return []; } },
  async createRequest(u, g, j) { await this._post("/requests", { requester: u, group: g, justification: j }); },
  async approveRequest(id) { await this._post("/requests/approve", { request_id: id }); },
  async denyRequest(id, note) { await this._post("/requests/deny", { request_id: id, note }); },
  async enrollMfa(u) { await this._post("/mfa/enroll", { username: u }); },
  async network() { try { return await this._json("/network"); } catch { return { segments: [], devices: [], policy: [], flags: [] }; } },
  async netCheck(src, dst) { return this._post("/network/check", { src, dst }); },
  async netMove(name, segment) { await this._post("/network/move", { device: name, segment }); },
  async alerts() { try { return await this._json("/alerts"); } catch { return { alerts: [], counts: { high: 0, medium: 0, info: 0 }, total: 0 }; } },
  async campaign() { try { return await this._json("/campaign"); } catch { return { progress: { total: 0 }, rows: [] }; } },
  async startCampaign(name) { return this._post("/campaign/start", { name }); },
  async certifyUser(u) { await this._post("/campaign/certify", { username: u }); },
  async revokeUser(u) { await this._post("/campaign/revoke", { username: u }); },
  async jit() { try { return await this._json("/jit"); } catch { return { active: [], expired_unswept: [], all: [] }; } },
  async jitGrant(u, g, ttl, reason) { await this._post("/jit/grant", { username: u, group: g, ttl_minutes: ttl, reason }); },
  async jitRevoke(id) { await this._post("/jit/revoke", { grant_id: id }); },
  async jitSweep() { return (await this._post("/jit/sweep", {})).expired; },
  async readiness() { try { return await this._json("/readiness"); } catch { return { rows: [], ready_count: 0, total: 0 }; } },
  async readinessUser(u) { return this._json(`/readiness/${u}`); },
  async cost() { try { return await this._json("/cost"); } catch { return { by_department: [], vendor_by_category: [] }; } },
  async backup() { try { return await this._json("/backup"); } catch { return { records: [], total: 0, stale: 0, protected_pct: 100 }; } },
  async backupRun(resource) { await this._post("/backup/run", { resource }); },
  async usernames() {
    try { return (await this._json("/scim/v2/Users")).Resources.map((u) => u.userName).sort(); }
    catch { return Object.keys(engine.users).sort(); }
  },
};

let backend = demoBackend;

async function detectMode() {
  try {
    const r = await fetch("/scim/v2/Users", { method: "GET" });
    if (r.ok) { backend = liveBackend; }
  } catch { /* stay in demo mode */ }
  const badge = byId("mode");
  badge.textContent = backend.mode === "live" ? "live · FastAPI" : "demo mode";
  badge.className = "modebadge " + (backend.mode === "live" ? "live" : "demo");
  byId("mode-note").textContent = backend.mode === "live"
    ? "Connected to a live LabSuite API."
    : "Running entirely in your browser — a faithful mirror of the Python engine.";
}

/* ------------------------------------------------------------------ *
 * Rendering helpers
 * ------------------------------------------------------------------ */
// Build a CSV string and trigger a browser download (client-side, works offline).
function csvCell(v) { v = String(v ?? ""); return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v; }
function downloadCsv(filename, header, rows) {
  const body = [header, ...rows].map((r) => r.map(csvCell).join(",")).join("\n");
  const url = URL.createObjectURL(new Blob([body], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url; a.download = filename; document.body.appendChild(a); a.click();
  a.remove(); URL.revokeObjectURL(url);
}
const _csvBtn = (id) => `<button class="btn btn-sm ghost" id="${id}" style="float:right">⬇ CSV</button>`;

const layerDot = (name) => ({ okta: "okta", ad: "ad", nas: "nas", pve: "pve" }[name] || "");
const chip = (allowed) => allowed ? '<span class="chip allow">● ALLOW</span>' : '<span class="chip deny">● DENY</span>';

function accessTables(a) {
  const nasRows = Object.keys(a.truenas).sort().map((s) => `<tr><td><i class="dot nas"></i> ${esc(s)}</td><td><span class="tag">${esc(a.truenas[s])}</span></td></tr>`).join("") || `<tr><td class="muted" colspan="2">no share access</td></tr>`;
  const pveRows = Object.keys(a.proxmox).sort().map((p) => `<tr><td><i class="dot pve"></i> ${esc(p)}</td><td><span class="tag">${esc(a.proxmox[p])}</span></td></tr>`).join("") || `<tr><td class="muted" colspan="2">no VM access</td></tr>`;
  return `
    <div class="grid2" style="margin-top:14px">
      <div><div class="label">TrueNAS</div><table>${nasRows}</table></div>
      <div><div class="label">Proxmox</div><table>${pveRows}</table></div>
    </div>`;
}

function explorerCompliance(a) {
  const trainings = a.trainings || {};
  const blocked = a.blocked || {};
  // Conditional access — MFA (Okta Verify) enrollment.
  const mfaChip = a.mfa_enrolled
    ? '<span class="chip allow">● enrolled</span>'
    : `<span class="chip deny">● not enrolled</span> ${_actBtn("mfa", `data-user="${esc(a.username)}"`, "enroll MFA")}`;
  const mfaBlock = `<hr class="divider" /><div class="label">Conditional access</div>
    <div class="tags" style="margin:.3rem 0 .2rem"><span class="k" style="margin-right:.4rem">MFA (Okta Verify)</span>${mfaChip}</div>
    <p class="muted" style="font-size:.8rem;margin:.1rem 0 0">Sensitive shares require MFA regardless of group membership.</p>`;
  const tchips = Object.entries(trainings).map(([t, s]) => {
    const cls = s === "current" ? "allow" : s === "expired" ? "deny" : "";
    return `<span class="chip ${cls}">${esc(t)}: ${esc(s)}</span>`;
  }).join(" ");
  const bchips = Object.entries(blocked).map(([s, m]) =>
    `<div class="muted" style="font-size:.83rem"><span class="chip deny">blocked</span> ${esc(s)} — needs ${m.map(esc).join(", ")}</div>`).join("");
  const compBlock = (Object.keys(trainings).length || Object.keys(blocked).length)
    ? `<hr class="divider" /><div class="label">Compliance / training</div>
       <div class="tags" style="margin:.3rem 0 .4rem">${tchips || '<span class="muted">none</span>'}</div>${bchips}`
    : "";
  return mfaBlock + compBlock;
}

function groupTags(a) {
  const direct = new Set(a.okta_groups);
  return a.ad_effective_groups.map((g) => direct.has(g)
    ? `<span class="tag">${esc(g)}</span>`
    : `<span class="tag nested" title="inherited via AD nesting">${esc(g)} ⤴</span>`).join(" ");
}

/* ------------------------------------------------------------------ *
 * Views
 * ------------------------------------------------------------------ */
async function renderOverview() {
  const s = await backend.stats();
  byId("ov-stats").innerHTML = [
    ["Identities", s.users], ["Active", s.active], ["TrueNAS shares", s.shares], ["Proxmox VMs", s.vms],
  ].map(([l, n]) => `<div class="stat"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");
  await renderActionCenter();
}

const _sevDot = (sev) => `<span class="sev ${sev}" title="${sev}"></span>`;

async function renderActionCenter() {
  const ac = await backend.alerts();
  const c = ac.counts || { high: 0, medium: 0, info: 0 };
  const badges = `
    <span class="chip ${c.high ? "deny" : "allow"}">${c.high} high</span>
    <span class="chip">${c.medium} medium</span>
    <span class="chip">${c.info} info</span>`;
  const body = (ac.alerts || []).length
    ? ac.alerts.map((a) => `<div class="alert-row" data-view="${esc(a.view)}">
        ${_sevDot(a.severity)}
        <span class="cat">${esc(a.category)}</span>
        <span class="ttl">${esc(a.title)}${a.detail ? ` <span class="muted">— ${esc(a.detail)}</span>` : ""}</span>
        <span class="go muted">${esc(a.view)} →</span></div>`).join("")
    : '<div class="chip allow">✓ all clear — nothing needs attention</div>';
  byId("ov-alerts").innerHTML = `
    <div class="card">
      <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.5rem">
        <div class="label">Action center</div><div class="tags">${badges}</div>
      </div>
      <hr class="divider" />
      <div class="alerts-list">${body}</div>
    </div>`;
  $$("#ov-alerts .alert-row").forEach((el) => el.onclick = () => go(el.dataset.view));
}

async function renderDirectory() {
  const rows = await backend.directory();
  byId("dir-table").innerHTML =
    `<tr><th>User</th><th>Name</th><th>Department</th><th>Status</th><th>Shares</th><th>VMs</th></tr>` +
    rows.map((u) => `<tr>
      <td><span class="linkish" data-user="${esc(u.username)}">${esc(u.username)}</span></td>
      <td>${esc(u.display_name)}</td><td>${esc(u.department)}</td>
      <td>${u.active ? '<span class="chip allow">active</span>' : '<span class="chip deny">disabled</span>'}</td>
      <td>${u.shares}</td><td>${u.vms}</td></tr>`).join("");
  $$("#dir-table [data-user]").forEach((el) => el.onclick = () => { byId("ex-user").value = el.dataset.user; go("explorer"); });
}

function fillRoleSelect() {
  byId("ob-dept").innerHTML = D.departments.map((d) => `<option>${d}</option>`).join("");
  byId("ob-role").innerHTML = Object.keys(D.role_blueprints).map((r) => `<option>${r}</option>`).join("");
  const upd = () => byId("ob-roledesc").textContent = D.role_blueprints[byId("ob-role").value].description;
  byId("ob-role").onchange = upd; upd();
}

async function doOnboard() {
  const name = byId("ob-name").value.trim();
  if (!name) return;
  const r = await backend.onboard(name, byId("ob-dept").value, byId("ob-role").value);
  const nas = Object.keys(r.truenas).sort().map((s) => `<tr><td><i class="dot nas"></i> ${esc(s)}</td><td><span class="tag">${esc(r.truenas[s])}</span></td></tr>`).join("") || `<tr><td class="muted" colspan="2">none</td></tr>`;
  const pve = Object.keys(r.proxmox).sort().map((v) => `<tr><td><i class="dot pve"></i> /vms/${esc(v)} (${esc(D.vms[v].name)})</td><td><span class="tag">${esc(r.proxmox[v])}</span></td></tr>`).join("") || `<tr><td class="muted" colspan="2">none</td></tr>`;
  byId("ob-result").innerHTML = `
    <div class="label">Provisioned</div>
    <div class="kv" style="margin:.3rem 0 1rem">
      <span class="k">username</span><b>${esc(r.username)}</b>
      <span class="k">email</span><span>${esc(r.email)}</span>
      <span class="k">temp password</span><span class="mono">${esc(r.temp_password)}</span>
      <span class="k">Okta groups</span><span class="tags">${r.okta_groups.map((g) => `<span class="tag">${esc(g)}</span>`).join("")}</span>
    </div>
    <div class="grid2"><div><div class="label">TrueNAS</div><table>${nas}</table></div><div><div class="label">Proxmox</div><table>${pve}</table></div></div>
    ${deviceCard(r.device, "imaged & shipped (day one)")}
    ${onboardExtras(r)}`;
  await refreshUserSelects();
}

function onboardExtras(r) {
  let html = "";
  if (r.trainings && Object.keys(r.trainings).length) {
    const gated = Object.entries(r.gated || {}).map(([s, m]) =>
      `<div class="muted" style="font-size:.83rem"><span class="chip deny">gated</span> ${esc(s)} — until ${m.map(esc).join(", ")}</div>`).join("");
    html += `<hr class="divider" /><div class="label">Compliance</div>
      <div class="tags" style="margin:.3rem 0">${Object.keys(r.trainings).map((t) => `<span class="tag">${esc(t)} pending</span>`).join("")}</div>${gated}`;
  }
  if (r.saas && r.saas.length) {
    html += `<hr class="divider" /><div class="label">SaaS provisioned</div>
      <div class="tags" style="margin-top:.3rem">${r.saas.map((a) => `<span class="tag">${esc(a)}</span>`).join("")}</div>`;
  }
  return html;
}

function deviceCard(dev, tagline) {
  if (!dev) return "";
  const img = D.image_catalog[dev.image] || {};
  return `<hr class="divider" />
    <div class="label">Device — ${esc(tagline)}</div>
    <div style="display:flex;align-items:center;gap:.6rem;margin-top:.35rem;flex-wrap:wrap">
      <b>${esc(dev.model)}</b>
      <span class="tag">${esc(dev.image)}</span>
      <span class="chip">${esc(dev.platform)}</span>
      ${dev.status === "wipe & return" ? '<span class="chip deny">wipe &amp; return</span>' : '<span class="chip allow">assigned</span>'}
    </div>
    <div class="muted" style="font-size:.85rem;margin-top:.35rem">${esc(img.security_summary || "")} · MDM ${esc(img.mdm || "")} · asset ${esc(dev.asset_tag)}</div>`;
}

async function doOffboard() {
  const u = byId("off-user").value;
  const r = await backend.offboard(u);
  byId("off-result").innerHTML = `
    <div class="label">Result for ${esc(u)}</div>
    <div class="kv" style="margin:.3rem 0 1rem">
      <span class="k">removed from</span><span class="tags">${(r.removed_groups.length ? r.removed_groups : ["(none)"]).map((g) => `<span class="tag">${esc(g)}</span>`).join("")}</span>
    </div>
    <div style="font-size:1.05rem">${r.clean
      ? '<span class="chip allow">✓ CLEAN — zero residual access</span>'
      : '<span class="chip deny">✗ RESIDUAL ACCESS REMAINS</span>'}</div>
    ${r.clean ? '<p class="muted" style="margin:.8rem 0 0;font-size:.88rem">Re-resolved across TrueNAS + Proxmox after deprovisioning — nothing remains.</p>'
      : `<pre>${esc(JSON.stringify({ truenas: r.residual_truenas, proxmox: r.residual_proxmox }, null, 2))}</pre>`}
    ${deviceCard(r.device, "flagged for return")}
    ${r.saas_revoked && r.saas_revoked.length ? `<hr class="divider" /><div class="label">SaaS seats reclaimed</div><div class="tags" style="margin-top:.3rem">${r.saas_revoked.map((a) => `<span class="tag">${esc(a)}</span>`).join("")}</div>` : ""}`;
  await refreshUserSelects();
}

async function renderDevices() {
  const rows = (await backend.devices()).slice().sort((a, b) => a.asset_tag.localeCompare(b.asset_tag));
  const body = rows.map((d) => {
    const img = D.image_catalog[d.image] || {};
    const status = d.status === "wipe & return"
      ? '<span class="chip deny">wipe &amp; return</span>' : '<span class="chip allow">assigned</span>';
    return `<tr><td>${esc(d.asset_tag)}</td><td>${esc(d.model)}</td>
      <td><span class="tag">${esc(d.image)}</span></td><td>${esc(d.platform)}</td>
      <td class="muted">${esc(img.security_summary || "")}</td>
      <td>${esc(d.assignee || "—")}</td><td>${status}</td></tr>`;
  }).join("");
  byId("dev-table").innerHTML =
    `<tr><th>Asset</th><th>Model</th><th>Image</th><th>OS</th><th>Baseline</th><th>Assignee</th><th>Status</th></tr>${body}`;
}

async function renderExplorer() {
  const u = byId("ex-user").value;
  if (!u) return;
  const a = await backend.resolve(u);
  byId("ex-report").innerHTML = `
    <div class="card">
      <div class="kv">
        <span class="k">status</span><span>${a.active ? '<span class="chip allow">active</span>' : '<span class="chip deny">disabled</span>'}</span>
        <span class="k">Okta groups</span><span class="tags">${a.okta_groups.map((g) => `<span class="tag">${esc(g)}</span>`).join(" ") || '<span class="muted">none</span>'}</span>
        <span class="k">AD effective</span><span class="tags">${groupTags(a) || '<span class="muted">none</span>'}</span>
      </div>
      ${accessTables(a)}
      ${explorerCompliance(a)}
      <p class="muted" style="font-size:.8rem;margin:.9rem 0 0">Dashed tags ⤴ are inherited through AD nesting. Blocked shares need current training or MFA.</p>
    </div>`;
  $$("#ex-report [data-act='mfa']").forEach((b) => b.onclick = async () => {
    await backend.enrollMfa(b.dataset.user); renderExplorer();
  });
  fillCheckControls();
}

function fillCheckControls() {
  const sys = byId("ex-sys").value;
  if (sys === "truenas") {
    byId("ex-res").innerHTML = Object.keys(D.shares).map((s) => `<option>${s}</option>`).join("");
    byId("ex-act").innerHTML = ["read", "modify", "full"].map((a) => `<option>${a}</option>`).join("");
  } else {
    byId("ex-res").innerHTML = Object.keys(D.vms).map((v) => `<option value="${v}">/vms/${v} — ${esc(D.vms[v].name)}</option>`).join("");
    byId("ex-act").innerHTML = Object.keys(D.proxmox_privileges).map((p) => `<option>${p}</option>`).join("");
  }
}

async function doCheck() {
  const u = byId("ex-user").value, sys = byId("ex-sys").value, res = byId("ex-res").value, act = byId("ex-act").value;
  let d;
  if (backend.mode === "live") {
    // resolve-based decision so we don't need a dedicated live endpoint
    const a = await backend.resolve(u);
    if (sys === "truenas") { const order = { read: 1, modify: 2, full: 3 }; const have = order[a.truenas[res]] || 0;
      d = { allowed: have >= order[act], granted: a.truenas[res] || "none", required: act, via: [], reason: have >= order[act] ? "granted" : "insufficient" }; }
    else { const path = D.vms[res].path; const roleOrder = D.proxmox_roles; const nameToInt = Object.fromEntries(Object.entries(roleOrder).map(([k, v]) => [v, +k]));
      const have = nameToInt[a.proxmox[path]] || 0; const need = D.proxmox_privileges[act];
      d = { allowed: have >= need, granted: a.proxmox[path] || "NoAccess", required: roleOrder[need], via: [], reason: have >= need ? "granted" : "insufficient" }; }
  } else {
    d = sys === "truenas" ? await backend.checkTruenas(u, res, act) : await backend.checkProxmox(u, +res, act);
  }
  byId("ex-decision").innerHTML = `${chip(d.allowed)}
    <span style="margin-left:.6rem">${esc(u)} → ${esc(sys)}:${esc(res)} <span class="muted">[${esc(act)}]</span></span>
    <div class="muted" style="font-size:.85rem;margin-top:.4rem">granted <b>${esc(d.granted)}</b>, required <b>${esc(d.required)}</b> — ${esc(d.reason)}${d.via && d.via.length ? " (" + d.via.map(esc).join(", ") + ")" : ""}</div>`;
}

async function renderReview() {
  const r = await backend.review();
  byId("rv-export").onclick = async () => {
    const rows = [];
    for (const u of Object.keys(r.entitlements).sort()) {
      const a = await backend.resolve(u);
      rows.push([u, a.active ? "active" : "disabled", a.mfa_enrolled ? "yes" : "no",
        Object.keys(a.truenas).length, Object.keys(a.proxmox).length,
        Object.entries(a.truenas).map(([s, l]) => `${s}:${l}`).join("; "), Object.keys(a.blocked || {}).join("; ")]);
    }
    downloadCsv("labsuite-access.csv", ["username", "status", "mfa", "shares", "vms", "share_access", "blocked"], rows);
  };
  byId("rv-flags").innerHTML = r.flags.length
    ? r.flags.map((f) => `<div class="flag"><span class="bang">!</span> ${esc(f)}</div>`).join("")
    : '<div class="chip allow">✓ estate is clean</div>';
  const rows = Object.keys(r.entitlements).sort();
  byId("rv-table").innerHTML = `<tr><th>User</th><th>Status</th><th>Shares</th><th>VMs</th></tr>` +
    rows.map((u) => { const e = r.entitlements[u]; return `<tr><td>${esc(u)}</td>
      <td>${e.active ? '<span class="chip allow">active</span>' : '<span class="chip deny">disabled</span>'}</td>
      <td>${Object.keys(e.truenas).length}</td><td>${Object.keys(e.proxmox).length}</td></tr>`; }).join("");
  await renderCampaign();
}

async function renderCampaign() {
  const st = await backend.campaign();
  const p = st.progress || { total: 0 };
  if (!p.total) {
    byId("rv-campaign").innerHTML = `<div class="card">
      <div class="label" style="margin-bottom:6px">Attestation campaign</div>
      <p class="muted" style="font-size:.88rem;margin:0 0 .8rem">Turn this report into a certify/revoke campaign — reviewers attest each user's access and progress is tracked.</p>
      <button class="btn btn-sm" id="cmp-start">Start review campaign</button></div>`;
    byId("cmp-start").onclick = async () => { await backend.startCampaign("Access review"); renderReview(); };
    return;
  }
  const bar = `<div class="progress"><span style="width:${p.completion_pct}%"></span></div>`;
  const body = (st.rows || []).map((r) => {
    const chipCls = r.status === "certified" ? "allow" : r.status === "revoked" ? "deny" : "";
    const actions = r.status === "pending"
      ? `${_actBtn("certify", `data-u="${esc(r.username)}"`, "certify")} ${_actBtn("revoke", `data-u="${esc(r.username)}"`, "revoke")}`
      : `<span class="chip ${chipCls}">${esc(r.status)}</span>`;
    return `<tr><td>${esc(r.username)}</td><td>${r.shares} shares, ${r.vms} VMs</td><td>${actions}</td></tr>`;
  }).join("");
  byId("rv-campaign").innerHTML = `<div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem">
      <div class="label">Attestation campaign — ${esc(p.name)}</div>
      <div class="tags"><span class="chip allow">${p.certified} certified</span><span class="chip deny">${p.revoked} revoked</span><span class="chip">${p.pending} pending</span></div>
    </div>
    ${bar}
    <div class="muted" style="font-size:.82rem;margin:.2rem 0 .6rem">${p.completion_pct}% reviewed</div>
    <table><tr><th>User</th><th>Access</th><th></th></tr>${body}</table></div>`;
  $$("#rv-campaign button.act").forEach((b) => b.onclick = async () => {
    if (b.dataset.act === "certify") await backend.certifyUser(b.dataset.u);
    else await backend.revokeUser(b.dataset.u);
    renderReview();
  });
}

async function renderAudit() {
  const events = await backend.audit();
  byId("audit-export").onclick = () => downloadCsv("labsuite-audit.csv",
    ["ts", "actor", "action", "target", "system", "outcome", "detail"],
    events.slice().reverse().map((e) => [Math.round(e.ts || 0), e.actor, e.action, e.target, e.system, e.outcome, e.detail || ""]));
  byId("audit-list").innerHTML = events.length ? events.map((e) => `
    <div class="audit-row">
      <span class="sys">${esc(e.system)}</span>
      <span class="act">${esc(e.action)}</span>
      <span class="det">${esc(e.target)} <span class="muted">${esc(e.detail || "")}</span></span>
      <span>${e.outcome === "denied" || e.outcome === "error" ? '<span class="chip deny">' + esc(e.outcome) + '</span>' : '<span class="chip allow">' + esc(e.outcome) + '</span>'}</span>
    </div>`).join("") : '<div class="muted">No events yet — try onboarding or a decision, then return here.</div>';
}

async function renderRequests() {
  const [reqs, users] = await Promise.all([backend.requestsList(), backend.usernames()]);
  const groups = (D.all_groups || []).filter((g) => g !== "Everyone");
  const statusChip = (s) => s === "approved" ? '<span class="chip allow">approved</span>'
    : s === "denied" ? '<span class="chip deny">denied</span>' : '<span class="chip">pending</span>';
  const rows = reqs.slice().sort((a, b) => b.id.localeCompare(a.id)).map((r) => {
    const actions = r.status === "pending"
      ? `${_actBtn("approve", `data-id="${esc(r.id)}"`, "approve")} ${_actBtn("deny", `data-id="${esc(r.id)}"`, "deny")}`
      : `<span class="muted">${esc(r.decided_by || "")}</span>`;
    return `<tr><td>${esc(r.id)}</td><td>${esc(r.requester)}</td><td><span class="tag">${esc(r.group)}</span></td>
      <td class="muted">${esc(r.justification || "")}</td><td>${statusChip(r.status)}</td><td>${actions}</td></tr>`;
  }).join("") || `<tr><td colspan="6" class="muted">No requests yet — file one below.</td></tr>`;
  byId("req-table").innerHTML =
    `<tr><th>ID</th><th>Requester</th><th>Group</th><th>Justification</th><th>Status</th><th></th></tr>${rows}`;
  byId("req-user").innerHTML = users.map((u) => `<option>${esc(u)}</option>`).join("");
  byId("req-group").innerHTML = groups.map((g) => `<option>${esc(g)}</option>`).join("");
  $$("#req-table button.act").forEach((b) => b.onclick = async () => {
    if (b.dataset.act === "approve") await backend.approveRequest(b.dataset.id);
    else await backend.denyRequest(b.dataset.id, "");
    renderRequests();
  });
  byId("req-submit").onclick = async () => {
    await backend.createRequest(byId("req-user").value, byId("req-group").value, byId("req-why").value);
    byId("req-why").value = "";
    renderRequests();
  };
}

async function renderCompliance() {
  const records = await backend.complianceRecords();
  const trainings = ["Data-Handling", "Biosafety", "Chemical-Safety", "IACUC"];
  const chip = (s) => s === "current" ? '<span class="chip allow">current</span>'
    : s === "expired" ? '<span class="chip deny">expired</span>' : '<span class="chip">missing</span>';
  const rows = Object.keys(records).sort().map((u) => {
    const cells = trainings.map((t) => {
      if (!(t in records[u])) return "<td></td>";
      const s = records[u][t];
      const act = s === "current" ? "expire" : "complete";
      const label = s === "current" ? "lapse" : "grant";
      return `<td>${chip(s)} <button class="btn btn-sm" data-u="${esc(u)}" data-t="${esc(t)}" data-act="${act}" style="padding:.15rem .5rem;font-size:.72rem;margin-left:.3rem">${label}</button></td>`;
    }).join("");
    return `<tr><td><b>${esc(u)}</b></td>${cells}</tr>`;
  }).join("");
  byId("comp-table").innerHTML =
    `<tr><th>User</th>${trainings.map((t) => `<th>${esc(t)}</th>`).join("")}</tr>${rows}`;
  $$("#comp-table button").forEach((b) => b.onclick = async () => {
    if (b.dataset.act === "expire") await backend.expireTraining(b.dataset.u, b.dataset.t);
    else await backend.completeTraining(b.dataset.u, b.dataset.t);
    renderCompliance();
  });
}

async function renderSaas() {
  const data = await backend.saas();
  byId("saas-export").onclick = () => downloadCsv("labsuite-saas.csv",
    ["app", "cost_per_seat_monthly", "seats", "monthly_total", "annual_total", "assignees"],
    data.apps.slice().sort((a, b) => a.name.localeCompare(b.name)).map((a) => {
      const seats = a.assignees.length;
      return [a.name, a.monthly_cost_per_seat.toFixed(2), seats, (a.monthly_cost_per_seat * seats).toFixed(2),
        (a.monthly_cost_per_seat * seats * 12).toFixed(2), a.assignees.slice().sort().join("; ")];
    }));
  const annual = (data.annual_cost != null) ? data.annual_cost : data.monthly_spend * 12;
  byId("saas-stats").innerHTML = [
    ["$" + Math.round(data.monthly_spend).toLocaleString(), "Monthly spend"],
    ["$" + Math.round(annual).toLocaleString(), "Annual"],
    [String(data.apps.length), "Apps"],
    [String(data.apps.reduce((t, a) => t + a.assignees.length, 0)), "Seats"],
  ].map(([n, l]) => `<div class="stat"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");
  const apps = data.apps.slice().sort((a, b) => a.name.localeCompare(b.name));
  const rows = apps.map((a) => {
    const monthly = a.monthly_cost_per_seat * a.assignees.length;
    const chips = a.assignees.map((u) =>
      `<span class="tag">${esc(u)} <button class="seat-x" data-app="${esc(a.name)}" data-user="${esc(u)}" title="revoke seat" style="border:0;background:none;cursor:pointer;color:var(--no);font-weight:700">×</button></span>`).join(" ");
    return `<tr><td><b>${esc(a.name)}</b></td><td>${a.assignees.length}</td>
      <td>$${a.monthly_cost_per_seat.toFixed(1)}</td><td>$${monthly.toFixed(0)}/mo</td>
      <td><div class="tags">${chips || '<span class="muted">—</span>'}</div></td></tr>`;
  }).join("");
  const userOpts = (await backend.usernames()).map((u) => `<option>${esc(u)}</option>`).join("");
  const appOpts = apps.map((a) => `<option>${esc(a.name)}</option>`).join("");
  byId("saas-table").innerHTML = `<tr><th>App</th><th>Seats</th><th>$/seat</th><th>Cost</th><th>Assignees</th></tr>${rows}
    <tr><td colspan="5" style="padding-top:12px">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <span class="label" style="margin:0">Add a seat:</span>
        <select id="saas-user" style="width:auto">${userOpts}</select>
        <select id="saas-app" style="width:auto">${appOpts}</select>
        <button class="btn btn-sm" id="saas-grant">grant seat</button>
      </div></td></tr>`;
  $$("#saas-table .seat-x").forEach((b) => b.onclick = async () => {
    await backend.revokeSaasSeat(b.dataset.user, b.dataset.app); renderSaas();
  });
  byId("saas-grant").onclick = async () => {
    await backend.grantSaasSeat(byId("saas-user").value, byId("saas-app").value); renderSaas();
  };
}

async function renderCost() {
  const c = await backend.cost();
  const usd = (n) => "$" + Math.round(n).toLocaleString();
  byId("cost-stats").innerHTML = [
    [usd(c.saas_monthly_total), "SaaS / month"],
    [usd(c.saas_annual_total), "SaaS / year"],
    [usd(c.vendor_annual_total), "Vendors / year"],
    [usd(c.orphaned_monthly) + "/mo", "Orphaned seats"],
  ].map(([n, l]) => `<div class="stat"><div class="n">${esc(n)}</div><div class="l">${esc(l)}</div></div>`).join("");
  const maxSpend = Math.max(1, ...c.by_department.map((d) => Math.max(d.monthly, d.budget || 0)));
  const deptRows = c.by_department.map((d) => {
    const w = Math.round((100 * d.monthly) / maxSpend);
    const bw = d.budget ? Math.round((100 * d.budget) / maxSpend) : 0;
    const barCls = d.over_budget ? "over" : "";
    return `<tr><td>${esc(d.department)}</td>
      <td style="min-width:160px"><div class="budgetbar"><span class="${barCls}" style="width:${w}%"></span>${d.budget ? `<i style="left:${bw}%"></i>` : ""}</div></td>
      <td>$${d.monthly.toLocaleString()}</td>
      <td class="muted">${d.budget != null ? "$" + d.budget.toLocaleString() : "—"}</td>
      <td>${d.over_budget ? '<span class="chip deny">over</span>' : '<span class="chip allow">ok</span>'}</td></tr>`;
  }).join("");
  const catRows = c.vendor_by_category.map((v) =>
    `<tr><td>${esc(v.category)}</td><td>$${v.annual.toLocaleString()}/yr</td></tr>`).join("");
  byId("cost-body").innerHTML = `
    <div class="grid2">
      <div class="card"><div class="label" style="margin-bottom:6px">SaaS spend by department (monthly vs budget)</div>
        <table><tr><th>Dept</th><th></th><th>Spend</th><th>Budget</th><th></th></tr>${deptRows}</table>
        <p class="muted" style="font-size:.8rem;margin:.6rem 0 0">The tick marks each department's monthly budget; a filled red bar is over.</p></div>
      <div class="card"><div class="label" style="margin-bottom:6px">Vendor spend by category (annual)</div>
        <table><tr><th>Category</th><th>Annual</th></tr>${catRows}</table>
        ${c.orphaned_monthly ? `<hr class="divider" /><p class="muted" style="font-size:.85rem;margin:0">Reclaiming orphaned SaaS seats would save <b>${usd(c.orphaned_annual)}/yr</b>.</p>` : ""}</div>
    </div>`;
}

const _actBtn = (act, attrs, label) =>
  `<button class="btn btn-sm act" data-act="${act}" ${attrs} style="padding:.15rem .55rem;font-size:.72rem">${label}</button>`;

async function renderOps() {
  const [equipment, inventory, vendors, safety] = await Promise.all(
    [backend.assets(), backend.inventory(), backend.vendors(), backend.safety()]);
  const maint = (d) => d < 0 ? `<span class="chip deny">overdue ${-d}d</span>`
    : d <= 14 ? `<span class="chip">due ${d}d</span>` : `${d}d`;
  const eq = equipment.map((e) => `<tr><td>${esc(e.asset_tag)}</td><td>${esc(e.name)}</td><td>${maint(e.maintenance_in_days)}</td>
    <td>${_actBtn("maint", `data-tag="${esc(e.asset_tag)}"`, "mark serviced")}</td></tr>`).join("");
  const inv = inventory.map((i) => `<tr><td>${esc(i.name)}</td><td>${i.qty} ${esc(i.unit)}</td>
    <td>${i.low ? '<span class="chip deny">low</span>' : '<span class="chip allow">ok</span>'}</td>
    <td>${i.low ? _actBtn("reorder", `data-sku="${esc(i.sku)}"`, "reorder") : ""}</td></tr>`).join("");
  const ven = vendors.slice().sort((a, b) => a.renewal_in_days - b.renewal_in_days).map((v) => `<tr><td><b>${esc(v.name)}</b></td>
    <td>${v.renewal_in_days <= 60 ? `<span class="chip">renews ${v.renewal_in_days}d</span>` : v.renewal_in_days + "d"}</td>
    <td>$${v.annual_cost.toLocaleString()}/yr</td>
    <td>${v.renewal_in_days <= 60 ? _actBtn("renew", `data-name="${esc(v.name)}"`, "renew") : ""}</td></tr>`).join("");
  const saf = safety.map((s) => `<tr><td>${s.status === "open" ? '<span class="chip deny">OPEN</span>' : '<span class="chip allow">pass</span>'}</td>
    <td>${esc(s.area)}</td><td>${esc(s.check)}<span class="muted">${s.note ? " — " + esc(s.note) : ""}</span></td>
    <td>${s.status === "open" ? _actBtn("resolve", `data-area="${esc(s.area)}" data-check="${esc(s.check)}"`, "resolve") : ""}</td></tr>`).join("");
  byId("ops-body").innerHTML = `
    <div class="grid2">
      <div class="card"><div class="label" style="margin-bottom:6px">Equipment &amp; maintenance</div><table><tr><th>Asset</th><th>Name</th><th>Maint</th><th></th></tr>${eq}</table></div>
      <div class="card"><div class="label" style="margin-bottom:6px">Inventory</div><table><tr><th>Item</th><th>Qty</th><th>Stock</th><th></th></tr>${inv}</table></div>
    </div>
    <div class="grid2" style="margin-top:16px">
      <div class="card"><div class="label" style="margin-bottom:6px">Vendors &amp; renewals</div><table><tr><th>Vendor</th><th>Renewal</th><th>Annual</th><th></th></tr>${ven}</table></div>
      <div class="card"><div class="label" style="margin-bottom:6px">Facility safety</div><table><tr><th></th><th>Area</th><th>Check</th><th></th></tr>${saf}</table></div>
    </div>`;
  $$("#ops-body button.act").forEach((b) => b.onclick = async () => {
    const a = b.dataset.act;
    if (a === "maint") await backend.completeMaintenance(b.dataset.tag);
    else if (a === "reorder") await backend.reorder(b.dataset.sku);
    else if (a === "renew") await backend.renewVendor(b.dataset.name);
    else if (a === "resolve") await backend.resolveSafety(b.dataset.area, b.dataset.check);
    renderOps();
  });
}

const _trustChip = (t) => {
  const cls = t === "high" ? "allow" : t === "none" || t === "low" ? "deny" : "";
  return `<span class="chip ${cls}">${esc(t)}</span>`;
};

async function renderNetwork() {
  const s = await backend.network();
  const segs = s.segments || [];
  const segRows = segs.map((seg) => `<tr>
    <td><span class="tag">VLAN ${seg.vlan_id}</span></td><td><b>${esc(seg.name)}</b></td>
    <td class="mono" style="font-size:.82rem">${esc(seg.cidr)}</td>
    <td>${_trustChip(seg.trust)}</td>
    <td>${seg.internet ? '<span class="chip">internet</span>' : '<span class="chip deny">no egress</span>'}</td>
    <td class="muted">${esc(seg.purpose || "")}</td></tr>`).join("");
  const devRows = (s.devices || []).map((d) => {
    const misplaced = (s.flags || []).some((f) => f.startsWith(d.name + " "));
    return `<tr><td>${esc(d.name)}</td><td><span class="tag">${esc(d.kind)}</span></td>
      <td>${misplaced ? '<span class="chip deny">' + esc(d.segment) + '</span>' : '<span class="tag">' + esc(d.segment) + '</span>'}</td>
      <td class="mono" style="font-size:.82rem">${esc(d.ip || "")}</td><td class="muted">${esc(d.owner || "—")}</td>
      <td>${misplaced ? _actBtn("quarantine", `data-dev="${esc(d.name)}"`, "move → IoT") : ""}</td></tr>`;
  }).join("");
  // segmentation matrix
  const names = segs.map((x) => x.name);
  const head = `<tr><th>from ╲ to</th>${names.map((n) => `<th>${esc(n)}</th>`).join("")}</tr>`;
  const matrix = names.map((src) => `<tr><td><b>${esc(src)}</b></td>${names.map((dst) => {
    const r = engineReach(s, src, dst);
    return `<td class="center">${r ? '<span class="chip allow" style="padding:.1rem .4rem">✓</span>' : '<span class="muted">·</span>'}</td>`;
  }).join("")}</tr>`).join("");
  const flags = (s.flags || []).length
    ? (s.flags).map((f) => `<div class="flag"><span class="bang">!</span> ${esc(f)}</div>`).join("")
    : '<div class="chip allow">✓ every device is on its expected segment</div>';
  byId("net-body").innerHTML = `
    <div class="card" style="margin-bottom:16px"><div class="label" style="margin-bottom:8px">Segmentation flags</div><div class="flags">${flags}</div></div>
    <div class="grid2">
      <div class="card"><div class="label" style="margin-bottom:6px">VLAN segments</div><table><tr><th>VLAN</th><th>Name</th><th>Subnet</th><th>Trust</th><th>Egress</th><th>Purpose</th></tr>${segRows}</table></div>
      <div class="card"><div class="label" style="margin-bottom:6px">East-west policy (default deny)</div><table>${head}${matrix}</table>
        <p class="muted" style="font-size:.8rem;margin:.7rem 0 0">✓ = the firewall permits traffic initiated from the row VLAN toward the column VLAN.</p></div>
    </div>
    <div class="card" style="margin-top:16px"><div class="label" style="margin-bottom:6px">Devices</div>
      <table><tr><th>Device</th><th>Kind</th><th>Segment</th><th>IP</th><th>Owner</th><th></th></tr>${devRows}</table></div>
    <div class="card" style="margin-top:16px">
      <div class="label" style="margin-bottom:6px">Reachability check</div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">
        <div class="field" style="margin:0"><label>From</label><select id="net-src">${names.map((n) => `<option>${esc(n)}</option>`).join("")}</select></div>
        <div class="field" style="margin:0"><label>To</label><select id="net-dst">${names.map((n) => `<option>${esc(n)}</option>`).join("")}</select></div>
        <button class="btn btn-sm" id="net-check">Check</button>
      </div>
      <div id="net-decision" style="margin-top:14px"></div>
    </div>`;
  $$("#net-body button.act").forEach((b) => b.onclick = async () => {
    await backend.netMove(b.dataset.dev, "IoT"); renderNetwork();
  });
  byId("net-check").onclick = async () => {
    const src = byId("net-src").value, dst = byId("net-dst").value;
    const d = await backend.netCheck(src, dst);
    byId("net-decision").innerHTML = `${chip(d.allowed)}
      <span style="margin-left:.6rem">${esc(src)} → ${esc(dst)}</span>
      <div class="muted" style="font-size:.85rem;margin-top:.4rem">${esc(d.reason)}</div>`;
  };
}

async function renderBackup() {
  const h = await backend.backup();
  byId("bk-stats").innerHTML = [
    [h.protected_pct + "%", "Protected"],
    [String(h.total), "Resources"],
    [String(h.stale), "Stale"],
  ].map(([n, l]) => `<div class="stat"><div class="n">${esc(n)}</div><div class="l">${esc(l)}</div></div>`).join("");
  const rows = (h.records || []).map((r) => {
    const stale = r.status === "stale";
    return `<tr>
      <td>${esc(r.resource)}</td><td><span class="tag">${esc(r.kind)}</span></td>
      <td>${esc(r.schedule)}</td><td>${r.last_backup_hours}h ago</td>
      <td class="muted">${esc(r.target)}</td>
      <td>${stale ? '<span class="chip deny">stale</span>' : '<span class="chip allow">ok</span>'}</td>
      <td>${stale ? _actBtn("backupnow", `data-res="${esc(r.resource)}"`, "back up now") : ""}</td></tr>`;
  }).join("");
  byId("bk-body").innerHTML = `<div class="card">
    <div class="label" style="margin-bottom:6px">Datasets &amp; VMs</div>
    <table><tr><th>Resource</th><th>Type</th><th>Schedule</th><th>Last backup</th><th>Target</th><th>Status</th><th></th></tr>${rows}</table>
    <p class="muted" style="font-size:.8rem;margin:.7rem 0 0">A backup is “stale” once it is older than its schedule allows (hourly&gt;6h, daily&gt;36h, weekly&gt;192h).</p></div>`;
  $$("#bk-body button.act").forEach((b) => b.onclick = async () => { await backend.backupRun(b.dataset.res); renderBackup(); });
}

// Recompute a matrix cell from the summary's policy (works for demo + live).
function engineReach(summary, src, dst) {
  if (src === dst) return true;
  return (summary.policy || []).some((p) => p[0] === src && p[1] === dst);
}

async function renderJit() {
  const st = await backend.jit();
  const groups = D.elevated_groups || { "Domain-Admins": "Full admin" };
  const users = await backend.usernames();
  const activeRows = (st.active || []).map((g) => `<tr>
    <td>${esc(g.id)}</td><td>${esc(g.username)}</td><td><span class="tag">${esc(g.group)}</span></td>
    <td><span class="chip allow">${g.remaining_minutes}m left</span></td>
    <td class="muted">${esc(g.reason || "")}</td>
    <td>${_actBtn("jitrevoke", `data-id="${esc(g.id)}"`, "revoke")}</td></tr>`).join("");
  const expiredRows = (st.expired_unswept || []).map((g) => `<tr>
    <td>${esc(g.id)}</td><td>${esc(g.username)}</td><td><span class="tag">${esc(g.group)}</span></td>
    <td><span class="chip deny">expired</span></td><td class="muted">${esc(g.reason || "")}</td><td></td></tr>`).join("");
  byId("jit-body").innerHTML = `
    <div class="card" style="margin-bottom:16px">
      <div class="label" style="margin-bottom:8px">Grant break-glass access</div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">
        <div class="field" style="margin:0"><label>User</label><select id="jit-user">${users.map((u) => `<option>${esc(u)}</option>`).join("")}</select></div>
        <div class="field" style="margin:0"><label>Elevated group</label><select id="jit-group">${Object.keys(groups).map((g) => `<option>${esc(g)}</option>`).join("")}</select></div>
        <div class="field" style="margin:0"><label>Duration</label><select id="jit-ttl"><option value="15">15 min</option><option value="60" selected>1 hour</option><option value="240">4 hours</option></select></div>
        <div class="field" style="margin:0;flex:1;min-width:160px"><label>Reason</label><input id="jit-reason" placeholder="incident / change ticket" /></div>
        <button class="btn btn-sm" id="jit-grant">Elevate</button>
      </div>
      <p class="muted" style="font-size:.8rem;margin:.7rem 0 0" id="jit-groupdesc"></p>
    </div>
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem">
        <div class="label">Active elevations</div>
        ${(st.expired_unswept || []).length ? `<button class="btn btn-sm ghost" id="jit-sweep">Sweep ${st.expired_unswept.length} expired</button>` : ""}
      </div>
      <table style="margin-top:8px"><tr><th>Grant</th><th>User</th><th>Group</th><th>Status</th><th>Reason</th><th></th></tr>
        ${activeRows || expiredRows ? activeRows + expiredRows : '<tr><td class="muted" colspan="6">no active elevations</td></tr>'}</table>
      <p class="muted" style="font-size:.8rem;margin:.7rem 0 0">Grants auto-expire at the end of their window; “sweep” reclaims any that have lapsed.</p>
    </div>`;
  const setDesc = () => byId("jit-groupdesc").textContent = groups[byId("jit-group").value] || "";
  setDesc(); byId("jit-group").onchange = setDesc;
  byId("jit-grant").onclick = async () => {
    await backend.jitGrant(byId("jit-user").value, byId("jit-group").value, +byId("jit-ttl").value, byId("jit-reason").value);
    renderJit();
  };
  if (byId("jit-sweep")) byId("jit-sweep").onclick = async () => { await backend.jitSweep(); renderJit(); };
  $$("#jit-body button.act").forEach((b) => b.onclick = async () => { await backend.jitRevoke(b.dataset.id); renderJit(); });
}

async function renderReadiness() {
  const s = await backend.readiness();
  const sel = byId("rd-user");
  const cur = sel.value;
  sel.innerHTML = (s.rows || []).map((r) => `<option>${esc(r.username)}</option>`).join("");
  if (s.rows.some((r) => r.username === cur)) sel.value = cur;
  const summary = (s.rows || []).map((r) => `<tr data-user="${esc(r.username)}" class="linkish-row">
    <td>${esc(r.username)}</td><td>${esc(r.display_name)}</td>
    <td>${r.ready ? '<span class="chip allow">✓ day-one ready</span>' : `<span class="chip deny">${r.completion_pct}%</span>`}</td></tr>`).join("");
  byId("rd-summary").innerHTML = `<div class="label" style="margin-bottom:6px">Readiness — ${s.ready_count}/${s.total} day-one ready</div>
    <table><tr><th>User</th><th>Name</th><th>Status</th></tr>${summary}</table>`;
  await renderChecklist();
  $$("#rd-summary tr[data-user]").forEach((tr) => tr.onclick = () => { byId("rd-user").value = tr.dataset.user; renderChecklist(); });
  byId("rd-user").onchange = renderChecklist;
}

async function renderChecklist() {
  const u = byId("rd-user").value;
  if (!u) { byId("rd-checklist").innerHTML = ""; return; }
  const c = await backend.readinessUser(u);
  const rows = c.items.map((i) => `<div class="check-row">
    <span class="check ${i.done ? "on" : "off"}">${i.done ? "✓" : "○"}</span>
    <span>${esc(i.item)}${i.required ? "" : ' <span class="muted" style="font-size:.75rem">(optional)</span>'}</span>
    <span class="muted" style="font-size:.82rem">${esc(i.detail)}</span></div>`).join("");
  const badge = c.ready ? '<span class="chip allow">✓ day-one ready</span>' : `<span class="chip deny">${c.completion_pct}% ready</span>`;
  byId("rd-checklist").innerHTML = `<div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem">
      <div class="label">${esc(c.display_name)} · ${esc(u)}</div>${badge}</div>
    <div class="progress"><span style="width:${c.completion_pct}%"></span></div>
    <div class="checklist" style="margin-top:.6rem">${rows}</div></div>`;
}

function renderArchitecture() {
  byId("arch").innerHTML = `
    <div style="display:grid;gap:12px;max-width:560px">
      ${[["1", "okta", "Okta — identity / login", "Source of truth; authenticates and issues a session token."],
         ["2", "ad", "Active Directory", "Synced from Okta; nested groups resolved by transitive closure."]]
        .map(([i, c, t, d]) => `<div class="card" style="display:grid;grid-template-columns:auto 1fr;gap:12px;align-items:center;padding:14px 16px">
          <span class="stat" style="width:34px;height:34px;display:grid;place-items:center;padding:0;border-radius:50%;box-shadow:none"><i class="dot ${c}"></i></span>
          <div><b>${t}</b><div class="muted" style="font-size:.85rem">${d}</div></div></div>`).join('<div class="center muted">↓</div>')}
      <div class="center muted">↓ effective groups drive every ACL ↓</div>
      <div class="grid2">
        <div class="card" style="padding:14px 16px"><b><i class="dot nas"></i> TrueNAS</b><div class="muted" style="font-size:.85rem">Share ACLs: group → read/modify/full.</div></div>
        <div class="card" style="padding:14px 16px"><b><i class="dot pve"></i> Proxmox</b><div class="muted" style="font-size:.85rem">(path, group) → PVE role, inherited.</div></div>
      </div>
    </div>`;
}

async function doLogin() {
  const r = await backend.login(byId("lg-user").value.trim(), byId("lg-pass").value);
  byId("lg-result").innerHTML = r.ok
    ? `<div class="chip allow">✓ authenticated</div>
       <div class="label" style="margin-top:1rem">session token</div>
       <pre style="white-space:pre-wrap;word-break:break-all">${esc(r.token)}</pre>
       <div class="label">claims</div><pre>${esc(JSON.stringify(r.claims, null, 2))}</pre>`
    : `<div class="chip deny">✗ authentication failed</div>
       <p class="muted" style="font-size:.85rem;margin-top:.7rem">Wrong password, or the account is deactivated (try offboarding a user, then signing in as them).</p>`;
}

/* ------------------------------------------------------------------ *
 * Wiring
 * ------------------------------------------------------------------ */
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

async function refreshUserSelects() {
  const names = await backend.usernames();
  for (const id of ["off-user", "ex-user"]) {
    const cur = byId(id).value;
    byId(id).innerHTML = names.map((n) => `<option>${esc(n)}</option>`).join("");
    if (names.includes(cur)) byId(id).value = cur;
  }
}

const RENDERERS = {
  overview: renderOverview, directory: renderDirectory, explorer: renderExplorer,
  review: renderReview, audit: renderAudit, architecture: renderArchitecture,
  devices: renderDevices, compliance: renderCompliance, saas: renderSaas, ops: renderOps,
  requests: renderRequests, network: renderNetwork, jit: renderJit, readiness: renderReadiness,
  cost: renderCost, backup: renderBackup,
};

function go(view) {
  $$(".navitem").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + view));
  if (RENDERERS[view]) RENDERERS[view]();
}

function init() {
  $$(".navitem").forEach((b) => b.onclick = () => go(b.dataset.view));
  $$("[data-goto]").forEach((el) => el.onclick = () => go(el.dataset.goto));
  fillRoleSelect();
  byId("lg-pass").value = D.demo_password;
  byId("lg-hint").textContent = `Demo password for every seeded account: ${D.demo_password}`;
  byId("ob-submit").onclick = doOnboard;
  byId("off-submit").onclick = doOffboard;
  byId("ex-user").onchange = renderExplorer;
  byId("ex-sys").onchange = fillCheckControls;
  byId("ex-check").onclick = doCheck;
  byId("lg-submit").onclick = doLogin;
  // Detect live vs demo first, THEN populate the user lists from that backend
  // (so onboarding/offboarding on a live server drive the real user set).
  detectMode().then(async () => {
    await refreshUserSelects();
    go("overview");
  });
}

document.addEventListener("DOMContentLoaded", init);
