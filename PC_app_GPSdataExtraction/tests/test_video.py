"""
End to end on a real video: keyframe sampling + OCR + cleaning. Needs a Vietmap M1 front
clip; set DASHGPS_TEST_VIDEO to its path (skipped otherwise).
"""
import os
from pathlib import Path

import pytest

from dashgps import extract, frames, ocr
from dashgps.core.profiles import VIETMAP_M1_FRONT

VIDEO = os.environ.get("DASHGPS_TEST_VIDEO")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not VIDEO or not Path(VIDEO).is_file(), reason="set DASHGPS_TEST_VIDEO to a front-camera clip"),
    pytest.mark.skipif(not (ocr.models_dir() / ocr.REC_MODEL).is_file(), reason="OCR models missing"),
]


def test_samples_one_keyframe_per_second():
    info = frames.info(VIDEO)
    got = [pos for pos, _ in frames.sample(VIDEO, 1.0, VIETMAP_M1_FRONT.crop)]
    assert got == sorted(got)
    assert len(got) >= int(info.duration_sec)
    assert all(b - a <= 1.0 + 1e-6 for a, b in zip(got, got[1:]))


def test_extracts_a_clean_track():
    x = extract.extract([Path(VIDEO)], VIETMAP_M1_FRONT)
    assert len(x.track.fixes) >= 0.9 * len(x.samples)
    assert len(x.track.segments) == 1
