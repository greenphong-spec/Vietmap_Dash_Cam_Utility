"""
Pulls one frame every interval_sec seconds out of a video, as fast as possible. PC
counterpart of the Android app's FrameSampler (MediaExtractor + MediaCodec), on PyAV
(FFmpeg libraries bundled in the wheel).

Only **keyframes** are read and decoded: for each sample time the demuxer seeks to the
keyframe at or before it and only that packet goes to the decoder. Keyframes decode on
their own, so no other frames are needed. Vietmap records two keyframes per second and its
overlay changes once per second, so this reads every overlay value while decoding ~1/30
of the frames. If a camera's keyframes are further apart than the interval, each keyframe
is used once (sampling becomes the keyframe spacing).

Only the Y (luma) plane of the crop region is copied out: the overlay text is white,
colour is not needed for OCR.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Optional, Tuple

import av
import numpy as np

from .core.models import CropRect

# Pixel formats whose plane 0 is 8-bit luma, one byte per pixel.
_LUMA8 = {"yuv420p", "yuvj420p", "yuv422p", "yuvj422p", "yuv444p", "yuvj444p", "nv12", "nv21", "gray"}


@dataclass(frozen=True)
class VideoInfo:
    duration_sec: float
    width: int
    height: int
    codec: str


def _video_stream(container):
    if not container.streams.video:
        raise ValueError("no video track")
    return container.streams.video[0]


def info(path: str) -> VideoInfo:
    with av.open(path) as c:
        return info_of(c, _video_stream(c))


def crop_luma(frame: av.VideoFrame, c: CropRect) -> np.ndarray:
    """The luma pixels of the crop region, as a (height, width) uint8 array."""
    w, h = frame.width, frame.height
    x0 = min(max(int(c.left * w), 0), w - 1)
    x1 = min(max(int(c.right * w), 1), w)
    y0 = min(max(int(c.top * h), 0), h - 1)
    y1 = min(max(int(c.bottom * h), 1), h)
    if frame.format.name in _LUMA8:
        plane = frame.planes[0]
        y = np.frombuffer(plane, np.uint8).reshape(plane.height, plane.line_size)
        return np.ascontiguousarray(y[y0:y1, x0:x1])
    # 10-bit, RGB, ...: let FFmpeg convert.
    return np.ascontiguousarray(frame.to_ndarray(format="gray")[y0:y1, x0:x1])


def sample(
    path: str,
    interval_sec: float,
    crop: CropRect,
    cancel: Optional[threading.Event] = None,
    start_sec: float = 0.0,
    max_frames: Optional[int] = None,
    on_progress: Callable[[float], None] = lambda pos: None,
) -> Iterator[Tuple[float, np.ndarray]]:
    """Yields (position in seconds, luma crop) for each sampled frame, in order."""
    with av.open(path) as c:
        s = _video_stream(c)
        # No frame threading: it holds pictures back, and HEVC drops held-back pictures when
        # the next (non-consecutive) keyframe arrives.
        s.thread_type = "SLICE"
        cc = s.codec_context
        cc.skip_frame = "NONKEY"
        tb = s.time_base
        start_pts = s.start_time or 0
        duration = info_of(c, s).duration_sec

        def frames(packet):
            for fr in cc.decode(packet):
                pos = float((fr.pts - start_pts) * tb) if fr.pts is not None else float(fr.time or 0.0)
                yield pos, crop_luma(fr, crop)

        def keyframes():
            """(position, packet) of the keyframe at or before each sample time, each keyframe once.
            Seeking reads only those packets - most of the file is never read (slow disks, SD cards)."""
            target = start_sec
            last = None
            while target <= duration + 1e-6:
                c.seek(int(target / tb) + start_pts, stream=s, backward=True, any_frame=False)
                pkt = next((p for p in c.demux(s) if p.pts is not None and p.is_keyframe), None)
                target += interval_sec
                if pkt is None:
                    break
                if last is not None and pkt.pts <= last:
                    # Keyframes further apart than the interval: jump to the next one.
                    nxt = next((p for p in c.demux(s) if p.pts is not None and p.is_keyframe and p.pts > last), None)
                    if nxt is None:
                        break
                    pkt = nxt
                    target = max(target, float((pkt.pts - start_pts) * tb) + interval_sec)
                last = pkt.pts
                yield float((pkt.pts - start_pts) * tb), pkt

        fed = 0
        for ts, pkt in keyframes():
            if cancel is not None and cancel.is_set():
                return
            on_progress(ts)
            yield from frames(pkt)
            fed += 1
            if max_frames is not None and fed >= max_frames:
                break
        yield from frames(None)  # anything the decoder still holds


def frames_at(path: str, positions: Iterable[float], crop: CropRect,
              cancel: Optional[threading.Event] = None) -> Iterator[Tuple[float, np.ndarray]]:
    """
    (position, luma crop) of the keyframe at or before each position - for positions that
    came from sample(), the very frame read then. The file is opened once.
    """
    with av.open(path) as c:
        s = _video_stream(c)
        s.thread_type = "SLICE"
        cc = s.codec_context
        cc.skip_frame = "NONKEY"
        tb = s.time_base
        start_pts = s.start_time or 0
        for pos in positions:
            if cancel is not None and cancel.is_set():
                return
            c.seek(int(round(pos / tb)) + start_pts, stream=s, backward=True, any_frame=False)
            pkt = next((p for p in c.demux(s) if p.pts is not None and p.is_keyframe), None)
            if pkt is None:
                continue
            for fr in list(cc.decode(pkt)) + list(cc.decode(None)):
                yield float((fr.pts - start_pts) * tb), crop_luma(fr, crop)
            cc.flush_buffers()


def info_of(c, s) -> VideoInfo:
    if s.duration is not None and s.time_base is not None:
        dur = float(s.duration * s.time_base)
    elif c.duration is not None:
        dur = c.duration / 1e6
    else:
        dur = 0.0
    return VideoInfo(dur, s.codec_context.width, s.codec_context.height, s.codec_context.name)
