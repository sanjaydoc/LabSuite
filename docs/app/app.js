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
    // operations: SaaS + registries
    const ops = data.operations || {};
    this.saas = {};
    for (const a of ops.saas || []) this.saas[a.name] = { name: a.name, cost: a.monthly_cost_per_seat, assignees: new Set(a.assignees || []) };
    this.equipment = ops.equipment || [];
    this.inventory = ops.inventory || [];
    this.vendors = ops.vendors || [];
    this.safety = ops.safety || [];
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
    const truenas = {}, proxmox = {}, blocked = {};
    for (const s in this.d.shares) {
      const r = this.truenasLevel(s, groups);
      if (r.level <= 0) continue;
      const missing = active ? this.missingForShare(username, s) : [];
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
    let reason;
    if (allowed && missing.length) { allowed = false; reason = `BLOCKED — training not current: ${missing.join(", ")}`; }
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
  if (!Object.keys(trainings).length && !Object.keys(blocked).length) return "";
  const tchips = Object.entries(trainings).map(([t, s]) => {
    const cls = s === "current" ? "allow" : s === "expired" ? "deny" : "";
    return `<span class="chip ${cls}">${esc(t)}: ${esc(s)}</span>`;
  }).join(" ");
  const bchips = Object.entries(blocked).map(([s, m]) =>
    `<div class="muted" style="font-size:.83rem"><span class="chip deny">blocked</span> ${esc(s)} — needs ${m.map(esc).join(", ")}</div>`).join("");
  return `<hr class="divider" /><div class="label">Compliance / training</div>
    <div class="tags" style="margin:.3rem 0 .4rem">${tchips || '<span class="muted">none</span>'}</div>${bchips}`;
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
      <p class="muted" style="font-size:.8rem;margin:.9rem 0 0">Dashed tags ⤴ are inherited through AD nesting. Blocked shares need current training.</p>
    </div>`;
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
  byId("rv-flags").innerHTML = r.flags.length
    ? r.flags.map((f) => `<div class="flag"><span class="bang">!</span> ${esc(f)}</div>`).join("")
    : '<div class="chip allow">✓ estate is clean</div>';
  const rows = Object.keys(r.entitlements).sort();
  byId("rv-table").innerHTML = `<tr><th>User</th><th>Status</th><th>Shares</th><th>VMs</th></tr>` +
    rows.map((u) => { const e = r.entitlements[u]; return `<tr><td>${esc(u)}</td>
      <td>${e.active ? '<span class="chip allow">active</span>' : '<span class="chip deny">disabled</span>'}</td>
      <td>${Object.keys(e.truenas).length}</td><td>${Object.keys(e.proxmox).length}</td></tr>`; }).join("");
}

async function renderAudit() {
  const events = await backend.audit();
  byId("audit-list").innerHTML = events.length ? events.map((e) => `
    <div class="audit-row">
      <span class="sys">${esc(e.system)}</span>
      <span class="act">${esc(e.action)}</span>
      <span class="det">${esc(e.target)} <span class="muted">${esc(e.detail || "")}</span></span>
      <span>${e.outcome === "denied" || e.outcome === "error" ? '<span class="chip deny">' + esc(e.outcome) + '</span>' : '<span class="chip allow">' + esc(e.outcome) + '</span>'}</span>
    </div>`).join("") : '<div class="muted">No events yet — try onboarding or a decision, then return here.</div>';
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
