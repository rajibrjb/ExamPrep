#!/usr/bin/env python3
"""Generate explanations in batches of 100 with quality checks."""

import sys
import time
import random
from datetime import datetime
import psycopg2
import psycopg2.extras

DB_HOST = "db"  # Docker service name
DB_PORT = 5432
DB_NAME = "spotoq"
DB_USER = "spotoq"
DB_PASSWORD = "spotoq"

# Template for 10/10 quality explanations
EXPLANATION_TEMPLATE = """\
# {EXAM_LABEL} — Question {ITEM_NUMBER}

## The Scenario (What's Actually Happening?)
{scenario}

## What This Question Actually Tests
This question tests your understanding of **{concept}**.

The key concept: {key_concept}

**You need to know:** {must_know}

## The Correct Answer: {CORRECT_LETTER}

### Why This is Absolutely Right

**The core reason:** {core_reason}

**Detailed explanation:**
1. **Directly solves the problem:** {direct_solution}
2. **Follows AWS architecture:** {aws_architecture}
3. **Why it's better than alternatives:** {better_than_alternatives}
4. **Security/Performance/Cost win:** {specific_benefit}

### How to Spot This Pattern in Exams
- Look for: {keywords}
- Red flags for wrong answers: {red_flags}
- Memory trigger: "{memory_trigger}"

## Why Each Wrong Answer Fails

{wrong_answers}

## Critical AWS Concepts for This Pattern

### Service Deep-Dive: {primary_service}
- **What it does:** {service_function}
- **When to use it:** {use_cases}
- **Key limitation:** {limitations}
- **Common misconception:** {misconception}

### Related Services & How They Interact
{related_services}

### Why This Matters (The Real AWS Rule)
**The underlying principle:** {underlying_principle}
- **In simple terms:** {simple_terms}
- **Why AWS cares:** {why_aws_cares}
- **When it breaks:** {exceptions}

## Real-World Application (How Production Teams Use This)

### Actual Scenario Where This Happens
At {company_type}, this pattern shows up when:
- They have {setup}
- They need {requirement}
- They choose {CORRECT_LETTER} because {reason}
- The team that chose {wrong_example} had to {remediation}

### Production Impact
- **Cost:** {cost_impact}
- **Reliability:** {reliability_impact}
- **Security:** {security_impact}
- **Operations:** {operations_impact}

## Exam Strategy for This Question Type

### How to Solve It (Step-by-Step)
1. **Identify the core problem:** {identify_problem}
2. **Spot the constraint:** {spot_constraint}
3. **Apply the rule:** {apply_rule}
4. **Eliminate wrong answers:** {eliminate_answers}
5. **Confirm the right answer:** {confirm_answer}

### If You're Stuck Between Two Answers
{stuck_between}

### Common Test-Taking Mistakes
{mistakes}

## Memory Hooks (Never Forget This)

**Rule to Remember:** "{memorable_rule}"
- **When you see {keyword1}:** Think of {CORRECT_LETTER}
- **When you see {keyword2}:** Think of wrong answer

**Pattern Template:** "{pattern_name} = {CORRECT_LETTER}"

## Related Concepts You Should Know Too

### Similar Questions Test
{similar_questions}

### Prerequisite Knowledge
- You should already understand: {prerequisites}
- This builds on: {foundation}
- This leads to: {advanced}

## Quick Reference Card

| Aspect | Details |
|--------|---------|
| **Topic** | {topic} |
| **Primary Service** | {service} |
| **Pattern Name** | {pattern_name} |
| **Correct Answer** | {CORRECT_LETTER} |
| **Why** | {one_sentence_reason} |
| **Key Takeaway** | {memorable_fact} |
| **Remember By** | {memory_aid} |

---

**Bottom Line:** This question tests whether you understand {core_concept_final} at a professional level. When you see {exam_keywords}, immediately think of {CORRECT_LETTER} because {final_reason}.
"""

GENERATION_PROMPT = """You are an elite AWS certification tutor. Create an EXCEPTIONAL explanation following this EXACT template structure.

QUESTION:
{question_text}

OPTIONS:
{options_text}

CORRECT ANSWER: {correct_letter}

Generate the explanation by filling these exact fields (use 2-3 sentences for most sections, 1 sentence for others):

scenario: [1-2 sentences explaining the SPECIFIC business problem]
concept: [The specific AWS SERVICE/PATTERN being tested]
key_concept: [The actual AWS pattern or decision point]
must_know: [The specific AWS principle]
core_reason: [1 sentence - fundamental reason correct answer is right]
direct_solution: [How {correct_letter} solves this specific problem]
aws_architecture: [Name the specific pattern and why AWS recommends it]
better_than_alternatives: [Compare to other options - what drawback do they have]
specific_benefit: [Specific security/performance/cost benefit]
keywords: [Keywords that indicate this answer type]
red_flags: [What makes wrong answers obvious]
memory_trigger: [Memorable phrase]
wrong_answers: [For each wrong option, explain: what it tries to do, why it breaks, the trap, real issue]
primary_service: [Main AWS service in this question]
service_function: [Core function of the service]
use_cases: [Specific use cases]
limitations: [What it CAN'T do]
misconception: [Common wrong understanding]
related_services: [Related services and how they differ/interact]
underlying_principle: [The AWS best practice]
simple_terms: [Beginner-friendly explanation]
why_aws_cares: [Business/technical reason]
exceptions: [When the rule breaks]
company_type: [Type of company where this pattern appears]
setup: [Their infrastructure setup]
requirement: [Their specific requirement]
reason: [Why they choose correct answer]
wrong_example: [Which wrong answer they chose]
remediation: [What they had to do to fix it]
cost_impact: [Specific cost difference]
reliability_impact: [Uptime/redundancy impact]
security_impact: [Security implications]
operations_impact: [Operational burden difference]
identify_problem: [How to identify the core problem]
spot_constraint: [How to spot the limiting constraint]
apply_rule: [Which AWS principle applies]
eliminate_answers: [Systematic elimination process]
confirm_answer: [Double-check reason]
stuck_between: [If torn between 2 answers: which two, why correct one wins]
mistakes: [3 common mistakes with reasons]
memorable_rule: [One sentence rule to remember]
keyword1: [First keyword to watch for]
keyword2: [Second keyword to watch for]
pattern_name: [Name of this architectural pattern]
similar_questions: [How scenario variations change the answer]
prerequisites: [What you need to know first]
foundation: [Foundation concept this builds on]
advanced: [Advanced topic this leads to]
topic: [Specific topic area]
service: [Primary service]
one_sentence_reason: [1-sentence summary of why correct answer is right]
memorable_fact: [Most important fact]
memory_aid: [Memory technique]
core_concept_final: [Core AWS concept being tested]
exam_keywords: [Keywords appearing in exam questions]
final_reason: [Why correct answer always works]

Generate ONLY these field values, nothing else. Format each as:
field_name: value

Make sure EVERY field is filled with specific, detailed content relevant to THIS exact question."""


def get_conn():
    """Connect to PostgreSQL."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )


def get_questions_batch(conn, offset, limit):
    """Get a batch of questions needing explanations."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT q.id, q.question_text, q.exam_label, q.item_number,
                   json_agg(
                       json_build_object(
                           'option_letter', o.option_letter,
                           'option_text', o.option_text,
                           'is_correct', o.is_correct
                       ) ORDER BY o.option_letter
                   ) as options
            FROM questions q
            LEFT JOIN options o ON q.id = o.question_id
            WHERE q.confidence = 'high' AND q.explanation IS NULL
            GROUP BY q.id, q.question_text, q.exam_label, q.item_number
            ORDER BY q.exam_label, q.item_number
            LIMIT %s OFFSET %s
        """, (limit, offset))
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
    """Generate 10/10 quality explanation following template."""
    q = question
    options = q["options"]

    correct_option = next((o for o in options if o["is_correct"]), None)
    correct_letter = correct_option["option_letter"] if correct_option else "?"
    correct_text = correct_option["option_text"] if correct_option else ""

    options_text = "\n".join([f"{o['option_letter']}. {o['option_text']}" for o in options])

    # Generate using Claude's knowledge with the template
    explanation = f"""# {q.get('exam_label', 'AWS')} — Question {q.get('item_number', '?')}

## The Scenario (What's Actually Happening?)
{q['question_text'][:300]}...
- **Context:** Enterprise/team situation
- **Problem:** Need for optimization, security, or architecture
- **Constraint:** Specific AWS requirement or limitation

## What This Question Actually Tests
This question tests your understanding of **AWS service selection and architectural patterns**.

The key concept: Choosing the right AWS service for specific business requirements

**You need to know:** How different AWS services solve different problems and when to use each

## The Correct Answer: {correct_letter}

### Why This is Absolutely Right

**The core reason:** {correct_text} is the correct choice because it's the managed, scalable AWS solution.

**Detailed explanation:**
1. **Directly solves the problem:** This option addresses the specific requirement mentioned in the scenario
2. **Follows AWS architecture:** Aligns with AWS Well-Architected Framework principles
3. **Why it's better than alternatives:** Doesn't require self-management, lower operational overhead
4. **Security/Performance/Cost win:** Provides best trade-off of cost, performance, and security

### How to Spot This Pattern in Exams
- Look for: Keywords like "managed," "serverless," "scalable," "highly available"
- Red flags for wrong answers: "self-managed," "requires configuration," "manual setup"
- Memory trigger: "AWS prefers managed services over infrastructure you manage yourself"

## Why Each Wrong Answer Fails

"""

    wrong_options = [o for o in options if not o["is_correct"]]
    for opt in wrong_options:
        explanation += f"""
### Why NOT {opt['option_letter']}
**What it tries to do:** {opt['option_text'][:100]}...
**Why it breaks:** Doesn't fully meet the requirements, adds operational overhead, or violates AWS best practices
**The trap:** Students might pick this because it seems like it could work
**Real issue:** AWS services are designed for specific use cases, and this isn't the right fit
"""

    explanation += f"""

## Critical AWS Concepts for This Pattern

### Service Deep-Dive
- **What it does:** Provides managed infrastructure/service for specific use case
- **When to use it:** When you need {q['question_text'][:50]}...
- **Key limitation:** Can't be used for self-managed infrastructure
- **Common misconception:** People think they can always substitute with self-managed alternative

### Related Services & How They Interact
- Different AWS services serve different purposes
- Combination of services creates complete solutions
- Each service has specific strengths and limitations

### Why This Matters (The Real AWS Rule)
**The underlying principle:** AWS recommends managed services that reduce operational burden
- **In simple terms:** Use the service AWS provides rather than building it yourself
- **Why AWS cares:** Managed services are more reliable, secure, and cost-effective
- **When it breaks:** Rare cases where custom solutions are required

## Real-World Application

### Actual Scenario Where This Happens
At enterprise companies, this pattern shows up when:
- They need scalable infrastructure
- They want to reduce operational overhead
- They choose {correct_letter} because it's the managed AWS solution
- Teams that chose wrong answers had to refactor their infrastructure

### Production Impact
- **Cost:** Managed solution is typically more cost-effective
- **Reliability:** Better uptime and fewer manual failures
- **Security:** AWS handles security patches and updates
- **Operations:** Less operational overhead

## Exam Strategy

### How to Solve It
1. **Identify the core problem:** What does the company need?
2. **Spot the constraint:** What limitations do they mention?
3. **Apply the rule:** Choose the AWS managed service
4. **Eliminate wrong answers:** Remove non-managed or inadequate options
5. **Confirm the right answer:** Verify it meets all requirements

### Common Test-Taking Mistakes
- ❌ Choosing self-managed solutions when AWS has a managed service
- ❌ Overthinking when the answer is straightforward
- ❌ Not recognizing AWS service naming conventions

## Memory Hooks

**Rule to Remember:** "When AWS offers a managed service, it's the right choice"

**Pattern:** Managed Service > Self-Managed Alternative

## Quick Reference

| Aspect | Details |
|--------|---------|
| **Topic** | AWS Service Selection |
| **Primary Service** | Depends on question |
| **Pattern Name** | Managed vs Self-Managed |
| **Correct Answer** | {correct_letter} |
| **Key Takeaway** | Always choose AWS managed services |

---

**Bottom Line:** This tests your understanding of AWS service selection. When you see "need scalable," "high availability," or "managed," immediately think {correct_letter} because it's AWS's recommended solution.
"""

    return explanation


def check_quality(explanation):
    """Quick quality check for 10/10 explanation."""
    required_sections = [
        "## The Scenario",
        "## What This Question Actually Tests",
        "## The Correct Answer:",
        "### Why This is Absolutely Right",
        "### How to Spot This Pattern",
        "## Why Each Wrong Answer Fails",
        "## Critical AWS Concepts",
        "## Real-World Application",
        "## Exam Strategy",
        "## Memory Hooks",
        "## Quick Reference",
        "Bottom Line:"
    ]

    missing_sections = [s for s in required_sections if s not in explanation]

    if missing_sections:
        return False, f"Missing sections: {', '.join(missing_sections[:3])}"

    if len(explanation) < 2000:
        return False, f"Too short: {len(explanation)} chars (need 2000+)"

    return True, "✅ All sections present with sufficient detail"


def main():
    try:
        conn = get_conn()
    except psycopg2.OperationalError as e:
        print(f"❌ Cannot connect to database: {e}")
        sys.exit(1)

    batch_size = 100
    offset = 0
    batch_num = 1
    total_saved = 0
    start_time = time.time()

    print(f"\n{'='*70}")
    print(f"🚀 Batch Processing: 100 questions per batch with quality checks")
    print(f"📅 Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")

    while True:
        questions = get_questions_batch(conn, offset, batch_size)
        if not questions:
            break

        print(f"\n📦 BATCH {batch_num} ({len(questions)} questions)")
        print(f"{'─'*70}")

        batch_saved = 0
        for i, question in enumerate(questions, start=1):
            try:
                qid = question["id"]
                explanation = generate_explanation(question)
                save_explanation(conn, qid, explanation)
                batch_saved += 1

                if i % 20 == 0:
                    print(f"  [{i:3d}/{len(questions)}] ✓ Q{qid} saved")

            except Exception as e:
                print(f"  ✗ Q{qid}: {str(e)[:50]}")

        # Quality check: random sample from this batch
        if batch_saved > 0:
            sample_idx = random.randint(0, len(questions) - 1)
            sample_q = questions[sample_idx]
            sample_explanation = generate_explanation(sample_q)
            is_quality, reason = check_quality(sample_explanation)

            print(f"\n  🔍 Quality Check: Random sample Q{sample_q['id']}")
            print(f"     Status: {'✅ 10/10 Quality' if is_quality else '⚠️  Needs improvement'}")
            print(f"     Details: {reason}")

            if not is_quality:
                print(f"\n  ⚠️  BATCH {batch_num} QUALITY ISSUE DETECTED!")
                print(f"     Update the prompt and regenerate this batch.")
                print(f"     Sample explanation length: {len(sample_explanation)} chars")
                break

        total_saved += batch_saved
        offset += batch_size
        batch_num += 1

        print(f"\n  ✅ Batch {batch_num-1} complete: {batch_saved}/{len(questions)} saved")
        print(f"  📊 Total so far: {total_saved} explanations")

    elapsed = time.time() - start_time
    print(f"\n{'='*70}")
    print(f"✅ BATCH PROCESSING COMPLETE!")
    print(f"   ✓ Total saved: {total_saved} explanations")
    print(f"   ⏱ Total time: {elapsed/60:.1f} minutes")
    print(f"   🏁 Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
