#!/usr/bin/env python3
"""Load hand-authored explanation markdown files into the question bank.

Each file is data/explanations/<question_id>.md and its full contents become
that question's explanation, attributed to the model that actually wrote it
(claude-opus-4.8 via the Claude Code CLI -- real provenance, unlike the earlier
mock generate_explanations.py which fabricated "claude-sonnet-5" labels).

Idempotent: re-running only loads files whose content differs from what's
already stored, so a batch can be re-run safely.

Usage (inside the app container):
    python3 scripts/load_explanations.py            # load all pending files
    python3 scripts/load_explanations.py 5 12 37    # load only these ids
"""
import sys
from pathlib import Path

from app.db import get_conn, save_explanation

EXPL_DIR = Path("/data/explanations")
PROVIDER = "claude"
MODEL = "claude-opus-4.8"


def main(argv):
    only_ids = {int(a) for a in argv} if argv else None
    files = sorted(EXPL_DIR.glob("*.md"), key=lambda p: int(p.stem))
    loaded = skipped = 0
    with get_conn() as conn:
        for f in files:
            qid = int(f.stem)
            if only_ids is not None and qid not in only_ids:
                continue
            text = f.read_text().strip()
            if not text:
                continue
            with conn.cursor() as cur:
                cur.execute("SELECT explanation FROM questions WHERE id = %s", (qid,))
                row = cur.fetchone()
                if row is None:
                    print(f"  [skip] id {qid}: no such question")
                    continue
                if row[0] == text:
                    skipped += 1
                    continue
            save_explanation(conn, qid, text, PROVIDER, MODEL)
            loaded += 1
            print(f"  [ok] id {qid}: loaded ({len(text)} chars)")
    print(f"done: {loaded} loaded, {skipped} unchanged")


if __name__ == "__main__":
    main(sys.argv[1:])
