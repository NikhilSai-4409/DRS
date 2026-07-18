// Timing policy for the broadcast replay — kept OUT of BroadcastReview so the renderer
// never owns cadence. play() maps timelineState() → DOM; presets/durations live here.
// Swap presets (IPL / TV / debug) or pass a partial override without touching render code.

// Durations in ms. A review runs: freeze on release → ball travels → Pitching → Impact →
// Wickets → Decision, with a deliberate hold between beats (that hold is what makes it
// read as a broadcast review instead of a continuous machine sweep).
export const ANIMATION_TIMELINES = {
  // Broadcast default (user-specified). release+travel+3 holds+decision = 4000ms.
  ipl:   { releaseHold: 500, travel: 1300, pitchingHold: 450, impactHold: 450, wicketsHold: 500, decisionReveal: 800 },
  // A touch more leisurely / dramatic.
  tv:    { releaseHold: 700, travel: 1500, pitchingHold: 600, impactHold: 600, wicketsHold: 650, decisionReveal: 1000 },
  // Snappy for development — barely any holds.
  debug: { releaseHold: 0,   travel: 350,  pitchingHold: 120, impactHold: 120, wicketsHold: 120, decisionReveal: 200 },
};

export const DEFAULT_TIMELINE = ANIMATION_TIMELINES.ipl;

// Accept a preset name, a full/partial timeline object, or nothing. Partial objects merge
// over the default so callers can nudge one duration without restating the whole preset.
export function resolveTimeline(input) {
  if (!input) return { ...DEFAULT_TIMELINE };
  if (typeof input === "string") return { ...DEFAULT_TIMELINE, ...(ANIMATION_TIMELINES[input] || {}) };
  return { ...DEFAULT_TIMELINE, ...input };
}

const clamp01 = (x) => Math.max(0, Math.min(1, x));

// PURE cadence function: given a timeline and elapsed ms, return exactly what should be
// on screen. No DOM, no side effects — so it is unit-testable and the renderer stays a
// dumb consumer. `travelP` (0..1) drives ball position; the show* flags gate the staged
// reveals; `ringP` (0..1) fills the confidence ring during the decision window.
export function timelineState(tl, elapsedMs) {
  const releaseEnd = tl.releaseHold;
  const travelEnd = releaseEnd + tl.travel;
  const pitchingEnd = travelEnd + tl.pitchingHold;
  const impactEnd = pitchingEnd + tl.impactHold;
  const wicketsEnd = impactEnd + tl.wicketsHold;
  const decisionEnd = wicketsEnd + tl.decisionReveal;
  return {
    travelP: clamp01((elapsedMs - releaseEnd) / tl.travel),
    showPitching: elapsedMs >= travelEnd,
    showImpact: elapsedMs >= pitchingEnd,
    showWickets: elapsedMs >= impactEnd,
    showDecision: elapsedMs >= wicketsEnd,
    ringP: clamp01((elapsedMs - wicketsEnd) / tl.decisionReveal),
    done: elapsedMs >= decisionEnd,
    totalMs: decisionEnd,
  };
}
