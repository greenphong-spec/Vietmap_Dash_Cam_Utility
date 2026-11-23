"""
extract.extract() with the decoder and OCR stubbed out: a "crop" is the overlay text itself,
so these run without a video or the OCR models.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from dashgps import extract, frames
from dashgps.core.profiles import VIETMAP_M1_FRONT


def overlay(sec: int) -> str:
    return f"13/09/2026 11:31:{sec:02d} 0KM/H N10.76736 E106.63187"


@pytest.fixture
def stub_ocr(monkeypatch):
    monkeypatch.setattr(extract.OcrPool, "read", lambda self, crop: crop)
    monkeypatch.setattr(frames, "info", lambda path: SimpleNamespace(duration_sec=10.0))


def test_a_damaged_video_is_reported_and_the_others_are_still_read(monkeypatch, stub_ocr):
    def sample(path, interval, crop, cancel=None, on_progress=lambda pos: None):
        start = 0 if path == "a.mp4" else 10
        for k in range(3):
            yield float(k), overlay(start + k)
        if path == "a.mp4":
            raise OSError("moov atom not found")
    monkeypatch.setattr(frames, "sample", sample)

    x = extract.extract([Path("a.mp4"), Path("b.mp4")], VIETMAP_M1_FRONT, second_look_pass=False, workers=2)

    assert [(s.video_name, s.pos_sec) for s in x.samples] == [
        ("a.mp4", 0.0), ("a.mp4", 1.0), ("a.mp4", 2.0), ("b.mp4", 0.0), ("b.mp4", 1.0), ("b.mp4", 2.0)]
    assert x.failed == [("a.mp4", "moov atom not found")]
    assert "Could not read a.mp4 to the end" in extract.summary(x, extract.Zone("Asia/Ho_Chi_Minh"))


def test_an_ocr_error_is_not_mistaken_for_a_damaged_video(monkeypatch, stub_ocr):
    monkeypatch.setattr(frames, "sample", lambda *a, **k: iter([(0.0, "x")]))

    def broken(self, crop):
        raise RuntimeError("model missing")
    monkeypatch.setattr(extract.OcrPool, "read", broken)

    with pytest.raises(RuntimeError, match="model missing"):
        extract.extract([Path("a.mp4")], VIETMAP_M1_FRONT, workers=1)


def test_second_look_matches_frames_by_position_when_one_cannot_be_decoded(monkeypatch, stub_ocr):
    monkeypatch.setattr(frames, "sample",
                        lambda *a, **k: iter([(float(k), f"unreadable {k}") for k in range(4)]))

    def frames_at(path, positions, crop, cancel=None):
        for i, pos in enumerate(positions):
            if i == 0:
                continue  # keyframe not found: nothing for this position
            yield i, pos, overlay(int(pos))
    monkeypatch.setattr(frames, "frames_at", frames_at)
    monkeypatch.setattr(extract, "second_look", lambda crop, first, accept: crop if accept(crop) else None)

    x = extract.extract([Path("a.mp4")], VIETMAP_M1_FRONT, workers=1)

    assert (x.second_looked, x.second_look_changed) == (3, 3)
    assert [s.text for s in x.samples] == ["unreadable 0", overlay(1), overlay(2), overlay(3)]
