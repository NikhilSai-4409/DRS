// Dynamic evidence panel for the Decision card — one config-driven definition per
// review type. Each entry declares the backend field that holds its analysis and a
// renderer that draws it, so adding a review type is configuration, not conditionals.
//
//   reviewDefinitions[type] = { label, backendField, body, render(u, data, decision) }
//
// The backend review modules (core/review_modules/*.py) already emit these blocks
// (wide_analysis / no_ball_analysis / run_out_analysis / edge_analysis); field names
// below mirror those modules exactly. Every value comes from the payload with a "--"
// fallback — never a hardcoded number. Hidden entirely while a review is not active.

const ALIAS = { lbw: "lbw", wide: "wide", noball: "noball", no_ball: "noball", front_foot: "noball", edge: "edge", ultraedge: "edge", runout: "runout", run_out: "runout", stumping: "stumping" };
function norm(t) { return ALIAS[String(t || "lbw").toLowerCase()] || "lbw"; }
function num(v) { return v == null || Number.isNaN(Number(v)) ? null : Number(v); }
function pct(v) { const n = num(v); return n == null ? "--" : `${Math.round(n * 100)}%`; }
function cm(v) { const n = num(v); return n == null ? "--" : `${Math.abs(n).toFixed(1)} cm`; }
function titleCase(v) { if (v == null || v === "UNKNOWN" || v === "") return "--"; const s = String(v); return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase(); }

// Verdict presets: [colour, badgeTextColour, badge, verdictText]
const VD = {
  out: ["#e0483a", "#ff6a5a", "OUT", "OUT"], notout: ["#2fbf6a", "#5fe89a", "NOT OUT", "NOT OUT"],
  hitting: ["#e0483a", "#ff6a5a", "OUT", "HITTING"], missing: ["#2fbf6a", "#5fe89a", "NOT OUT", "MISSING"],
  call: ["#f4b13a", "#ffcc66", "UMPIRE'S CALL", "UMPIRE'S CALL"], wait: ["#7f95a5", "#9fb3c2", "PENDING", "AWAITING"],
  wide: ["#e0483a", "#ff6a5a", "WIDE", "WIDE"], nowide: ["#2fbf6a", "#5fe89a", "NOT WIDE", "NOT WIDE"],
  noball: ["#e0483a", "#ff6a5a", "NO BALL", "NO BALL"], legal: ["#2fbf6a", "#5fe89a", "LEGAL", "LEGAL"],
  edge: ["#e0483a", "#ff6a5a", "EDGE", "EDGE"], noedge: ["#2fbf6a", "#5fe89a", "NO EDGE", "NO EDGE"],
};

const stumpsSvg = `
  <line x1="40" y1="128" x2="280" y2="128" stroke="#2a3742" stroke-width="2"/>
  <rect x="120" y="40" width="80" height="88" rx="4" fill="#ffffff08" stroke="#3aa0ff" stroke-width="1.3" stroke-dasharray="4 4"/>
  <g stroke-linecap="round"><line x1="140" y1="128" x2="140" y2="56" stroke="#e9e3d2" stroke-width="7"/><line x1="160" y1="128" x2="160" y2="54" stroke="#efe9d8" stroke-width="7"/><line x1="180" y1="128" x2="180" y2="56" stroke="#e9e3d2" stroke-width="7"/><line x1="133" y1="54" x2="167" y2="54" stroke="#d8d2c0" stroke-width="4"/><line x1="153" y1="53" x2="187" y2="53" stroke="#d8d2c0" stroke-width="4"/></g>`;
const view = (inner) => `<div class="wip-view"><svg viewBox="0 0 320 150" preserveAspectRatio="xMidYMid meet"><rect x="0" y="0" width="320" height="150" rx="8" fill="#0c1116"/>${inner}</svg></div>`;
const verdictEl = `<div class="wip-verdict" data-verdict>AWAITING</div>`;
const tiles = (a, b, c) => `<div class="wip-tiles">${[a, b, c].map(([lbl, key]) => `<div class="wip-tile"><span>${lbl}</span><b data-${key}>--</b></div>`).join("")}</div>`;

const reviewDefinitions = {
  lbw: {
    label: "WICKETS · BALL IMPACT",
    backendField: null,                       // LBW evidence lives at the decision top level
    body: view(`${stumpsSvg}<g data-impact hidden><path data-path d="M300,32 L172,100" fill="none" stroke="#e0483a" stroke-width="2.4" stroke-dasharray="7 6" opacity="0.9"/><circle data-glow cx="172" cy="100" r="20" fill="#e0483a" opacity="0.22"/><circle data-dot cx="172" cy="100" r="9" fill="#e0483a"/><circle data-dot-ring cx="172" cy="100" r="9" fill="none" stroke="#ffffffbb" stroke-width="2"/></g>`) + verdictEl + tiles(["STUMP", "stump"], ["HEIGHT", "height"], ["HIT PROB", "conf"]),
    render(u, _data, d) {
      const raw = String(d.wicket_status || d.wicket_zone_status || "").toUpperCase();
      const v = raw.includes("HIT") ? VD.hitting : raw.includes("UMPIRE") ? VD.call : raw.includes("MISS") ? VD.missing : VD.wait;
      u.verdict(v);
      const wp = d.wicket_prediction || {};
      u.set("[data-stump]", titleCase(wp.stump));
      const hMm = wp.impact_height_mm ?? d.impact_height_mm ?? null;
      u.set("[data-height]", hMm == null ? "--" : `${(Number(hMm) / 1000).toFixed(2)} m`);
      u.set("[data-conf]", pct(d.prediction_confidence ?? d.overall_confidence));
      const imp = u.q("[data-impact]"), show = raw.includes("HIT") || raw.includes("UMPIRE");
      if (imp) { imp.hidden = !show; if (show) { const dx = raw.includes("UMPIRE") ? 196 : 172; ["[data-dot]", "[data-glow]", "[data-dot-ring]"].forEach((s) => { const c = u.q(s); if (c) c.setAttribute("cx", dx); }); [u.q("[data-dot]"), u.q("[data-glow]")].forEach((c) => c && c.setAttribute("fill", v[0])); u.q("[data-path]").setAttribute("stroke", v[0]); } }
    },
  },
  wide: {
    label: "WIDE · BALL LINE",
    backendField: "wide_analysis",
    body: view(`<rect x="120" y="14" width="70" height="122" fill="#16324f" opacity="0.35"/><g stroke-linecap="round"><line x1="146" y1="34" x2="146" y2="18" stroke="#e9e3d2" stroke-width="4"/><line x1="155" y1="34" x2="155" y2="18" stroke="#e9e3d2" stroke-width="4"/><line x1="164" y1="34" x2="164" y2="18" stroke="#e9e3d2" stroke-width="4"/></g><line x1="70" y1="14" x2="70" y2="136" stroke="#be78cd" stroke-width="2" stroke-dasharray="6 5"/><text x="12" y="80" fill="#be78cd" font-size="10" font-family="system-ui">WIDE LINE</text><circle data-ball cx="120" cy="86" r="8" fill="#f2f2f2"/><circle data-ball-ring cx="120" cy="86" r="8" fill="none" stroke="#be78cd" stroke-width="2"/>`) + verdictEl + tiles(["MARGIN", "margin"], ["SIDE", "side"], ["CONFIDENCE", "conf"]),
    render(u, w) {
      const has = w.distance_cm != null || w.is_wide != null;
      u.verdict(w.is_wide === true ? VD.wide : has ? VD.nowide : VD.wait);
      u.set("[data-margin]", cm(w.distance_cm));
      u.set("[data-side]", titleCase(w.side));
      u.set("[data-conf]", pct(w.confidence));
      const ball = u.q("[data-ball]"), ring = u.q("[data-ball-ring]");
      const cx = w.distance_cm != null ? Math.max(78, Math.min(150, 120 + Number(w.distance_cm) * 1.2)) : 120;
      if (ball) { ball.setAttribute("cx", cx); ring.setAttribute("cx", cx); }
    },
  },
  noball: {
    label: "NO BALL · FRONT FOOT",
    backendField: "no_ball_analysis",
    body: view(`<line x1="30" y1="86" x2="290" y2="86" stroke="#ffdc78" stroke-width="3"/><text x="30" y="78" fill="#ffdc78" font-size="10" font-family="system-ui">POPPING CREASE</text><g data-foot><rect x="150" y="60" width="42" height="26" rx="5" fill="#5ec86e" opacity="0.3" stroke="#5ec86e" stroke-width="1.4"/><circle cx="188" cy="63" r="3" fill="#5ec86e"/></g>`) + verdictEl + tiles(["OVERSTEP", "overstep"], ["FRONT FOOT", "footpos"], ["CONFIDENCE", "conf"]),
    render(u, nb) {
      const has = nb.is_no_ball != null || nb.distance_past_cm != null;
      u.verdict(nb.is_no_ball === true ? VD.noball : has ? VD.legal : VD.wait);
      u.set("[data-overstep]", cm(nb.distance_past_cm));       // backend emits centimetres
      u.set("[data-footpos]", nb.foot_position || "--");
      u.set("[data-conf]", pct(nb.confidence));
      const foot = u.q("[data-foot]"); if (foot) foot.setAttribute("transform", `translate(${nb.is_no_ball === true ? 20 : -6},0)`);
    },
  },
  runout: {
    label: "RUN OUT · CREASE",
    backendField: "run_out_analysis",
    body: view(`<line x1="150" y1="14" x2="150" y2="136" stroke="#ffc800" stroke-width="3"/><text x="156" y="26" fill="#ffc800" font-size="10" font-family="system-ui">CREASE</text><g data-bat><line x1="120" y1="70" x2="120" y2="120" stroke="#c8dcff" stroke-width="5" stroke-linecap="round"/><rect x="112" y="118" width="16" height="8" rx="2" fill="#c8dcff"/></g>`) + verdictEl + tiles(["BAT", "batpos"], ["MARGIN", "margin"], ["CONFIDENCE", "conf"]),
    render(u, ro) {
      const has = ro.is_out != null || ro.distance_cm != null;
      u.verdict(ro.is_out === true ? VD.out : has ? VD.notout : VD.wait);
      u.set("[data-batpos]", ro.is_out === true ? "Short" : ro.is_out === false ? "In" : "--");
      u.set("[data-margin]", cm(ro.distance_cm));
      u.set("[data-conf]", pct(ro.confidence));
    },
  },
  edge: {
    label: "EDGE · CONTACT",
    backendField: "edge_analysis",
    body: view(`<line x1="12" y1="75" x2="308" y2="75" stroke="#22303a" stroke-width="1"/><polyline points="12,75 60,75 90,74 120,76 150,75" fill="none" stroke="#5aa0c8" stroke-width="2"/><g data-spike hidden><line x1="176" y1="20" x2="176" y2="130" stroke="#eb3232" stroke-width="2.5"/><text x="182" y="30" fill="#eb3232" font-size="10" font-family="system-ui">SPIKE</text></g><polyline points="200,75 240,75 270,74 308,75" fill="none" stroke="#5aa0c8" stroke-width="2"/>`) + verdictEl + tiles(["SPIKE", "spikeval"], ["CONTACT", "contact"], ["CONFIDENCE", "conf"]),
    render(u, e, d) {
      const prob = num(e.edge_probability);
      const events = e.events || e.peaks || [];
      const has = e.edge_probability != null && !e.inconclusive;
      const hotspot = d.hotspot_analysis || {};
      const contact = hotspot.contact_detected === true;
      const isEdge = has && ((prob != null && prob >= 0.5) || events.length > 0 || contact);
      u.verdict(isEdge ? VD.edge : has ? VD.noedge : VD.wait);
      u.set("[data-spikeval]", has ? pct(prob) : "--");
      u.set("[data-contact]", !has ? "--" : contact ? "Bat" : events.length ? "Bat" : "None");
      u.set("[data-conf]", pct(e.confidence ?? hotspot.confidence));
      const sp = u.q("[data-spike]"); if (sp) sp.hidden = !isEdge;
    },
  },
  stumping: {
    label: "STUMPING · CREASE",
    backendField: "stumping_analysis",
    body: view(`<line x1="150" y1="14" x2="150" y2="136" stroke="#ffc800" stroke-width="3"/><text x="156" y="26" fill="#ffc800" font-size="10" font-family="system-ui">CREASE</text>`) + verdictEl + tiles(["FOOT", "footpos"], ["BAILS", "bails"], ["CONFIDENCE", "conf"]),
    render(u, s, d) {
      const has = s.is_out != null;
      u.verdict(s.is_out === true ? VD.out : has ? VD.notout : VD.wait);
      u.set("[data-footpos]", s.foot_position || "--");
      u.set("[data-bails]", titleCase(s.bails_status));
      u.set("[data-conf]", pct(s.confidence ?? d.overall_confidence));
    },
  },
};

export class EvidencePanel {
  constructor(host) { this.host = host; this.type = null; this.host.hidden = true; this.render("lbw"); }

  render(k) {
    this.type = k;
    const def = reviewDefinitions[k] || reviewDefinitions.lbw;
    this.host.innerHTML = `<div class="wip"><div class="wip-head"><span class="wip-eyebrow" data-label>${def.label}</span><span class="wip-badge" data-badge>PENDING</span></div>${def.body}</div>`;
  }

  update(type, decision) {
    const k = norm(type);
    const d = decision || {};
    const waiting = String(d.status || "WAITING").toUpperCase() === "WAITING";
    this.host.hidden = waiting;               // no evidence panel while idle
    if (waiting) return;
    if (k !== this.type) this.render(k);
    const def = reviewDefinitions[k] || reviewDefinitions.lbw;
    const data = def.backendField ? (d[def.backendField] || {}) : d;
    const q = (s) => this.host.querySelector(s);
    const u = {
      q,
      set: (s, v) => { const n = q(s); if (n) n.textContent = v; },
      verdict: (vd) => { const b = q("[data-badge]"), v = q("[data-verdict]"); if (b) { b.textContent = vd[2]; b.style.borderColor = vd[0]; b.style.color = vd[1]; } if (v) { v.textContent = vd[3]; v.style.color = vd[0]; } },
    };
    def.render(u, data, d);
  }
}
