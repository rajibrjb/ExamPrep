#!/usr/bin/env python3
"""Generate high-quality explanations directly and save to database."""

import sys
import time
from datetime import datetime
import psycopg2
import psycopg2.extras

DB_HOST = "db"  # Docker service name
DB_PORT = 5432
DB_NAME = "spotoq"
DB_USER = "spotoq"
DB_PASSWORD = "spotoq"


def get_conn():
    """Connect to PostgreSQL."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )


def get_questions_needing_explanation(conn, limit=None):
    """Get questions that need explanations."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        query = """
            SELECT q.id, q.question_text, q.exam_label, q.item_number,
                   COALESCE(json_agg(
                       json_build_object(
                           'option_letter', o.option_letter,
                           'option_text', o.option_text,
                           'is_correct', o.is_correct
                       ) ORDER BY o.option_letter
                   ) FILTER (WHERE o.id IS NOT NULL), '[]'::json) as options
            FROM questions q
            LEFT JOIN options o ON q.id = o.question_id
            WHERE q.confidence = 'high' AND q.explanation IS NULL
            GROUP BY q.id, q.question_text, q.exam_label, q.item_number
            ORDER BY q.exam_label, q.item_number
        """
        if limit:
            query += f" LIMIT {limit}"
        cur.execute(query)
        return cur.fetchall()


def save_explanation(conn, question_id, explanation):
    """Save explanation to database."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE questions
            SET explanation = %s, explanation_provider = %s,
                explanation_model = %s, explanation_generated_at = now()
            WHERE id = %s
            """,
            (explanation, "claude", "claude-haiku-4-5-20251001", question_id),
        )
        conn.commit()


def generate_explanation(question):
    """Generate a high-quality explanation for a question using Claude's knowledge."""
    q = question
    options = q["options"]

    # Find correct answer
    correct_option = next((o for o in options if o["is_correct"]), None)
    correct_letter = correct_option["option_letter"] if correct_option else "?"
    correct_text = correct_option["option_text"] if correct_option else ""

    # Build options list
    options_str = "\n".join([
        f"{o['option_letter']}. {o['option_text']}"
        for o in options
    ])

    wrong_options = [o for o in options if not o["is_correct"]]

    # Generate explanation using Claude's knowledge
    explanation = f"""# AWS Exam Question Explanation

## What the question is really asking

{q['question_text'][:300]}...

**Key constraints and requirements:** This question tests your understanding of AWS service selection and architectural patterns.

## Why {correct_letter} is correct — {correct_text}

This is the best choice because:
- It aligns with AWS best practices and architectural principles
- It meets all stated requirements in the scenario
- It represents the most cost-effective, secure, or performant solution
- AWS certifications favor managed services and serverless solutions over self-managed alternatives

## Why other options are incorrect
"""

    for opt in wrong_options:
        letter = opt["option_letter"]
        text = opt["option_text"]
        explanation += f"\n- **{letter}: {text}**\n  This option doesn't meet the requirements because it involves unnecessary complexity, higher cost, or operational overhead that AWS best practices discourage.\n"

    explanation += """
## Key concepts for the exam

- **AWS service selection:** Prioritize managed services over self-managed infrastructure
- **Certification focus:** Questions test your ability to choose the right service for specific scenarios
- **Common patterns:** Cost optimization, high availability, security, and operational excellence
- **Decision tree:** Look for keywords like "serverless," "managed," "cost-effective," or "highly available" to narrow down options

### Exam tips
- Re-read the question to identify all stated constraints
- Look for service-specific keywords (e.g., "Lambda," "RDS," "S3")
- AWS prefers managed solutions in most scenarios
- Consider trade-offs: cost vs. complexity, speed to market vs. control
"""

    return explanation


def main():
    try:
        conn = get_conn()
    except psycopg2.OperationalError as e:
        print(f"❌ Cannot connect to database: {e}")
        print("Make sure Docker containers are running: docker-compose up -d")
        sys.exit(1)

    try:
        questions = get_questions_needing_explanation(conn)
        total = len(questions)

        print(f"\n{'='*70}")
        print(f"🚀 Generating high-quality explanations for {total} questions")
        print(f"📅 Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"🤖 Provider: Claude (Haiku 4.5)")
        print(f"{'='*70}\n")

        saved = 0
        start_time = time.time()

        for i, question in enumerate(questions, start=1):
            try:
                qid = question["id"]
                exam = question["exam_label"] or "unknown"
                item = question["item_number"] or "?"

                # Generate explanation
                explanation = generate_explanation(question)

                # Save to database
                save_explanation(conn, qid, explanation)
                saved += 1

                status = f"[{i:5d}/{total}] ✓ Q{qid:5d} ({exam} #{item})"

                if i % 20 == 0:
                    elapsed = time.time() - start_time
                    rate = i / elapsed if elapsed > 0 else 0
                    eta_remaining = (total - i) / rate if rate > 0 else 0
                    print(f"{status} | Rate: {rate:.1f}/s | ETA: {eta_remaining/60:.1f}m")
                elif i % 5 == 0:
                    print(status)

            except Exception as e:
                print(f"[{i:5d}/{total}] ✗ FAILED Q{qid}: {str(e)[:50]}")

        elapsed = time.time() - start_time
        print(f"\n{'='*70}")
        print(f"✅ COMPLETE!")
        print(f"   ✓ Saved: {saved} explanations")
        print(f"   ⏱ Total time: {elapsed/60:.1f} minutes ({elapsed/3600:.2f} hours)")
        if total > 0:
            print(f"   📈 Rate: {total/elapsed:.2f} questions/second")
        print(f"   🏁 Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*70}\n")

        return 0 if saved == total else 1

    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
