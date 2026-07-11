"""Perceptual dedup of extracted frames via dHash (difference hash).

fps=1 extraction produces long runs of near-identical frames (a static talking
head, a slide held on screen). This collapses each consecutive run into one
representative BEFORE the expensive vision pass, so subagents never re-read
hundreds of copies of the same screen.

Pixel-level only (Pillow + stdlib) — it removes *visual* duplicates. Meaningful
selection (slide vs head, important vs noise) stays with the vision subagents.
"""
from __future__ import annotations
from PIL import Image


def dhash(path: str, size: int = 8) -> int:
    """64-bit difference hash: grayscale → (size+1)×size → compare adjacent
    columns. Similar images → few differing bits (low Hamming distance)."""
    img = Image.open(path).convert("L").resize((size + 1, size), Image.LANCZOS)
    px = img.load()
    bits = 0
    idx = 0
    for y in range(size):
        for x in range(size):
            if px[x, y] < px[x + 1, y]:
                bits |= (1 << idx)
            idx += 1
    return bits


def hamming(a: int, b: int) -> int:
    """Number of differing bits between two hashes (0 = identical)."""
    return bin(a ^ b).count("1")


def dedup_sequential(frames: list[dict], threshold: int = 10) -> list[dict]:
    """Collapse consecutive near-duplicate frames into representatives.

    frames: [{index, timestamp, path}] in time order (from extract_fps1).
    Each new frame is compared to the FIRST frame of the current run; while it
    stays within `threshold` Hamming it joins that run. The representative's
    `path`/`index` are updated to the LAST frame of the run — a UI action or an
    animation resolves at its end (toggle applied, slide fully rendered), so the
    last frame is the informative one. `timestamp` stays at the run's START (when
    the state appeared) for captioning; `span_end_timestamp` marks its end.
    Returns [{index, timestamp, path, span_end_timestamp}] — one per unique run.

    threshold ~10/64: forgives jitter (blinking head, moving cursor) but keeps
    genuinely different screens. Lower → more frames kept (safe, vision filters
    leftovers); higher → risk of collapsing a meaningful small change (a toggle).
    """
    reps: list[dict] = []
    first_hash: int | None = None  # hash of the run's FIRST frame (drift baseline)
    for fr in frames:
        h = dhash(fr["path"])
        if first_hash is None or hamming(h, first_hash) >= threshold:
            reps.append({"index": fr["index"], "timestamp": fr["timestamp"],
                         "path": fr["path"], "span_end_timestamp": fr["timestamp"]})
            first_hash = h
        else:
            # same run → representative becomes the LAST frame (path/index),
            # timestamp stays at appearance
            reps[-1]["index"] = fr["index"]
            reps[-1]["path"] = fr["path"]
            reps[-1]["span_end_timestamp"] = fr["timestamp"]
    return reps
