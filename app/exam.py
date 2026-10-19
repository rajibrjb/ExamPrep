"""Exam practice module: simulated exams, retry-failed drills, and study
quizzes over the extracted question bank.

Grading is exact-match on the answer SET (multi-select questions are only
correct when every correct option and no wrong option is selected). The
"failed pool" used by retry_failed mode is derived, not stored: a question is
failing iff its most recent GRADED exam answer was wrong -- answering it
correctly in any later session removes it from the pool automatically.
"""
import psycopg2.extras

DEFAULT_QUESTION_COUNT = 65
DEFAULT_TIME_LIMIT_SECONDS = 130 * 60
PASS_THRESHOLD = 0.72


def _dict_cur(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


# Questions usable in an exam: confirmed (not needing review) with a real
# answer key (>=2 options, >=1 correct).
ELIGIBLE_SQL = """
    SELECT q.id FROM questions q
    WHERE NOT q.needs_review
      AND (SELECT count(*) FROM options o WHERE o.question_id = q.id) >= 2
      AND EXISTS (SELECT 1 FROM options o WHERE o.question_id = q.id AND o.is_correct)
"""

# Most recent graded answer per question was wrong => currently failing.
FAILED_POOL_SQL = """
    SELECT question_id FROM (
        SELECT DISTINCT ON (question_id) question_id, is_correct
        FROM exam_answers
        WHERE is_correct IS NOT NULL
        ORDER BY question_id, answered_at DESC NULLS LAST, id DESC
    ) latest
    WHERE NOT is_correct
"""


def eligible_count(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM ({ELIGIBLE_SQL}) e")
        return cur.fetchone()[0]


def failed_pool_count(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM ({FAILED_POOL_SQL}) f")
        return cur.fetchone()[0]


def create_session(conn, mode, question_count=None, time_limit_seconds=None):
    """Create a session with a fixed random question order. Returns the session
    id, or None if the pool for the requested mode is empty."""
    with _dict_cur(conn) as cur:
        if mode == "retry_failed":
            cur.execute(
                f"""
                SELECT f.question_id AS id FROM ({FAILED_POOL_SQL}) f
                WHERE f.question_id IN ({ELIGIBLE_SQL})
                ORDER BY random()
                """ + ("LIMIT %s" if question_count else ""),
                (question_count,) if question_count else (),
            )
        else:
            cur.execute(
                f"SELECT id FROM ({ELIGIBLE_SQL}) e ORDER BY random() LIMIT %s",
                (question_count or DEFAULT_QUESTION_COUNT,),
            )
        ids = [row["id"] for row in cur.fetchall()]
        if not ids:
            return None

        cur.execute(
            """
            INSERT INTO exam_sessions (mode, question_count, time_limit_seconds, pass_threshold)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (mode, len(ids), time_limit_seconds, PASS_THRESHOLD),
        )
        session_id = cur.fetchone()["id"]
        for seq, qid in enumerate(ids, start=1):
            cur.execute(
                "INSERT INTO exam_answers (session_id, question_id, seq) VALUES (%s, %s, %s)",
                (session_id, qid, seq),
            )
    return session_id


def get_session(conn, session_id):
    with _dict_cur(conn) as cur:
        cur.execute("SELECT * FROM exam_sessions WHERE id = %s", (session_id,))
        session = cur.fetchone()
        if not session:
            return None
        cur.execute(
            """
            SELECT count(*) FILTER (WHERE selected_letters IS NOT NULL) AS answered,
                   extract(epoch FROM (now() - (SELECT started_at FROM exam_sessions WHERE id = %s)))::int AS elapsed
            FROM exam_answers WHERE session_id = %s
            """,
            (session_id, session_id),
        )
        extra = cur.fetchone()
        session["answered"] = extra["answered"]
        session["elapsed_seconds"] = extra["elapsed"]
        if session["time_limit_seconds"]:
            session["remaining_seconds"] = max(0, session["time_limit_seconds"] - extra["elapsed"])
        else:
            session["remaining_seconds"] = None
    return session


def unfinished_session(conn):
    with _dict_cur(conn) as cur:
        cur.execute(
            "SELECT id FROM exam_sessions WHERE finished_at IS NULL ORDER BY started_at DESC LIMIT 1"
        )
        row = cur.fetchone()
    return get_session(conn, row["id"]) if row else None


def get_exam_question(conn, session_id, seq):
    """One question as shown during the exam: options WITHOUT correctness."""
    with _dict_cur(conn) as cur:
        cur.execute(
            """
            SELECT a.seq, a.selected_letters, a.marked, a.is_correct,
                   q.id AS question_id, q.exam_label, q.item_number, q.question_text,
                   q.explanation
            FROM exam_answers a JOIN questions q ON q.id = a.question_id
            WHERE a.session_id = %s AND a.seq = %s
            """,
            (session_id, seq),
        )
        row = cur.fetchone()
        if not row:
            return None
        cur.execute(
            "SELECT option_letter, option_text FROM options WHERE question_id = %s ORDER BY option_letter",
            (row["question_id"],),
        )
        row["options"] = cur.fetchall()
        cur.execute(
            "SELECT count(*) FROM options WHERE question_id = %s AND is_correct",
            (row["question_id"],),
        )
        row["multi_select"] = cur.fetchone()["count"] > 1
    return row


def palette(conn, session_id):
    """Per-seq status for the question grid: answered / marked."""
    with _dict_cur(conn) as cur:
        cur.execute(
            """
            SELECT seq, (selected_letters IS NOT NULL) AS answered, marked
            FROM exam_answers WHERE session_id = %s ORDER BY seq
            """,
            (session_id,),
        )
        return cur.fetchall()


def correct_letters_for(conn, question_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT option_letter FROM options WHERE question_id = %s AND is_correct ORDER BY option_letter",
            (question_id,),
        )
        return [r[0] for r in cur.fetchall()]


def save_answer(conn, session_id, seq, letters, marked=None, grade_now=False):
    """Store the user's selection. In quiz (study) mode grade_now=True grades
    immediately and returns correctness + the answer key."""
    letters = sorted(set(letters or []))
    with _dict_cur(conn) as cur:
        cur.execute(
            """
            UPDATE exam_answers
            SET selected_letters = %s,
                marked = COALESCE(%s, marked),
                answered_at = now()
            WHERE session_id = %s AND seq = %s
            RETURNING question_id
            """,
            (letters if letters else None, marked, session_id, seq),
        )
        row = cur.fetchone()
        if not row:
            return None
        result = {"saved": True}
        if grade_now:
            correct = correct_letters_for(conn, row["question_id"])
            is_correct = letters == correct
            cur.execute(
                "UPDATE exam_answers SET is_correct = %s WHERE session_id = %s AND seq = %s",
                (is_correct if letters else None, session_id, seq),
            )
            result.update({"is_correct": is_correct, "correct_letters": correct})
    return result


def submit_session(conn, session_id):
    """Grade every answer (exact set match; unanswered = wrong), finalize the
    session, and return it."""
    with _dict_cur(conn) as cur:
        cur.execute(
            "SELECT seq, question_id, selected_letters FROM exam_answers WHERE session_id = %s",
            (session_id,),
        )
        answers = cur.fetchall()
        correct_count = 0
        for a in answers:
            correct = correct_letters_for(conn, a["question_id"])
            selected = sorted(a["selected_letters"] or [])
            is_correct = bool(selected) and selected == correct
            correct_count += is_correct
            cur.execute(
                "UPDATE exam_answers SET is_correct = %s, answered_at = COALESCE(answered_at, now()) "
                "WHERE session_id = %s AND seq = %s",
                (is_correct, session_id, a["seq"]),
            )
        total = len(answers)
        cur.execute(
            """
            UPDATE exam_sessions
            SET finished_at = now(), score_correct = %s, score_total = %s,
                passed = (%s::float / NULLIF(%s, 0)) >= pass_threshold
            WHERE id = %s AND finished_at IS NULL
            """,
            (correct_count, total, correct_count, total, session_id),
        )
    return get_session(conn, session_id)


def session_results(conn, session_id):
    """Per-question breakdown for the results page."""
    with _dict_cur(conn) as cur:
        cur.execute(
            """
            SELECT a.seq, a.selected_letters, a.is_correct, a.marked,
                   q.id AS question_id, q.exam_label, q.item_number,
                   q.question_text, q.explanation
            FROM exam_answers a JOIN questions q ON q.id = a.question_id
            WHERE a.session_id = %s ORDER BY a.seq
            """,
            (session_id,),
        )
        rows = cur.fetchall()
        for row in rows:
            cur.execute(
                """
                SELECT option_letter, option_text, is_correct
                FROM options WHERE question_id = %s ORDER BY option_letter
                """,
                (row["question_id"],),
            )
            row["options"] = cur.fetchall()
            row["correct_letters"] = [o["option_letter"] for o in row["options"] if o["is_correct"]]
    return rows


def list_sessions(conn, limit=50):
    with _dict_cur(conn) as cur:
        cur.execute(
            """
            SELECT *, extract(epoch FROM (finished_at - started_at))::int AS duration_seconds
            FROM exam_sessions ORDER BY started_at DESC LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def weak_questions(conn, limit=200):
    """Questions answered wrong at least once, with aggregate stats; the
    currently_failing flag mirrors the retry_failed pool."""
    with _dict_cur(conn) as cur:
        cur.execute(
            f"""
            SELECT q.id, q.exam_label, q.item_number, left(q.question_text, 120) AS question_text,
                   count(*) FILTER (WHERE a.is_correct IS NOT NULL) AS times_asked,
                   count(*) FILTER (WHERE a.is_correct = false) AS times_wrong,
                   (q.id IN ({FAILED_POOL_SQL})) AS currently_failing
            FROM exam_answers a JOIN questions q ON q.id = a.question_id
            WHERE a.is_correct IS NOT NULL
            GROUP BY q.id
            HAVING count(*) FILTER (WHERE a.is_correct = false) > 0
            ORDER BY (q.id IN ({FAILED_POOL_SQL})) DESC, count(*) FILTER (WHERE a.is_correct = false) DESC
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()
