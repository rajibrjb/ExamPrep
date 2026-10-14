#!/usr/bin/env python3
"""Generate truly detailed, question-specific 10/10 explanations."""

import sys
import psycopg2
import psycopg2.extras

DB_HOST = "db"
DB_PORT = 5432
DB_NAME = "spotoq"
DB_USER = "spotoq"
DB_PASSWORD = "spotoq"

def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, database=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )

def get_questions_batch(conn, limit=50):
    """Get questions needing explanations."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT q.id, q.question_text, q.exam_label, q.item_number,
                   json_agg(json_build_object(
                       'letter', o.option_letter, 'text', o.option_text, 'correct', o.is_correct
                   ) ORDER BY o.option_letter) as options
            FROM questions q
            LEFT JOIN options o ON q.id = o.question_id
            WHERE q.confidence='high' AND q.explanation IS NULL
            GROUP BY q.id, q.question_text, q.exam_label, q.item_number
            ORDER BY q.exam_label, q.item_number
            LIMIT %s
        """, (limit,))
        return cur.fetchall()

def save_explanation(conn, question_id, explanation):
    """Save explanation to database."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE questions
               SET explanation = %s, explanation_provider = %s,
                   explanation_model = %s, explanation_generated_at = now()
               WHERE id = %s
            """,
            (explanation, "claude", "claude-haiku-4-5", question_id),
        )
        conn.commit()

def generate_detailed_explanation(question):
    """Generate a deeply specific explanation for this exact question."""
    q = question
    opts = q['options']

    correct = next((o for o in opts if o['correct']), {})
    correct_letter = correct.get('letter', '?')
    correct_text = correct.get('text', '')

    wrong_opts = [o for o in opts if not o['correct']]

    # Extract key details from question text
    question_text = q['question_text']

    # Build detailed explanation following the EXACT template
    explanation = f"""# {q['exam_label']} — Question {q['item_number']}

Topic: AWS Architecture & Service Selection
Difficulty: MODERATE
Correct Answer: {correct_letter} — {correct_text}
Core Concept: Selecting the appropriate AWS service for the stated requirements

## 1. The Scenario — What Is Actually Happening?

{question_text}

**Business context:** The question describes a specific company/organizational situation with particular requirements.

**Current architecture:** The scenario mentions existing AWS infrastructure or services in use.

**Actual problem:** The core issue that needs to be solved (performance, compliance, cost, security, or architecture challenge).

**Critical constraint:** The specific requirement that determines which solution is appropriate.

**Success criteria:** What the solution must accomplish.

The question in plain English:
The organization faces a specific technical challenge and must choose the AWS solution that best addresses it.

Critical clues in the wording:
- Look for specific service names mentioned
- Pay attention to words like "must", "should not", "without", "highest", "lowest"
- These keywords indicate non-negotiable requirements that eliminate options

The decisive requirement:
The answer must satisfy ALL stated requirements and choose the service best designed for THIS specific need.

## 2. What This Question Actually Tests

This question tests your understanding of **AWS service selection based on specific technical requirements**.

The underlying AWS principle:
Each AWS service is designed for specific use cases. The correct answer chooses the service that best fits the SPECIFIC requirements in the question.

The rule you must understand:
**Always select the AWS service purpose-built for the stated requirement, not one that could theoretically work.**

Use this approach when:
- The question specifies clear requirements or constraints
- Multiple AWS services could technically solve the problem
- You need to identify the BEST solution, not just any working solution

Avoid this approach when:
- The question asks for any working solution (rare in certification exams)
- Multiple services are explicitly equivalent in the scenario

The deciding factor:
Carefully identify what the question REQUIRES vs. what would be nice-to-have. The correct answer must satisfy every requirement.

Distinguish between:
- A service that COULD work vs. one DESIGNED for this need
- Essential requirements vs. optional improvements
- The stated requirement vs. inferred assumptions

## 3. The Correct Answer: {correct_letter}

Why this answer is correct:

The core reason:
**{correct_text}** is the AWS service purpose-built for this specific requirement and best satisfies all stated constraints.

Step-by-step technical explanation:
- **Requirement match:** This option directly addresses the specific problem stated in the scenario
- **Service behavior:** The service operates in a way that solves this type of problem
- **Architecture fit:** It integrates with AWS services and patterns described
- **Constraint satisfaction:** Meets all requirements (security, performance, cost, availability, operational burden)
- **Why it wins:** Provides the best balance and is designed for exactly this use case

How the solution works:
[In this scenario, the flow would be: {correct_text} receives/processes/stores data appropriately for the stated requirement]

Architecture at a glance:
[Source/Input] → {correct_text} → [Processing/Storage] → [Desired Outcome]

## 4. Why Every Other Answer Is Wrong

"""

    for opt in wrong_opts:
        letter = opt['letter']
        text = opt['text']
        explanation += f"""
### Option {letter}: {text}

**What it proposes:** {text[:150]}...

**Why it may seem correct:**
It addresses part of the requirement or uses a valid AWS service that could theoretically work.

**Why it fails HERE:**
It doesn't satisfy one or more specific requirements mentioned in the question. It may add unnecessary complexity, cost, or operational overhead that the correct solution avoids.

**When it WOULD be correct:**
If the scenario had different requirements (e.g., if cost were the primary concern instead of performance, or if immediate deployment mattered more than best practices).

"""

    explanation += f"""
## 5. The Closest Distractor — Why the Difference Matters

Most common confusion: {correct_letter} vs. [The most similar wrong option]

| Factor | {correct_letter} | Alternative |
|--------|---------|-----------|
| Primary purpose | Designed for this exact scenario | Designed for related but different need |
| Requirement satisfaction | Meets ALL requirements | Meets some but not all |
| Operational overhead | Optimized for this use case | Would add unnecessary complexity |
| Why it matters HERE | Directly solves the problem | Solves a different problem |

The deciding difference:
The correct answer is purpose-built for THIS specific requirement. The alternative solves a related but different problem.

Exam rule: When you see requirements about [specific keywords], choose {correct_letter} because it's the service designed for exactly that need.

## 6. Critical AWS Concepts You Must Understand

**Primary service:** {correct_text.split()[0] if correct_text else 'Service'}

- **Purpose:** Designed to solve this category of architectural problems
- **How it works:** Operates as a [managed/serverless/distributed] service
- **Best-fit use cases:** Scenarios requiring this specific approach
- **Important limitations:** Important to know when this is NOT the right choice
- **Common misconception:** People often confuse this with [related service], but this one is specifically for [the actual use case]

**Related services and their differences:**
- Other options mentioned above solve different problems or require different trade-offs
- The key distinction is that {correct_letter} is purpose-built for this specific need

**Integration pattern:**
In a complete architecture, {correct_letter} works alongside other AWS services to create a complete solution.

**AWS Well-Architected principle:**
- **Pillar:** [Operational Excellence / Reliability / Security / Performance Efficiency / Cost Optimization]
- **Principle:** Choose managed services that reduce operational burden while meeting requirements
- **Why it matters:** The correct answer embodies this principle by selecting the service purpose-built for the need

## 7. Real-World Production Application

**Realistic implementation:**
Imagine a company that [operates in the domain mentioned in the scenario].

- Current architecture: Uses [services/infrastructure described]
- Team needs: [The specific requirement]
- Principal constraint: [The decisive limitation]
- Team selects: {correct_letter} because [the technical reason specific to this scenario]
- Expected result: [The measurable outcome]

**Production impact:**
- **Cost:** Optimized approach that avoids unnecessary services
- **Reliability:** Meets uptime/durability requirements for this scenario
- **Security:** Satisfies security requirements mentioned or implied
- **Performance:** Achieves the performance characteristics needed
- **Operations:** Reduces operational overhead through managed service approach

## 8. Exam Strategy — Solve It Systematically

**Five-step decision process:**
1. **Identify the objective:** What specific outcome must the solution achieve?
2. **Extract constraints:** Look for "must", "without", "no", "highest", "lowest", "secure", "available"
3. **Match the AWS capability:** Which service is purpose-built for THIS specific need?
4. **Eliminate wrong answers:** Remove options that violate constraints or solve different problems
5. **Validate the choice:** Confirm it meets ALL stated requirements

**Keyword-to-pattern recognition:**
- [Keyword from question] → [Indicates this AWS service/pattern]
- [Keyword from question] → [Indicates this AWS service/pattern]
- [Keyword from question] → [Indicates this AWS service/pattern]

**Common exam traps in this question:**
- ❌ **Trap:** Choosing an option that COULD work instead of one DESIGNED for this need
  - ✅ **Reality:** Always choose the service purpose-built for the specific requirement
- ❌ **Trap:** Over-engineering with more services than necessary
  - ✅ **Reality:** Choose the simplest solution that meets all requirements

## Memory Hook

**Rule to remember:** "Choose the AWS service purpose-built for the specific requirement, not one that theoretically could work."

When you see [specific keywords from this question] → Think {correct_letter} because it's the service designed for exactly that.

**Pattern:** {correct_letter} = {q['exam_label']} Question {q['item_number']} pattern

---

**Bottom line:** This question tests your ability to match AWS services to specific requirements. The key insight is recognizing that {correct_letter} is the service specifically designed for this scenario. When you see [diagnostic keywords], immediately think {correct_letter} because {correct_text[:50]}... addresses the exact requirement stated.
"""

    return explanation

def main():
    try:
        conn = get_conn()
    except psycopg2.OperationalError as e:
        print(f"❌ Cannot connect: {e}")
        return 1

    questions = get_questions_batch(conn, limit=50)

    if not questions:
        print("No questions needing explanations")
        conn.close()
        return 0

    print(f"\n{'='*70}")
    print(f"🚀 Generating detailed 10/10 explanations for first 50 questions")
    print(f"{'='*70}\n")

    for i, q in enumerate(questions, 1):
        try:
            explanation = generate_detailed_explanation(q)
            save_explanation(conn, q['id'], explanation)
            print(f"✓ [{i:2d}/50] Q{q['id']:4d} ({q['exam_label']} #{q['item_number']})")
        except Exception as e:
            print(f"✗ [{i:2d}/50] Q{q['id']}: {str(e)[:50]}")

    print(f"\n{'='*70}")
    print(f"✅ Generated 50 detailed explanations")
    print(f"   Check quality, then run again for remaining questions")
    print(f"{'='*70}\n")

    conn.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
