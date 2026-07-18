// LBW Validation tab (engineer tooling) — a thin UI over the same engine the
// CLI drives (core/lbw_validation.py via /api/testing/validation/*). Placeholder
// on purpose: runs the ground-truth set, shows accuracy + regressions, lets you
// open a run's per-clip breakdown. Fill in charts/filters later.

const API_BASE = "http://localhost:8765";

const STATUS_ICON = { correct: "✅", incorrect: "❌", error: "💥" };

export class ValidationPanel {
  constructor(root) {
    this.root = root;
    this.pollTimer = null;
    this.selectedRun = null;
  }

  async render() {
    this.root.innerHTML = this.template();
    this.bind();
    await this.loadRuns();
  }

  template() {
    return `
      <article class="card val-panel">
        <header class="card-h">
          <div><strong>LBW Validation</strong><small>Score the DRS against known outcomes — measure accuracy and catch regressions</small></div>
          <span class="chip-quiet">Engineer tool</span>
        </header>
        <div class="val-body">
          <section class="val-run-bar">
            <button id="val-run" class="btn primary" type="button">Run Validation</button>
            <span id="val-status" class="val-status">—</span>
          </section>
          <p class="val-hint">Ground truth lives in <code>data/testing/validation_set.json</code>. Add labelled clips there (or run <code>python scripts/validate_lbw.py</code>). Each run scores every clip and diffs against the previous run.</p>

          <section class="val-latest" id="val-latest" hidden></section>

          <section class="val-history">
            <h4>Runs</h4>
            <div id="val-runs" class="val-table-wrap"><p class="val-empty">Loading…</p></div>
          </section>

          <section class="val-detail" id="val-detail" hidden></section>
        </div>
      </article>
    `;
  }

  bind() {
    this.root.querySelector("#val-run")?.addEventListener("click", () => this.startRun());
    this.root.querySelector("#val-runs")?.addEventListener("click", (e) => {
      const row = e.target.closest("[data-run-id]");
      if (row) this.loadDetail(row.getAttribute("data-run-id"));
    });
  }

  async startRun() {
    const btn = this.root.querySelector("#val-run");
    try {
      const res = await fetch(`${API_BASE}/api/testing/validation/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        this.setStatus(`⚠ ${err.detail || res.statusText}`);
        return;
      }
      const data = await res.json();
      this.setStatus(`Queued ${data.clips ?? ""} clip(s)…`);
      if (btn) btn.disabled = true;
      this.startPolling();
    } catch (e) {
      this.setStatus(`⚠ ${e.message}`);
    }
  }

  startPolling() {
    this.stopPolling();
    this.pollTimer = setInterval(() => this.loadRuns(), 2000);
  }

  stopPolling() {
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.pollTimer = null;
  }

  async loadRuns() {
    let data;
    try {
      const res = await fetch(`${API_BASE}/api/testing/validation/runs`);
      data = await res.json();
    } catch (e) {
      this.root.querySelector("#val-runs").innerHTML = `<p class="val-empty">Backend unavailable — start the DRS engine.</p>`;
      return;
    }
    const state = data.state || {};
    const btn = this.root.querySelector("#val-run");
    if (state.status === "running") {
      this.setStatus("Running…");
      if (btn) btn.disabled = true;
    } else {
      if (btn) btn.disabled = false;
      if (state.status === "error") this.setStatus(`⚠ ${state.error || "run failed"}`);
      else if (state.status === "complete") this.setStatus("Last run complete.");
      else this.setStatus("Idle");
      this.stopPolling();
    }
    this.renderRuns(data.runs || []);
    // auto-open the newest run once a run finishes
    if (state.status === "complete" && state.run_id && this.selectedRun !== state.run_id) {
      this.loadDetail(state.run_id);
    }
  }

  renderRuns(runs) {
    const el = this.root.querySelector("#val-runs");
    if (!runs.length) {
      el.innerHTML = `<p class="val-empty">No runs yet. Add clips to the manifest and press <strong>Run Validation</strong>.</p>`;
      return;
    }
    const rows = [...runs].reverse().map((r) => {
      const scored = (r.correct || 0) + (r.incorrect || 0);
      const acc = ((r.accuracy || 0) * 100).toFixed(1);
      return `
        <tr data-run-id="${r.run_id}" class="val-row">
          <td>${r.timestamp || r.run_id}</td>
          <td>${this.modelLabel(r.model)}</td>
          <td><strong>${acc}%</strong> <span class="val-dim">(${r.correct || 0}/${scored})</span></td>
          <td>${(r.avg_detection_confidence || 0).toFixed(2)}</td>
          <td>${r.replay_success || 0}/${r.total || 0}</td>
        </tr>`;
    }).join("");
    el.innerHTML = `
      <table class="val-table">
        <thead><tr><th>Run</th><th>Model</th><th>Accuracy</th><th>Det.conf</th><th>Replays</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  async loadDetail(runId) {
    this.selectedRun = runId;
    const el = this.root.querySelector("#val-detail");
    el.hidden = false;
    el.innerHTML = `<p class="val-empty">Loading run ${runId}…</p>`;
    let report;
    try {
      const res = await fetch(`${API_BASE}/api/testing/validation/runs/${encodeURIComponent(runId)}`);
      if (!res.ok) { el.innerHTML = `<p class="val-empty">Run not found.</p>`; return; }
      report = await res.json();
    } catch (e) {
      el.innerHTML = `<p class="val-empty">${e.message}</p>`;
      return;
    }
    const regr = (report.regressions || []).map((r) => `<li>⚠ <code>${r.id}</code> ${r.was} → ${r.now} (expected ${r.expected})</li>`).join("");
    const impr = (report.improvements || []).map((r) => `<li>✅ <code>${r.id}</code> ${r.was} → ${r.now} (expected ${r.expected})</li>`).join("");
    const clips = (report.clips || []).map((c) => `
      <tr class="val-clip val-${c.status}">
        <td>${STATUS_ICON[c.status] || "?"}</td>
        <td>${c.id}</td>
        <td>${c.expected_verdict}</td>
        <td>${c.actual_verdict}</td>
        <td>${(c.detection_confidence || 0).toFixed(2)}</td>
        <td class="val-reason">${c.reason_for_failure || "—"}</td>
      </tr>`).join("");
    el.innerHTML = `
      <h4>Run ${report.run_id} — ${((report.accuracy || 0) * 100).toFixed(1)}% (${report.correct}/${report.scored})</h4>
      ${regr ? `<ul class="val-diff val-diff-bad">${regr}</ul>` : ""}
      ${impr ? `<ul class="val-diff val-diff-good">${impr}</ul>` : ""}
      <div class="val-table-wrap">
        <table class="val-table">
          <thead><tr><th></th><th>Clip</th><th>Expected</th><th>Actual</th><th>Conf</th><th>Reason</th></tr></thead>
          <tbody>${clips}</tbody>
        </table>
      </div>`;
  }

  modelLabel(model) {
    if (!model) return "default";
    const parts = String(model).split(/[\\/]/);
    return parts[parts.length - 1];
  }

  setStatus(text) {
    const el = this.root.querySelector("#val-status");
    if (el) el.textContent = text;
  }

  destroy() {
    this.stopPolling();
  }
}
