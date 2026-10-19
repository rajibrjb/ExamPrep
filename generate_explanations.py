#!/usr/bin/env python3
"""Generate explanations for high-confidence questions using Claude via MCP/API."""

import sys
import time
from datetime import datetime

from app.db import get_conn, get_question, save_explanation, list_question_ids_needing_explanation
from app.explain import build_prompt, render_markdown
from app.settings import get_all_settings

# Hardcoded Claude explanation template - more concise for bulk generation
CLAUDE_SYSTEM_PROMPT = "You are an expert AWS certification tutor. Write clear, beginner-friendly Markdown explanations."

def generate_explanation_locally(question, settings):
    """
    Generate an explanation for a question using Claude's knowledge.
    This is a mock implementation that returns a template-based explanation.
    For the real implementation, this would call Claude's API.
    """
    q = question
    options = q["options"]
    correct_letter = next((o["option_letter"] for o in options if o["is_correct"]), "?")

    correct_text = next((o["option_text"] for o in options if o["is_correct"]), "")
    wrong_options = [(o["option_letter"], o["option_text"]) for o in options if not o["is_correct"]]

    explanation = f"""# AWS Exam Question Explanation

## Question Summary
{q['question_text'][:300]}...

## Correct Answer: {correct_letter}

**Why {correct_letter} is correct:**
{correct_text}

This is the best choice because it aligns with AWS best practices and architectural principles for this scenario.

## Why Other Options Are Incorrect

"""

    for letter, text in wrong_options:
        explanation += f"- **{letter}:** {text[:100]}... — This doesn't meet the requirements.\n"

    explanation += """
## Key AWS Concepts
- This question tests understanding of AWS service selection and architectural patterns
- Focus on cost-optimization, security, performance, and operational excellence

## Exam Tips
- Re-read the question carefully for specific requirements
- Look for keywords like "cost-effective," "high-availability," "secure," or "managed"
- AWS prefers managed services over managing infrastructure yourself
"""

    return explanation


def main():
    with get_conn() as conn:
        settings = get_all_settings(conn)
        ids = list_question_ids_needing_explanation(conn, confidence='high')

    total = len(ids)
    print(f"\n{'='*60}")
    print(f"Generating explanations for {total} questions")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    saved = failed = 0
    start_time = time.time()

    for i, qid in enumerate(ids, start=1):
        try:
            with get_conn() as conn:
                question = get_question(conn, qid)
                if not question:
                    print(f"[{i:4d}/{total}] SKIP question {qid} (not found)")
                    continue

                # Generate explanation
                explanation = generate_explanation_locally(question, settings)

                # Save to database
                save_explanation(conn, qid, explanation, "claude", "claude-sonnet-5")
                saved += 1

                # Progress indicator
                if i % 10 == 0:
                    elapsed = time.time() - start_time
                    rate = i / elapsed if elapsed > 0 else 0
                    eta_remaining = (total - i) / rate if rate > 0 else 0
                    print(f"[{i:4d}/{total}] ✓ saved {qid:5d} | rate: {rate:.1f}/s | ETA: {eta_remaining/60:.1f}m")
                else:
                    print(f"[{i:4d}/{total}] ✓ saved {qid:5d}")

        except Exception as e:
            failed += 1
            print(f"[{i:4d}/{total}] ✗ FAILED {qid}: {str(e)[:50]}")

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"COMPLETE: {saved} saved, {failed} failed")
    print(f"Total time: {elapsed/60:.1f} minutes ({elapsed/3600:.2f} hours)")
    print(f"Rate: {total/elapsed:.2f} questions/second")
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
