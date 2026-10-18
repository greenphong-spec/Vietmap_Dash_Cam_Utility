"""OCR on real Vietmap M1 overlay strips (2048x144 luma crops of 2560x1440 front video)."""
from datetime import datetime

import numpy as np
import pytest
from PIL import Image

from conftest import RESOURCES
from dashgps import ocr
from dashgps.core.profiles import VIETMAP_M1_FRONT

pytestmark = pytest.mark.skipif(
    not (ocr.models_dir() / ocr.REC_MODEL).is_file(), reason="OCR models missing: python tools/fetch_models.py")


def crop(name):
    return np.asarray(Image.open(RESOURCES / "frames" / f"{name}.png").convert("L"))


@pytest.mark.parametrize("name, time, speed, lat, lon", [
    ("town_74kmh", datetime(2026, 9, 19, 17, 52, 22), 74, 10.40776, 106.33883),
    ("highway", datetime(2026, 8, 31, 16, 5, 14), 71, 10.86700, 107.55365),
    # The recogniser's best guess has no space in "31/08/2026  16:09:02"; the space class is
    # still likely in that gap, and the decoder puts the space back.
    ("needs_space_recovery", datetime(2026, 8, 31, 16, 9, 2), 71, 10.87854, 107.50993),
])
def test_reads_real_overlay(name, time, speed, lat, lon):
    text = ocr.engine().read(crop(name))
    r = VIETMAP_M1_FRONT.parse(text)
    assert r is not None, text
    assert (r.time, r.speed_kmh) == (time, speed), text
    assert r.lat == pytest.approx(lat, abs=1e-9)
    assert r.lon == pytest.approx(lon, abs=1e-9)


def test_empty_strip_reads_nothing():
    assert ocr.engine().read(np.zeros((144, 2048), np.uint8)) == ""


def test_space_recovery_only_in_gaps():
    e = ocr.engine()
    space = len(e.chars) - 1
    a, b = (list(e.chars).index(c) for c in ("2", "1"))

    def pred(steps):
        p = np.zeros((len(steps), len(e.chars)), np.float32)
        for t, (cls, sp) in enumerate(steps):
            p[t, cls] = 0.9
            p[t, space] = sp
        return p
    # "2", gap with some space probability, "1" -> "2 1"; without it -> "21".
    assert e._ctc(pred([(a, 0), (0, 0.3), (b, 0)]))[0] == "2 1"
    assert e._ctc(pred([(a, 0), (0, 0.02), (b, 0)]))[0] == "21"
    # A space inside a run of the same character splits it: "5 5", not "5".
    assert e._ctc(pred([(a, 0), (a, 0.3), (a, 0)]))[0] == "2 2"
