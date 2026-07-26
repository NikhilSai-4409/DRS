// Truthful tri-state values for review evidence — the JS twin of core/observation.py.
// The wire literals are identical on both sides, so a renderer can never disagree
// with the module that produced the value.
//
// A DRS check has THREE outcomes, never two:
//   observed_true   the system looked and the thing was there
//   observed_false  the system looked and the thing was not there
//   not_observed    the system could not look at all
//
// Rule for consumers: never test one of these for truthiness. Compare to a member,
// or ask isKnown() first. UNKNOWN must render as its OWN visual state — never as
// the negative one. (A placeholder "dislodged" once reached the evidence frame as a
// red "BAILS DISLODGED"; naively removing it would have printed "BAILS INTACT",
// which is equally false.)

export const Observation = Object.freeze({
  TRUE: "observed_true",
  FALSE: "observed_false",
  UNKNOWN: "not_observed",
});

// Accepts a wire string or a legacy boolean/null. Anything unrecognised is UNKNOWN
// — an unreadable value is not evidence.
export function observation(value) {
  if (value === true) return Observation.TRUE;
  if (value === false) return Observation.FALSE;
  if (value === Observation.TRUE || value === Observation.FALSE) return value;
  return Observation.UNKNOWN;
}

export const isKnown = (value) => observation(value) !== Observation.UNKNOWN;

// Operator wording. `unknown` states what the camera saw rather than blaming the
// detector — "not detected" reads as a model failure to an umpire.
export function observationLabel(value, yes, no, unknown = "Not observed") {
  const o = observation(value);
  return o === Observation.TRUE ? yes : o === Observation.FALSE ? no : unknown;
}

export const BailsState = Object.freeze({
  DISLODGED: "dislodged",
  INTACT: "intact",
  NOT_OBSERVED: "not_observed",
});

// Legacy payloads used null for "no detector". That is NOT_OBSERVED, and must
// never be read as INTACT.
export function bailsState(value) {
  const v = String(value ?? "").toLowerCase();
  return v === BailsState.DISLODGED || v === BailsState.INTACT ? v : BailsState.NOT_OBSERVED;
}

export function bailsLabel(value) {
  const s = bailsState(value);
  return s === BailsState.DISLODGED ? "Dislodged" : s === BailsState.INTACT ? "Intact" : "Not observed";
}

// null when unobserved — callers must not treat that as "not broken".
export function wicketBroken(value) {
  const s = bailsState(value);
  return s === BailsState.NOT_OBSERVED ? null : s === BailsState.DISLODGED;
}

/* ===================== FRAME IDENTITY — twin of core/frame_ref.py =====================
   Three different integers named `frame_id` circulate in this system: per-camera
   capture counters, replay-clip positions, and (historically) synthesised array
   indices. Comparing across them draws an overlay on the WRONG MOMENT, which is
   misleading evidence rather than a cosmetic bug. `timestamp_ms` is the only value
   that means the same thing everywhere, so it is the cross-surface join key. */

export const FrameSpace = Object.freeze({
  CAPTURE: "capture",   // per-camera VideoFrame.frame_id counter
  CLIP: "clip",         // 0..total-1 within one frozen replay window
});

export class FrameSpaceMismatch extends Error {}

export function captureFrame(index, timestampMs = null, cameraId = null) {
  return {
    space: FrameSpace.CAPTURE,
    index: Number(index),
    timestamp_ms: timestampMs == null ? null : Number(timestampMs),
    source: cameraId == null ? null : `camera:${cameraId}`,
  };
}

export function clipFrame(index, timestampMs = null, window = null) {
  return {
    space: FrameSpace.CLIP,
    index: Number(index),
    timestamp_ms: timestampMs == null ? null : Number(timestampMs),
    source: window ?? null,
  };
}

// Comparable only within ONE space AND one source: camera 0 frame 194 and
// camera 1 frame 194 are different instants.
export function comparableWith(a, b) {
  return Boolean(a && b) && a.space === b.space && (a.source ?? null) === (b.source ?? null);
}

// Throws rather than returning false for an incomparable pair — a quiet false
// reads as "a different moment" when the honest answer is "unanswerable".
export function isSameMoment(a, b) {
  if (!comparableWith(a, b)) {
    throw new FrameSpaceMismatch(
      `cannot compare ${a && a.space}/${a && a.source} with ${b && b.space}/${b && b.source}` +
      " — join on timestamp_ms instead");
  }
  return a.index === b.index;
}

// The frame an overlay should draw for, given where the umpire is in the clip.
// Returns the tracked point whose timestamp is nearest — never an index match,
// because the track is in capture space and the scrubber is in clip space.
export function trackPointAt(track, timestampMs) {
  if (!Array.isArray(track) || !track.length || timestampMs == null) return null;
  let best = null, bestGap = Infinity;
  for (const point of track) {
    const ts = point && point.frame && point.frame.timestamp_ms;
    if (ts == null) continue;
    const gap = Math.abs(ts - timestampMs);
    if (gap < bestGap) { bestGap = gap; best = point; }
  }
  return best;
}
