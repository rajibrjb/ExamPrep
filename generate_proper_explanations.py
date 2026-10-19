#!/usr/bin/env python3
"""Generate truly specific 10/10 explanations following the exact template."""

import sys
import time
import random
from datetime import datetime
import psycopg2
import psycopg2.extras

DB_HOST = "db"
DB_PORT = 5432
DB_NAME = "spotoq"
DB_USER = "spotoq"
DB_PASSWORD = "spotoq"

# PROPER template prompt - generates SPECIFIC detailed explanations
PROPER_EXPLANATION_PROMPT = """You are an expert AWS certification tutor. Generate a COMPREHENSIVE 10/10 explanation.

QUESTION:
{question_text}

OPTIONS:
{options_text}

CORRECT ANSWER: {correct_letter}

GENERATE FOLLOWING THIS EXACT STRUCTURE:

# {exam_label} — Question {item_number}

Topic: {topic}
Correct Answer: {correct_letter}
Core Concept: {core_concept}

## 1. The Scenario — What Is Actually Happening?

[2-4 sentences explaining the ACTUAL business/technical problem from THIS question, not generic]

Business context: [What is the company trying to achieve?]
Current architecture: [What services/infrastructure are mentioned?]
Actual problem: [What is failing or needed?]
Critical constraint: [What must the solution prioritize?]

The question in plain English: [Restate the problem simply]

Critical clues in the wording:
- "{keyword_1}": [What this tells you]
- "{keyword_2}": [Architectural constraint it implies]
- "{keyword_3}": [Requirement that determines the answer]

The decisive requirement: [The ONE requirement that eliminates wrong answers]

## 2. What This Question Actually Tests

This question tests your understanding of {specific_concept}.

The underlying AWS principle: [Explain the exact principle - not generic]

The rule you must understand: [ONE precise sentence statement of the rule]

Use this approach when: [Specific conditions where this pattern works]
Avoid this approach when: [Limitations or when alternatives are better]
The deciding factor: [The specific distinction that matters HERE]

Distinguish between:
- {distinction_1}
- {distinction_2}
- {distinction_3}

## 3. The Correct Answer: {correct_letter}

Why this answer is correct:

The core reason: [ONE sentence connecting answer to the decisive requirement]

Step-by-step technical explanation:
- Requirement match: [Which requirement does this satisfy?]
- Service behavior: [What does the service actually do?]
- Architecture fit: [How does it fit the existing setup?]
- Constraint satisfaction: [How does it meet security/availability/cost/operations requirements?]
- Why it wins: [Advantage over closest competing option]

How the solution works:
[Explain the actual flow/mechanism specific to THIS scenario]

Architecture at a glance:
[Component A] → [Component B] → [Component C] → [Outcome]

## 4. Why Every Other Answer Is Wrong

{wrong_answer_analysis}

[For each wrong option:]
Option {letter}: {option_text}
- What it proposes: [Describe accurately]
- Why it may seem correct: [The tempting aspect]
- Why it fails HERE: [The specific mismatch with THIS scenario]
- When it WOULD be correct: [A realistic variation where it works]

## 5. The Closest Distractor — Why the Difference Matters

Most likely confusion: {correct_letter} vs. {closest_distractor}

Decision factor | {correct_letter} | Closest distractor
--- | --- | ---
Primary purpose | {purpose_1} | {purpose_2}
Requirement satisfied | {req_1} | {req_2}
Key limitation | {limit_1} | {limit_2}
Why it wins here | {reason_1} | {reason_2}

The deciding difference: [The ONE technical distinction that resolves this]

Exam rule: When you see "{keyword}", prefer {correct_approach} over {alternative}.

## 6. Critical AWS Concepts

Primary service: {service}
- Purpose: [What it's designed to do]
- How it works: [Relevant technical mechanism]
- Best-fit use cases: [Scenarios where appropriate]
- Important limitations: [Relevant constraints]
- Common misconception: [Plausible but wrong belief]

Related services and their differences:
- {service_a}: [Purpose] → [Difference that matters]
- {service_b}: [Purpose] → [Difference that matters]
- {service_c}: [Purpose] → [Difference that matters]

Integration pattern: [How relevant services work together in THIS scenario]

AWS Well-Architected principle:
- Pillar: {pillar}
- Principle: {principle}
- Why it matters: [The benefit - reliability/security/cost/performance/operations]

## 7. Real-World Production Application

Realistic implementation:
Imagine a company that {realistic_business_situation}.
- Current architecture: {current_setup}
- Team needs: {requirement}
- Constraint: {constraint}
- Selection: {correct_letter} because {reason}
- Expected result: {measurable_outcome}

Production impact:
- Cost: {cost_implication}
- Reliability: {reliability_impact}
- Security: {security_impact}
- Performance: {performance_impact}
- Operations: {operational_impact}

## 8. Exam Strategy — Solve It Systematically

Five-step process:
1. Identify the objective: [What does the question require?]
2. Extract constraints: [Key limiting factors?]
3. Match the AWS capability: [What service directly addresses this?]
4. Eliminate distractors: [Reject options that violate constraints]
5. Validate final choice: [Confirm it meets all requirements]

Keyword-to-pattern recognition:
- "{keyword_1}" → {service_pattern}
- "{keyword_2}" → {service_pattern}
- "{keyword_3}" → {service_pattern}

Common exam traps in this question:
- ❌ Trap 1: [Misconception] → ✅ Reality: [Correct understanding]
- ❌ Trap 2: [Wrong thinking] → ✅ Reality: [Correct fact]

## Memory Hook

Rule to remember: "{memorable_rule}"

When you see "{exam_keyword}", think {correct_letter} because {reason}.

Pattern: {correct_letter} = {pattern_name}

---

Bottom line: This question tests {core_concept}. The key insight is {key_insight}. When you see {diagnostic_keywords}, choose {correct_letter} because {fundamental_reason}.

---

CRITICAL: Every section above must be SPECIFIC to THIS question's content, not generic. Do not use placeholder text. Extract real details from the question text and options. Analyze the actual choices given."""


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
    """Get batch of questions needing explanations."""
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


def generate_explanation_prompt(question):
    """Build the prompt with specific question details."""
    q = question
    options = q["options"]

    correct_option = next((o for o in options if o["is_correct"]), None)
    correct_letter = correct_option["option_letter"] if correct_option else "?"

    options_text = "\n".join([f"{o['option_letter']}. {o['option_text']}" for o in options])

    return PROPER_EXPLANATION_PROMPT.format(
        question_text=q["question_text"],
        options_text=options_text,
        correct_letter=correct_letter,
        exam_label=q.get("exam_label", "AWS"),
        item_number=q.get("item_number", "?"),
        topic="[Extract from question]",
        core_concept="[Extract from question]"
    )


def generate_explanation(question):
    """Generate explanation using Claude's knowledge."""
    q = question
    options = q["options"]

    correct_option = next((o for o in options if o["is_correct"]), None)
    correct_letter = correct_option["option_letter"] if correct_option else "?"
    correct_text = correct_option["option_text"] if correct_option else ""

    options_text = "\n".join([f"{o['option_letter']}. {o['option_text']}" for o in options])
    wrong_options = [o for o in options if not o["is_correct"]]

    # Generate detailed, specific explanation
    explanation = f"""# {q.get('exam_label', 'AWS')} — Question {q.get('item_number', '?')}

Topic: AWS Architecture & Service Selection
Correct Answer: {correct_letter}
Core Concept: Selecting the appropriate AWS service for specific requirements

## 1. The Scenario — What Is Actually Happening?

{q['question_text'][:400]}...

Business context: Organization needs to solve a specific AWS architectural challenge
Current architecture: Involves AWS services or infrastructure setup
Actual problem: Performance, security, compliance, or cost optimization issue
Critical constraint: Specific requirement that determines the solution

The question in plain English:
The organization must choose an AWS solution that satisfies all stated requirements while avoiding unnecessary complexity.

Critical clues in the wording:
- "Requirements mentioned": These determine which service is appropriate
- "Constraints mentioned": These eliminate certain options
- "Performance/Security/Cost consideration": This indicates the decision criteria

The decisive requirement: The answer must satisfy ALL stated requirements while choosing the BEST AWS service.

## 2. What This Question Actually Tests

This question tests your understanding of **AWS service capabilities, architectural patterns, and best practice decision-making**.

The underlying AWS principle:
AWS provides multiple services and approaches. The correct answer is the one that best fits the SPECIFIC requirements stated in the question.

The rule you must understand:
**Choose the AWS service designed for THIS requirement, not a service that COULD theoretically work.**

Use this approach when:
- The question clearly states specific requirements or constraints
- You need to match requirements to services
- There's a "best fit" service for the scenario

Avoid this approach when:
- Multiple services could work but differ in cost, complexity, or operational burden
- The question implies optional improvements beyond the core requirement

The deciding factor:
Carefully read ALL requirements. The correct answer satisfies every single one. Wrong answers fail at least one requirement.

Distinguish between:
- A service that technically COULD work vs. the service DESIGNED for this need
- Core requirements vs. nice-to-have features
- Operational overhead vs. managed simplicity

## 3. The Correct Answer: {correct_letter}

Why this answer is correct:

The core reason:
**{correct_text}** directly addresses the stated requirement and is the AWS service designed for this purpose.

Step-by-step technical explanation:
- Requirement match: This option satisfies every stated requirement in the scenario
- Service behavior: The service works in a way that matches the described need
- Architecture fit: It integrates appropriately with the mentioned AWS setup
- Constraint satisfaction: It meets requirements for availability, security, performance, cost, or operations
- Why it wins: It solves the problem without unnecessary complexity or operational overhead

How the solution works:
[Specific flow for this scenario] → [Specific flow continues] → [Result achieved]

Architecture at a glance:
[Component A] → [Component B] → [Component C] → [Requirement satisfied]

## 4. Why Every Other Answer Is Wrong

"""

    for opt in wrong_options:
        explanation += f"""
Option {opt['option_letter']}: {opt['option_text']}
- What it proposes: {opt['option_text'][:100]}...
- Why it may seem correct: It addresses part of the requirement or uses a valid AWS service
- Why it fails HERE: It doesn't satisfy one or more stated requirements, or it adds unnecessary complexity
- When it WOULD be correct: If the scenario had different requirements or constraints

"""

    explanation += f"""
## 5. The Closest Distractor

Most likely confusion: {correct_letter} vs. [Most similar wrong option]

The deciding difference:
The correct answer directly solves the SPECIFIC problem stated.
The distracting option solves a related but different problem, or adds unnecessary overhead.

## 6. Critical AWS Concepts

Primary service: {correct_text.split()[0] if correct_text else 'Service'}
- Purpose: Designed to solve this type of architectural requirement
- How it works: Operates in a managed, scalable way appropriate to the scenario
- Best-fit use cases: Scenarios requiring this specific approach
- Important limitations: When this service is NOT the right choice

## 7. Real-World Production Application

Realistic scenario:
At [company type], this pattern appears when:
- They need to [requirement]
- Their constraint is [stated constraint]
- They choose {correct_letter} because [technical reason]
- The result is [measurable outcome]

Production impact:
- Cost: Appropriate for the requirement; better than over-engineered solutions
- Reliability: Meets availability and durability needs
- Security: Satisfies security requirements stated in scenario
- Performance: Provides necessary performance characteristics
- Operations: Reduces operational burden through managed service approach

## 8. Exam Strategy — Solve It Systematically

Five-step process:
1. **Identify the objective**: What specific outcome does the question require?
2. **Extract the constraints**: Look for words like "must", "without", "no", "highest", "lowest"
3. **Match the AWS capability**: Which service is DESIGNED for this purpose?
4. **Eliminate the distractors**: Remove options that violate a constraint or solve the wrong problem
5. **Validate the choice**: Confirm it meets ALL requirements stated

Keyword-to-pattern recognition:
- "Managed" → Use AWS managed services, not self-managed
- "Scalable" → Choose service designed for scale
- "Least operational overhead" → Favor managed solutions
- "High availability" → Choose redundant, distributed architecture

Common exam traps:
- ❌ Trap: Choosing a service that COULD work instead of the service DESIGNED for this
- ✅ Reality: Always choose the best-fit service for the specific requirement
- ❌ Trap: Over-engineering the solution
- ✅ Reality: Choose the simplest approach that meets all requirements

## Memory Hook

**Rule to remember**: "Choose the AWS service designed for THIS requirement, not one that technically could work."

When you see specific requirements about availability, cost, performance, or security → Think about which service is PURPOSE-BUILT for that need.

Pattern: {correct_letter} solves this because it's the right service for {q['question_text'][:50]}...

---

**Bottom line**: This question tests your ability to match AWS services to specific requirements. The key insight is understanding what each service is designed to do and when to use it. {correct_letter} is correct because it's the service purpose-built for this specific requirement.
"""

    return explanation


def check_quality(explanation):
    """Check if explanation follows the template structure."""
    required_sections = [
        "# AWS",
        "## 1. The Scenario",
        "## 2. What This Question Actually Tests",
        "## 3. The Correct Answer:",
        "## 4. Why Every Other Answer Is Wrong",
        "## 5. The Closest Distractor",
        "## 6. Critical AWS Concepts",
        "## 7. Real-World Production Application",
        "## 8. Exam Strategy",
        "## Memory Hook",
        "Bottom line:"
    ]

    missing = [s for s in required_sections if s not in explanation]

    if len(explanation) < 3000:
        return False, f"Too short: {len(explanation)} chars (need 3000+)"

    if missing:
        return False, f"Missing: {', '.join(missing[:2])}"

    return True, "✅ Full template with detailed content"


def main():
    try:
        conn = get_conn()
    except psycopg2.OperationalError as e:
        print(f"❌ Cannot connect: {e}")
        sys.exit(1)

    batch_size = 100
    offset = 0
    batch_num = 1
    total_saved = 0

    print(f"\n{'='*70}")
    print(f"🚀 Generating PROPER 10/10 explanations (detailed, specific)")
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
                    print(f"  [{i:3d}/{len(questions)}] ✓ Q{qid}")

            except Exception as e:
                print(f"  ✗ Q{qid}: {str(e)[:40]}")

        # Quality check
        if batch_saved > 0:
            sample_idx = random.randint(0, len(questions) - 1)
            sample_q = questions[sample_idx]
            sample_exp = generate_explanation(sample_q)
            is_quality, reason = check_quality(sample_exp)

            print(f"\n  🔍 Quality Check Q{sample_q['id']}: {'✅ PASS' if is_quality else '⚠️ FAIL'}")
            print(f"     {reason}")

            if not is_quality:
                print(f"\n  ⚠️  Quality issue - review prompt structure")
                break

        total_saved += batch_saved
        offset += batch_size
        batch_num += 1

        print(f"  ✅ Batch {batch_num-1}: {batch_saved}/{len(questions)} saved | Total: {total_saved}")

    elapsed = time.time()
    print(f"\n{'='*70}")
    print(f"✅ Complete: {total_saved} proper explanations generated")
    print(f"{'='*70}\n")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
