#!/usr/bin/env python3
"""Generate 10/10 quality explanations using Claude and save to database."""

import os
import sys
import time
from datetime import datetime

from app.db import get_conn, get_question, save_explanation, list_question_ids_needing_explanation
from app.explain import explain_question
from app.settings import get_all_settings

SYSTEM_PROMPT = """You are an expert AWS certification tutor with 15+ years of experience.
Write exceptionally clear, engaging Markdown explanations that help candidates deeply understand AWS concepts.

Your explanations should:
1. Start with "## What the question is really asking" - summarize the core scenario and constraints
2. Have "## Why [CORRECT_LETTER] is correct" - explain the chosen answer with AWS best practices
3. Include "## Why other options are incorrect" - briefly disqualify each wrong option
4. End with "## Key concepts for the exam" - distill exam patterns and keywords
5. Use clear formatting, bullet points, and bold text for emphasis
6. Be concise but thorough - aim for 300-500 words total
7. Reference AWS services, features, and architectural patterns by name
8. Highlight exam-specific keywords and decision trees

Quality benchmark: Your explanations should be as good as the manual examples in data/explanations/"""

EXPLANATION_PROMPT_TEMPLATE = """Question: $question_text

Options:
$options_block

Write a high-quality 10/10 explanation that deeply explains why the correct answer is right and why others fail."""


def generate_quality_explanation(conn, question):
    """Generate a 10/10 quality explanation for a question."""
    try:
        # Build the prompt with the question and options
        from app.explain import build_prompt
        from app.settings import current_provider_config, get_all_settings

        settings = get_all_settings(conn)
        provider, model, api_key = current_provider_config(settings)

        # Use the built-in explain function
        from app.llm_providers import generate_text

        user_prompt = build_prompt(settings["explain_prompt_template"], question)
        raw_text = generate_text(
            provider, model,
            system=SYSTEM_PROMPT,
            user=user_prompt,
            api_key=api_key
        )
        return raw_text
    except Exception as e:
        print(f"Error generating explanation for question {question['id']}: {e}")
        raise


def main():
    # Get API key from environment
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set")
        print("Set it with: export ANTHROPIC_API_KEY=sk-...")
        sys.exit(1)

    from app.settings import set_settings

    with get_conn() as conn:
        settings = get_all_settings(conn)
        current_provider = settings.get("llm_provider", "ollama")
        current_key = settings.get("anthropic_api_key", "")

        if not current_key or current_provider != "anthropic":
            set_settings(conn, {
                "llm_provider": "anthropic",
                "anthropic_api_key": api_key,
                "anthropic_model": "claude-opus-4-8"
            })
            print("✓ Configured Claude as LLM provider")

        ids = list_question_ids_needing_explanation(conn, confidence='high')

    total = len(ids)
    print(f"\n{'='*70}")
    print(f"🚀 Generating 10/10 quality explanations for {total} questions")
    print(f"📅 Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🤖 Provider: Claude (Opus 4.8)")
    print(f"{'='*70}\n")

    saved = failed = 0
    start_time = time.time()

    for i, qid in enumerate(ids, start=1):
        try:
            with get_conn() as conn:
                question = get_question(conn, qid)
                if not question:
                    print(f"[{i:5d}/{total}] ⊗ SKIP {qid} (not found)")
                    continue

                # Generate explanation using Claude
                print(f"[{i:5d}/{total}] ⏳ Generating explanation for question {qid}...", end=" ", flush=True)
                explanation = generate_quality_explanation(conn, question)

                # Save to database
                save_explanation(conn, qid, explanation, "anthropic", "claude-opus-4-8")
                saved += 1
                print("✓ saved")

                # Progress metrics every 10
                if i % 10 == 0:
                    elapsed = time.time() - start_time
                    rate = i / elapsed if elapsed > 0 else 0
                    eta_remaining = (total - i) / rate if rate > 0 else 0
                    avg_time = elapsed / i
                    print(f"    📊 Rate: {rate:.2f}/min | Avg time: {avg_time:.1f}s | ETA: {eta_remaining/60:.1f}m")

        except Exception as e:
            failed += 1
            print(f"✗ FAILED: {str(e)[:60]}")

    elapsed = time.time() - start_time
    print(f"\n{'='*70}")
    print(f"✅ COMPLETE!")
    print(f"   ✓ Saved: {saved}")
    print(f"   ✗ Failed: {failed}")
    print(f"   ⏱ Total time: {elapsed/60:.1f} minutes ({elapsed/3600:.2f} hours)")
    print(f"   📈 Rate: {total/elapsed:.2f} questions/second")
    print(f"   🏁 Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
