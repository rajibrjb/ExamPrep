import os

import cv2
import numpy as np

from app.config import config


def _text_mask(hsv):
    """Pixels that look like real (opaque) text rather than the pale repeating watermark."""
    h, s, v = cv2.split(hsv)
    thr = config.color_thresholds

    dark = v < thr["text_max_value"]

    green = (
        (h >= thr["green"]["h_min"]) & (h <= thr["green"]["h_max"])
        & (s >= thr["green"]["s_min"])
        & (v >= thr["green"]["v_min"]) & (v <= thr["green"]["v_max"])
    )

    red = (
        ((h <= thr["red"]["h_max_low"]) | (h >= thr["red"]["h_min_high"]))
        & (s >= thr["red"]["s_min"])
        & (v >= thr["red"]["v_min"])
    )

    return dark | green | red


def clean_for_ocr(image_bgr):
    """Suppress the pale repeating watermark, keep black/red/green text crisp on white."""
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    mask = _text_mask(hsv)

    cleaned = np.full_like(image_bgr, 255)
    cleaned[mask] = image_bgr[mask]
    return cleaned


def crop_chrome(image_bgr):
    """Drop the OS title bar / taskbar strips that aren't part of the question content."""
    h, w = image_bgr.shape[:2]
    top = int(h * 0.02)
    bottom = int(h * 0.97)
    return image_bgr[top:bottom, :]


def load_and_prepare(file_path, cleaned_out_path=None):
    image_bgr = cv2.imread(file_path)
    if image_bgr is None:
        raise ValueError(f"Could not read image: {file_path}")

    cropped = crop_chrome(image_bgr)
    cleaned = clean_for_ocr(cropped)

    if cleaned_out_path:
        out_dir = os.path.dirname(cleaned_out_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        cv2.imwrite(cleaned_out_path, cleaned)

    return cropped, cleaned
