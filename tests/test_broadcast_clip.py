import cv2
import numpy as np

from core.broadcast_clip import (
    GREEN_TOP,
    RED_TOP,
    _timeline,
    _verdict_colors,
    render_ultraedge_clip,
)


def test_timeline_covers_choreography():
    steps = _timeline(37, impact=18)
    assert len(steps) > 150  # entrance + passes + holds + end hold at 25 fps
    indices = [s["ri"] for s in steps]
    assert max(indices) == 36
    assert min(indices) == 0
    # the rock-back exists: the replay index decreases somewhere after impact
    after_impact = indices[indices.index(18):]
    assert any(b < a for a, b in zip(after_impact, after_impact[1:]))


def test_timeline_without_impact_is_single_pass():
    steps = _timeline(10, impact=None)
    indices = [s["ri"] for s in steps]
    assert max(indices) == 9
    assert all(b >= a for a, b in zip(indices, indices[1:]))  # never rewinds
    assert not any(s["marker"] for s in steps)


def test_verdict_color_mapping():
    assert _verdict_colors("NOT OUT")[0] == GREEN_TOP
    assert _verdict_colors("OUT")[0] == RED_TOP
    assert _verdict_colors("IN LINE")[0] == RED_TOP


def test_render_ultraedge_clip_writes_playable_mp4(tmp_path):
    rng = np.random.default_rng(3)
    frames = [rng.integers(0, 255, (180, 320, 3), dtype=np.uint8) for _ in range(12)]
    buckets = [[-0.1, 0.1]] * (12 * 10)
    out = tmp_path / "clip.mp4"
    render_ultraedge_clip(frames, buckets, [6], 6, out, review_label="LBW",
                          verdict="NOT OUT", cards=[("UltraEdge", "No Bat")])
    assert out.exists() and out.stat().st_size > 10_000
    cap = cv2.VideoCapture(str(out))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    assert frame_count > 80  # choreography plus the 2.4 s verdict tail
