// The ONE mapping from a backend /results payload to the decision shape BroadcastReview
// reads. Both the Testing page and the live Dashboard import this, so the broadcast card
// is fed a provably identical object on both surfaces (no divergent per-page mappers).
// The canonical `results.trajectory` object passes straight through — the renderer
// projects its observed points into the SVG viewBox itself. Distances stay "--" (the
// results trajectory is in px, not decision_mapper metres); statuses/verdict/confidence/
// speed are real.
export function resultsToBroadcastDecision(results) {
  const decision = results.decision || {};
  const gates = results.lbw_gates || {};
  const summary = results.summary || {};
  const norm = (v) => (v == null ? undefined : String(v).replace(/_/g, " ").toUpperCase());
  const verdict = decision.verdict || "UMPIRES_CALL";
  return {
    status: verdict,                              // non-WAITING => active
    decision: verdict.replace(/_/g, " "),
    overall_confidence: decision.confidence,
    ball_speed_kmh: summary.ball_speed_kmh,
    pitching_status: norm(gates.pitching?.result),
    impact_status: norm(gates.impact?.result),
    wicket_status: norm(gates.wickets?.result),   // "hitting" -> HITTING (drives the stump glow)
    reliability: results.reliability,
    bounce_point: results.trajectory?.bounce_point,
    impact_point: results.trajectory?.impact_point,
    // Canonical trajectory object — the renderer projects it into the SVG viewBox itself,
    // so the ball animates along the analysed path instead of a template.
    trajectory: results.trajectory,
  };
}
