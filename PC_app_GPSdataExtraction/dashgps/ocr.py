"""
Offline OCR on the PC, in place of the Android app's ML Kit text recognition. Same
contract: a luma crop of the overlay strip in, all recognised text on one line out
("" when nothing is found).

PaddleOCR PP-OCRv6 small models (Apache-2.0, as packaged by RapidOCR) on ONNX Runtime,
with a lean pipeline for one strip of horizontal text:

1. detection on the strip scaled to at most DET_MAX_WIDTH wide (text stays ~15+ px tall);
2. axis-aligned boxes from the probability map (DB post-processing: threshold, dilate,
   box score, unclip), mapped back onto the full-resolution strip;
3. recognition of every box at 48 px height, batched, CTC-decoded;
4. boxes joined left to right, line by line.

General OCR wrappers re-plan for every image shape and spend most of their time outside
the network; on an older 6-core CPU this pipeline is ~4x faster for the same models.
The models are not in git: tools/fetch_models.py puts them in dashgps/models/.
"""
from __future__ import annotations

import math
import os
import sys
import threading
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

DET_MODEL = "PP-OCRv6_det_small.onnx"
REC_MODEL = "PP-OCRv6_rec_small.onnx"

DET_MAX_WIDTH = 1024
DET_THRESH = 0.3
DET_BOX_THRESH = 0.5
DET_UNCLIP = 1.6
DET_MIN_SIZE = 3
REC_HEIGHT = 48
REC_MIN_WIDTH = 320
REC_BATCH = 6
REC_WIDTH_STEP = 64
TEXT_SCORE = 0.5
# Space-class probability in a gap between two characters that means "there is a space".
# On 600 real frames every gap at >= 0.08 was a real word boundary.
SPACE_PROB = 0.1


def models_dir() -> Path:
    """dashgps/models next to this file; inside the PyInstaller bundle, _MEIPASS/dashgps/models."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "dashgps" / "models"


class Engine:
    """The two ONNX sessions. Thread-safe: ONNX Runtime allows concurrent run() calls."""

    def __init__(self, threads_per_call: int = 2, folder: Optional[Path] = None):
        import onnxruntime as ort
        folder = folder or models_dir()
        missing = [m for m in (DET_MODEL, REC_MODEL) if not (folder / m).is_file()]
        if missing:
            raise FileNotFoundError(f"OCR model(s) missing in {folder}: {', '.join(missing)}. "
                                    "Run: python tools/fetch_models.py")
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads_per_call
        so.inter_op_num_threads = 1
        so.log_severity_level = 3
        providers = ["CPUExecutionProvider"]
        self.det = ort.InferenceSession(str(folder / DET_MODEL), so, providers=providers)
        self.rec = ort.InferenceSession(str(folder / REC_MODEL), so, providers=providers)
        self._det_in = self.det.get_inputs()[0].name
        self._rec_in = self.rec.get_inputs()[0].name
        chars = self.rec.get_modelmeta().custom_metadata_map["character"].splitlines()
        # CTC classes: 0 = blank, then the dictionary, then a space.
        self.chars = np.array(["", *chars, " "], dtype=object)

    # ---- detection ---------------------------------------------------------------------

    def detect(self, gray: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """Text boxes (x0, y0, x1, y1) in the coordinates of gray."""
        h, w = gray.shape
        scale = min(1.0, DET_MAX_WIDTH / w)
        dw = max(32, int(round(w * scale / 32)) * 32)
        dh = max(32, int(round(h * scale / 32)) * 32)
        img = cv2.resize(gray, (dw, dh), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
        x = (img.astype(np.float32) / 255.0 - 0.5) / 0.5
        x = np.repeat(x[np.newaxis, np.newaxis], 3, axis=1)
        prob = self.det.run(None, {self._det_in: x})[0][0, 0]
        mask = cv2.dilate((prob > DET_THRESH).astype(np.uint8), np.ones((2, 2), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        sx, sy = w / dw, h / dh
        boxes = []
        for c in contours:
            bx, by, bw, bh = cv2.boundingRect(c)
            if min(bw, bh) < DET_MIN_SIZE:
                continue
            region = np.zeros((bh, bw), np.uint8)
            cv2.drawContours(region, [c - [bx, by]], -1, 1, -1)
            if cv2.mean(prob[by:by + bh, bx:bx + bw], region)[0] < DET_BOX_THRESH:
                continue
            d = bw * bh * DET_UNCLIP / (2 * (bw + bh))  # DB "unclip": grow by area * r / perimeter
            x0, y0 = (bx - d) * sx, (by - d) * sy
            x1, y1 = (bx + bw + d) * sx, (by + bh + d) * sy
            if min(x1 - x0, y1 - y0) < DET_MIN_SIZE + 2:
                continue
            boxes.append((max(0, int(x0)), max(0, int(y0)), min(w, int(math.ceil(x1))), min(h, int(math.ceil(y1)))))
        return _reading_order(boxes)

    # ---- recognition -------------------------------------------------------------------

    def recognize(self, images: List[np.ndarray]) -> List[Tuple[str, float]]:
        """Text and mean confidence per image. Batches hold images of similar width only:
        every image in a batch is padded to the widest, and the network's cost grows with width."""
        out: List[Tuple[str, float]] = [("", 0.0)] * len(images)
        ratio = [img.shape[1] / img.shape[0] for img in images]
        batches: List[List[int]] = []
        for i in sorted(range(len(images)), key=lambda i: ratio[i]):
            if batches and len(batches[-1]) < REC_BATCH and ratio[i] <= 1.3 * max(ratio[batches[-1][0]], REC_MIN_WIDTH / REC_HEIGHT):
                batches[-1].append(i)
            else:
                batches.append([i])
        for idx in batches:
            width = int(REC_HEIGHT * max(REC_MIN_WIDTH / REC_HEIGHT, *(ratio[i] for i in idx)))
            # Few distinct input shapes: ONNX Runtime plans each new shape again, which costs
            # more than the padding.
            width = -(-width // REC_WIDTH_STEP) * REC_WIDTH_STEP
            batch = np.zeros((len(idx), 3, REC_HEIGHT, width), np.float32)
            for k, i in enumerate(idx):
                rw = min(width, int(math.ceil(REC_HEIGHT * ratio[i])))
                r = cv2.resize(images[i], (rw, REC_HEIGHT)).astype(np.float32)
                batch[k, :, :, :rw] = (r / 255.0 - 0.5) / 0.5
            preds = self.rec.run(None, {self._rec_in: batch})[0]
            for k, i in enumerate(idx):
                out[i] = self._ctc(preds[k])
        return out

    def _ctc(self, pred: np.ndarray) -> Tuple[str, float]:
        """
        Greedy CTC decoding (best class per step, repeats and blanks removed), except that a
        step where the space class is likely - though not the best - counts as a space when it
        is a blank or a repeat of the previous character. The recogniser often drops the
        double space between overlay fields ("19/09/202618:02:21", or "25 56" read as "256");
        ML Kit keeps it, and the parser needs it.
        """
        ids = pred.argmax(axis=1)
        space = len(self.chars) - 1
        eff = ids.copy()
        repeat = np.zeros(len(ids), bool)
        repeat[1:] = ids[1:] == ids[:-1]
        eff[(pred[:, space] >= SPACE_PROB) & ((ids == 0) | repeat)] = space
        keep = np.ones(len(eff), bool)
        keep[1:] = eff[1:] != eff[:-1]
        keep &= eff != 0
        text = " ".join(w for w in "".join(self.chars[eff[keep]]).split(" ") if w)
        chars = keep & (eff == ids) & (ids != space)
        if not chars.any():
            return "", 0.0
        return text, float(pred[chars, ids[chars]].mean())

    def read(self, gray: np.ndarray) -> str:
        boxes = self.detect(gray)
        if not boxes:
            return ""
        crops = [gray[y0:y1, x0:x1] for x0, y0, x1, y1 in boxes]
        texts = [t for t, s in self.recognize(crops) if s >= TEXT_SCORE and t.strip()]
        return " ".join(texts)


def _reading_order(boxes):
    """Top to bottom; boxes whose vertical centres are within half a box height share a line, left to right."""
    boxes = sorted(boxes, key=lambda b: (b[1] + b[3]) / 2)
    lines: List[list] = []
    for b in boxes:
        cy, h = (b[1] + b[3]) / 2, b[3] - b[1]
        if lines and abs(cy - lines[-1][0]) <= h / 2:
            lines[-1][1].append(b)
        else:
            lines.append([cy, [b]])
    return [b for _, line in lines for b in sorted(line, key=lambda b: b[0])]


_engine: Optional[Engine] = None
_engine_lock = threading.Lock()


def engine() -> Engine:
    """One shared engine for the process (sessions are thread-safe)."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = Engine()
        return _engine


class Ocr:
    """Kept for the test-frame path: one read with the shared engine."""

    def read(self, crop: np.ndarray) -> str:
        return engine().read(crop)


class OcrPool:
    """Concurrent reads share one engine; ONNX Runtime releases the GIL while it runs."""

    def read(self, crop: np.ndarray) -> str:
        return engine().read(crop)


def default_workers() -> int:
    """Worker threads for OCR; each ONNX call uses 2 threads, so about one worker per 2 cores."""
    return max(1, min(8, (os.cpu_count() or 4) // 2))
