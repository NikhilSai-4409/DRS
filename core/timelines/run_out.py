"""Run Out timeline (no trajectory): freeze → frame-step → crease → bat → zoom → bails → verdict.

The camera frame-steps first (the key evidence is a few frames around the crease),
then zooms in — deliberately different from LBW's smooth zoom-then-trajectory.
"""

from __future__ import annotations

from core.timelines.base import ReviewTimeline


class RunOutTimeline(ReviewTimeline):
    key = "runout"
    duration = 5.0
    theme = "runout"
    verdict = (4.6, 0.3)
    cards_start = 3.6
    cues = {"crease": (0.9, 0.5), "bat": (1.6, 0.6), "bails": (2.6, 0.4), "framestep": (3.1, 1.0)}
    camera = (("freeze", 0.0, 0.4), ("framestep", 0.5, 1.8), ("zoom", 2.6, 0.9))
