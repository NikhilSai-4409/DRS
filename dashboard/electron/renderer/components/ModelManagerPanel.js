// Model Manager (engineer tooling) — the single source of truth for every
// detector model. Lists production/candidate/experiment/archive models with
// normalised metadata + latest validation score, and drives the lifecycle
// (promote / rollback / archive / delete / notes) + A-vs-B compare on the
// validation set. Talks only to /api/models/* (which wraps core.model_registry).

const API_BASE = "http://localhost:8765";

const TYPE_BADGE = {
  production: "mm-b-prod", previous: "mm-b-prev", candidate: "mm-b-cand",
  experiment: "mm-b-exp", archive: "mm-b-arch", other: "mm-b-other",
};

export class ModelManagerPanel {
  constructor(root) {
    this.root = root;
    this.models = [];
    this.comparePoll = null;
  }

  async render() {
    this.root.innerHTML = this.template();
    this.bind();
    await this.load();
  }

  template() {
    return `
      <article class="card mm-panel">
        <header class="card-h">
          <div><strong>Model Manager</strong><small>Every detector model in one registry — metrics, validation score, and lifecycle</small></div>
          <span class="chip-quiet">Engineer tool</span>
        </header>
        <div class="mm-body">
          <section class="mm-toolbar">
            <button id="mm-refresh" class="btn" type="button">Refresh</button>
            <button id="mm-rollback" class="btn" type="button">Rollback production</button>
            <span id="mm-status" class="mm-status"></span>
          </section>

          <section class="mm-compare">
            <strong>Compare on validation set:</strong>
            <select id="mm-cmp-a" class="mm-sel"></select>
            <span>vs</span>
            <select id="mm-cmp-b" class="mm-sel"></select>
            <button id="mm-compare" class="btn primary" type="button">Compare</button>
            <span id="mm-cmp-status" class="mm-status"></span>
          </section>
          <section id="mm-cmp-result" class="mm-cmp-result" hidden></section>

          <div id="mm-table" class="mm-table-wrap"><p class="mm-empty">Loading…</p></div>

          <section class="mm-history">
            <h4>Deployment history</h4>
            <div id="mm-history"><p class="mm-empty">—</p></div>
          </section>
        </div>
      </article>
    `;
  }

  bind() {
    this.root.querySelector("#mm-refresh")?.addEventListener("click", () => this.load());
    this.root.querySelector("#mm-rollback")?.addEventListener("click", () => this.rollback());
    this.root.querySelector("#mm-compare")?.addEventListener("click", () => this.startCompare());
    this.root.querySelector("#mm-table")?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-action]");
      if (btn) this.onAction(btn.getAttribute("data-action"), btn.getAttribute("data-id"));
    });
  }

  async load() {
    let data;
    try {
      const res = await fetch(`${API_BASE}/api/models`);
      data = await res.json();
    } catch (e) {
      this.root.querySelector("#mm-table").innerHTML = `<p class="mm-empty">Backend unavailable — start the DRS engine.</p>`;
      return;
    }
    this.models = data.models || [];
    this.renderTable();
    this.renderCompareSelects();
    this.renderHistory(data.history || []);
    if (data.compare?.status === "running") this.pollCompare();
  }

  renderHistory(history) {
    const el = this.root.querySelector("#mm-history");
    if (!el) return;
    if (!history.length) { el.innerHTML = `<p class="mm-empty">No promotions yet.</p>`; return; }
    const rows = [...history].reverse().map((h) => {
      const bits = [];
      if (h.replaced) bits.push(`replaced <strong>${h.replaced}</strong>`);
      if (h.validation_score != null) bits.push(`val ${(h.validation_score * 100).toFixed(1)}%`);
      if (h.dataset) bits.push(`dataset ${h.dataset}`);
      if (h.epochs != null) bits.push(`${h.epochs} epochs`);
      if (h.by) bits.push(`by ${h.by}`);
      if (h.reason) bits.push(`“${h.reason}”`);
      return `<li><span class="mm-h-when">${h.time || ""}</span> <span class="mm-h-act mm-h-${h.action}">${h.action}</span> <strong>${h.model || ""}</strong>${bits.length ? " — " + bits.join(" · ") : ""}</li>`;
    }).join("");
    el.innerHTML = `<ul class="mm-h-list">${rows}</ul>`;
  }

  renderTable() {
    const el = this.root.querySelector("#mm-table");
    if (!this.models.length) {
      el.innerHTML = `<p class="mm-empty">No models found under <code>models/</code>.</p>`;
      return;
    }
    const num = (v, d = 3) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));
    const pct = (v) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);
    const rows = this.models.map((m) => {
      const actions = [];
      if (!m.is_production) actions.push(`<button class="mm-act" data-action="promote" data-id="${m.id}">Promote</button>`);
      if (!m.is_production && m.type !== "archive") actions.push(`<button class="mm-act" data-action="archive" data-id="${m.id}">Archive</button>`);
      actions.push(`<button class="mm-act" data-action="notes" data-id="${m.id}">Notes</button>`);
      if (!m.is_production) actions.push(`<button class="mm-act mm-danger" data-action="delete" data-id="${m.id}">Delete</button>`);
      return `
        <tr>
          <td><strong>${m.name}</strong><div class="mm-id">${m.id}</div></td>
          <td><span class="mm-badge ${TYPE_BADGE[m.type] || "mm-b-other"}">${m.type}${m.is_production ? " ●" : ""}</span></td>
          <td>${num(m.map50)}</td>
          <td>${num(m.precision)}</td>
          <td>${num(m.recall)}</td>
          <td class="mm-val">${pct(m.validation_score)}</td>
          <td>${m.size_mb ?? "—"} MB</td>
          <td>${m.epochs ?? "—"}</td>
          <td class="mm-notes"><span class="mm-notes-text" title="${(m.notes || "").replace(/"/g, "&quot;")}">${m.notes || "—"}</span></td>
          <td class="mm-actions">${actions.join("")}</td>
        </tr>`;
    }).join("");
    el.innerHTML = `
      <table class="mm-table">
        <thead><tr>
          <th>Model</th><th>Type</th><th>mAP50</th><th>Prec</th><th>Recall</th>
          <th>Val</th><th>Size</th><th>Epochs</th><th>Notes</th><th>Actions</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  renderCompareSelects() {
    const a = this.root.querySelector("#mm-cmp-a");
    const b = this.root.querySelector("#mm-cmp-b");
    if (!a || !b) return;
    const opts = this.models.map((m) => `<option value="${m.id}">${m.name} (${m.type})</option>`).join("");
    a.innerHTML = opts;
    b.innerHTML = opts;
    const prod = this.models.find((m) => m.is_production);
    const other = this.models.find((m) => !m.is_production);
    if (prod) a.value = prod.id;
    if (other) b.value = other.id;
  }

  async onAction(action, id) {
    if (action === "promote") {
      if (!confirm(`Promote ${id} to production? The current production model is backed up first.`)) return;
      await this.post("/api/models/promote", { id }, `Promoted ${id}`);
    } else if (action === "archive") {
      await this.post("/api/models/archive", { id }, `Archived ${id}`);
    } else if (action === "delete") {
      if (!confirm(`Delete ${id}? This removes the .pt file.`)) return;
      await this.post("/api/models/delete", { id }, `Deleted ${id}`);
    } else if (action === "notes") {
      const m = this.models.find((x) => x.id === id);
      const notes = prompt("Notes for this model:", m?.notes || "");
      if (notes === null) return;
      await this.post("/api/models/notes", { id, notes }, `Updated notes`);
    }
  }

  async rollback() {
    if (!confirm("Roll production back to previous_best.pt?")) return;
    await this.post("/api/models/rollback", {}, "Rolled back production");
  }

  async post(path, body, okMsg) {
    this.setStatus("Working…");
    try {
      const res = await fetch(`${API_BASE}${path}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        this.setStatus(`⚠ ${err.detail || res.statusText}`);
        return;
      }
      this.setStatus(okMsg);
      await this.load();
    } catch (e) {
      this.setStatus(`⚠ ${e.message}`);
    }
  }

  async startCompare() {
    const a = this.root.querySelector("#mm-cmp-a")?.value;
    const b = this.root.querySelector("#mm-cmp-b")?.value;
    if (a === b) { this.setCmpStatus("Pick two different models."); return; }
    try {
      const res = await fetch(`${API_BASE}/api/models/compare`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_a: a, model_b: b }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        this.setCmpStatus(`⚠ ${err.detail || res.statusText}`);
        return;
      }
      this.setCmpStatus("Running comparison…");
      this.pollCompare();
    } catch (e) {
      this.setCmpStatus(`⚠ ${e.message}`);
    }
  }

  pollCompare() {
    if (this.comparePoll) clearInterval(this.comparePoll);
    this.comparePoll = setInterval(async () => {
      let state;
      try { state = await (await fetch(`${API_BASE}/api/models/compare`)).json(); }
      catch { return; }
      if (state.status === "running") { this.setCmpStatus("Running comparison…"); return; }
      clearInterval(this.comparePoll); this.comparePoll = null;
      if (state.status === "error") { this.setCmpStatus(`⚠ ${state.error}`); return; }
      this.setCmpStatus("Comparison complete.");
      if (state.result) this.renderCompare(state.result);
    }, 2000);
  }

  renderCompare(r) {
    const el = this.root.querySelector("#mm-cmp-result");
    el.hidden = false;
    const rows = (r.clips || []).map((c) => `
      <tr>
        <td>${c.id}</td><td>${c.expected}</td>
        <td class="${c.a_correct ? "mm-ok" : "mm-bad"}">${c.a_verdict ?? "—"}</td>
        <td class="${c.b_correct ? "mm-ok" : "mm-bad"}">${c.b_verdict ?? "—"}</td>
      </tr>`).join("");
    el.innerHTML = `
      <h4>${this.short(r.model_a)} vs ${this.short(r.model_b)}</h4>
      <p>A: <strong>${(r.accuracy_a * 100).toFixed(1)}%</strong> (${r.correct_a}/${r.scored}) &nbsp;·&nbsp;
         B: <strong>${(r.accuracy_b * 100).toFixed(1)}%</strong> (${r.correct_b}/${r.scored})</p>
      <div class="mm-table-wrap"><table class="mm-table">
        <thead><tr><th>Clip</th><th>Expected</th><th>A</th><th>B</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
  }

  short(p) { return p ? String(p).split(/[\\/]/).pop() : "default"; }
  setStatus(t) { const e = this.root.querySelector("#mm-status"); if (e) e.textContent = t; }
  setCmpStatus(t) { const e = this.root.querySelector("#mm-cmp-status"); if (e) e.textContent = t; }
  destroy() { if (this.comparePoll) clearInterval(this.comparePoll); }
}
