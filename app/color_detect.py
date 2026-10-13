import cv2
import numpy as np

from app.config import config


def _color_masks(hsv_region):
    h, s, v = cv2.split(hsv_region)
    thr = config.color_thresholds

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
    return green, red


def classify_bbox_color(original_bgr, bbox, y_pad=2):
    """Classify the dominant option-text color (green/red/other) within a bbox,
    sampled from the original (non watermark-cleaned) image so faint anti-aliased
    edges keep their true color."""
    x0, y0, x1, y1 = bbox
    h_img, w_img = original_bgr.shape[:2]
    y0 = max(0, y0 - y_pad)
    y1 = min(h_img, y1 + y_pad)
    x0 = max(0, x0)
    x1 = min(w_img, x1)
    if x1 <= x0 or y1 <= y0:
        return "other", 0, 0

    region = original_bgr[y0:y1, x0:x1]
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    green_mask, red_mask = _color_masks(hsv)
    green_count = int(np.count_nonzero(green_mask))
    red_count = int(np.count_nonzero(red_mask))

    if green_count == 0 and red_count == 0:
        return "other", green_count, red_count
    return ("green" if green_count >= red_count else "red"), green_count, red_count


def classify_options(original_bgr, options):
    """options: list of dicts with 'bbox'. Returns new list with color_detected added."""
    results = []
    for opt in options:
        color, green_count, red_count = classify_bbox_color(original_bgr, opt["bbox"])
        results.append({
            **opt,
            "color_detected": color,
            "_green_px": green_count,
            "_red_px": red_count,
        })
    return results
