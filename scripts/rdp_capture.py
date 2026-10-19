#!/usr/bin/env python3
"""
Verified one-by-one capture+ingest of SPOTO VCE Player questions over RDP
(macOS "Windows App" client) into SpotoQ.

Per-question flow (nothing advances until each gate passes):

  1. REVEAL GATE -- make sure the answer is actually revealed on screen
     (colored options / "Hide Answer" showing). If the current question is
     hidden, press Space (the focused Next button's first activation reveals);
     re-check; escalate retries (re-click Next, re-activate the app via its
     Dock icon) until the colors appear. The loop NEVER advances past a
     question whose answer was not visible on the captured image.
  2. CAPTURE -- screenshot straight into incoming/_staging.png.
  3. INGEST+VERIFY -- POST to the running SpotoQ app's /process_file, which
     OCRs the image, renames it to item_NNNN.png using the item number read
     from the image's own header ("Item N of M (Exam A, QN)"), writes the
     question+options+answer to Postgres, and returns a verification summary
     (option count, correct letters, confidence). Captures whose answer
     couldn't be extracted stay in the DB flagged needs_review.
  4. ADVANCE -- press Space again and verify the header actually changed.

Usage:
    caffeinate -d -i -s python3 scripts/rdp_capture.py --count 5

Requires: the Docker stack running (SpotoQ app at localhost:5050), macOS
Accessibility + Screen Recording permissions, `cliclick` (brew).

Known RDP/macOS quirks this script works around (hard-won, do not "simplify"):
  - Mouse clicks often focus the Next button WITHOUT firing it; Space on the
    focused button is reliable. So: click once to focus, drive with Space.
  - `tell app "Windows App" to activate` does NOT switch macOS Spaces; clicking
    the Dock icon does.
  - RDP render lag: a short settle delay makes change-detection see "no effect"
    and double-fire, silently skipping questions. SETTLE_DELAY must stay
    generous and retries must re-CHECK before re-pressing.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INCOMING_DIR = REPO_ROOT / "incoming"
SCRATCH_DIR = REPO_ROOT / "data" / "rdp_capture_scratch"
STAGING_NAME = "_staging.png"
APP_BASE_URL = "http://localhost:5050"

DISPLAY_INDEX = 1
WINDOWS_APP_PROCESS = "Windows App"
SPACE_KEY_CODE = 49

# Logical-point coordinates of the Next button (built-in Retina display, 2x
# scale: 3024x1964 px = 1512x982 pt; Windows App maximized). Pixel (272,1845)/2.
NEXT_CLICK_POINT = (136, 922)

# Pixel regions (screencapture coordinates on the 3024x1964 capture).
HEADER_REGION = (0, 180, 1000, 250)     # "Item N of 1350 (Exam A, QN)" line
OPTION_A_REGION = (0, 360, 1400, 560)   # options area, for is_revealed()

SETTLE_DELAY = 2.5
REVEAL_MAX_ATTEMPTS = 6
ADVANCE_MAX_ATTEMPTS = 5


def run(cmd):
    subprocess.run(cmd, check=True)


def screencapture(path):
    run(["screencapture", "-x", "-D", str(DISPLAY_INDEX), str(path)])


def send_space():
    run([
        "osascript", "-e",
        f'tell application "System Events" to tell process "{WINDOWS_APP_PROCESS}" '
        f'to key code {SPACE_KEY_CODE}',
    ])


def activate_app():
    # Dock click, NOT `activate` -- see module docstring.
    run([
        "osascript", "-e",
        'tell application "System Events" to tell process "Dock" to '
        f'click (first UI element of list 1 whose name is "{WINDOWS_APP_PROCESS}")',
    ])


def click_next_button():
    x, y = NEXT_CLICK_POINT
    run(["cliclick", f"c:{x},{y}"])


def region_bytes(image_path, region):
    from PIL import Image
    return Image.open(image_path).crop(region).tobytes()


def is_revealed(image_path):
    """True when the options area contains enough strongly red/green pixels to
    be colored option text (revealed answer) rather than black text + pale
    watermark. Minimum-count threshold keeps watermark pastels from false-
    positiving; thresholds sampled from real option text colors."""
    from PIL import Image
    img = Image.open(image_path).crop(OPTION_A_REGION).convert("RGB")
    matches = 0
    for r, g, b in img.getdata():
        reddish = r > 200 and g < 90 and b < 110
        greenish = g > 100 and r < 90 and b < 110
        if reddish or greenish:
            matches += 1
            if matches >= 40:
                return True
    return False


def ensure_revealed():
    """Gate 1: drive the player until the current question's answer is visibly
    revealed. Checks BEFORE pressing so an already-revealed question is never
    toggled, and re-checks after every press so render lag can't double-fire."""
    check_path = SCRATCH_DIR / "reveal_check.png"
    for attempt in range(1, REVEAL_MAX_ATTEMPTS + 1):
        screencapture(check_path)
        if is_revealed(check_path):
            return True
        if attempt == 3:
            click_next_button()
            time.sleep(0.3)
        elif attempt >= 4:
            activate_app()
            time.sleep(0.8)
            click_next_button()
            time.sleep(0.3)
        send_space()
        time.sleep(SETTLE_DELAY)
    # Final check after the last press.
    screencapture(check_path)
    return is_revealed(check_path)


def ingest_staging():
    """Gate 2: hand the captured file to the SpotoQ app for OCR + DB insert;
    returns its verification summary."""
    data = urllib.parse.urlencode({"filename": STAGING_NAME}).encode()
    req = urllib.request.Request(f"{APP_BASE_URL}/process_file", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode())


def advance():
    """Gate 3: press Space to move to the next question and verify the header
    text actually changed (re-checking before any retry press)."""
    before_path = SCRATCH_DIR / "adv_before.png"
    after_path = SCRATCH_DIR / "adv_after.png"
    screencapture(before_path)
    before = region_bytes(before_path, HEADER_REGION)

    for attempt in range(1, ADVANCE_MAX_ATTEMPTS + 1):
        if attempt == 2:
            click_next_button()
            time.sleep(0.3)
        elif attempt >= 3:
            activate_app()
            time.sleep(0.8)
            click_next_button()
            time.sleep(0.3)
        send_space()
        time.sleep(SETTLE_DELAY)
        screencapture(after_path)
        if region_bytes(after_path, HEADER_REGION) != before:
            return True
        print(f"  [advance retry {attempt}/{ADVANCE_MAX_ATTEMPTS}] header unchanged...")
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, required=True,
                        help="How many questions to capture+ingest this run")
    args = parser.parse_args()

    INCOMING_DIR.mkdir(exist_ok=True)
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    staging_path = INCOMING_DIR / STAGING_NAME

    activate_app()
    time.sleep(0.8)
    click_next_button()
    time.sleep(0.5)

    stats = {"ok": 0, "needs_review": 0, "duplicate": 0, "conflict": 0, "error": 0, "unrevealed": 0}
    for i in range(1, args.count + 1):
        revealed = ensure_revealed()
        if not revealed:
            # Still capture + ingest for the review queue (user decision), but
            # record it; the ingest will flag it as having no answer signal.
            stats["unrevealed"] += 1
            print(f"[{i}/{args.count}] WARNING: answer never became visible -- capturing anyway for review")

        screencapture(staging_path)
        result = ingest_staging()

        status = result.get("status")
        item = result.get("item_number")
        review = result.get("needs_review")
        if status in ("duplicate", "conflict", "error"):
            stats[status] += 1
        elif review:
            stats["needs_review"] += 1
        else:
            stats["ok"] += 1

        print(f"[{i}/{args.count}] item={item} file={result.get('file')} "
              f"options={result.get('num_options')} correct={result.get('correct_letters')} "
              f"confidence={result.get('confidence')} status={status}"
              + (f" REVIEW: {result.get('reasons')}" if review else ""))

        # Always advance (even after the last item) so a subsequent run starts
        # on a fresh, hidden question.
        if not advance():
            print("ERROR: could not advance to the next question -- stopping. "
                  "Check the remote session (end of exam, popup, or lost focus).",
                  file=sys.stderr)
            break

    print("\n=== stats ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
