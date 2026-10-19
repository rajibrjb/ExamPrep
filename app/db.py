import contextlib

import psycopg2
import psycopg2.extras

from app.config import config


@contextlib.contextmanager
def get_conn():
    conn = psycopg2.connect(config.db_dsn())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(schema_path="/app/schema.sql"):
    with open(schema_path) as f:
        ddl = f.read()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)


def get_or_create_source_image(conn, file_hash, file_path):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM source_images WHERE file_hash = %s", (file_hash,)
        )
        row = cur.fetchone()
        if row:
            return row, False
        cur.execute(
            """
            INSERT INTO source_images (file_hash, file_path, status)
            VALUES (%s, %s, 'pending')
            RETURNING *
            """,
            (file_hash, file_path),
        )
        return cur.fetchone(), True


def mark_source_image(conn, source_image_id, status, error_message=None):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE source_images
            SET status = %s, error_message = %s, processed_at = now()
            WHERE id = %s
            """,
            (status, error_message, source_image_id),
        )


def get_question_by_item(conn, exam_label, item_number):
    """Find the existing question for this (exam_label, item_number), if any,
    with its options -- used to decide duplicate vs. conflict on re-processing."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM questions WHERE exam_label = %s AND item_number = %s",
            (exam_label, item_number),
        )
        question = cur.fetchone()
        if not question:
            return None
        cur.execute(
            "SELECT * FROM options WHERE question_id = %s ORDER BY option_letter",
            (question["id"],),
        )
        question["options"] = cur.fetchall()
        return question


def insert_question(conn, source_image_id, exam_label, item_number, question_text,
                     raw_ocr_text, confidence, needs_review):
    """Plain insert for a (exam_label, item_number) not seen before. Re-processing
    an existing one goes through insert_conflict/mark_duplicate instead -- never
    silently overwritten (see app/dedupe.py)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO questions
                (source_image_id, exam_label, item_number, question_text,
                 raw_ocr_text, confidence, needs_review, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now())
            RETURNING *
            """,
            (source_image_id, exam_label, item_number, question_text,
             raw_ocr_text, confidence, needs_review),
        )
        return cur.fetchone()


def mark_source_image_duplicate(conn, source_image_id, duplicate_of_question_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE source_images
            SET status = 'duplicate', duplicate_of_question_id = %s, processed_at = now()
            WHERE id = %s
            """,
            (duplicate_of_question_id, source_image_id),
        )


def insert_conflict(conn, question_id, source_image_id, question_text, options, similarity):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO question_conflicts
                (question_id, source_image_id, question_text, options_json, similarity)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (question_id, source_image_id, question_text,
             psycopg2.extras.Json(options), similarity),
        )
        return cur.fetchone()


def list_conflicts(conn, resolved=False):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT c.*, q.exam_label, q.item_number, q.question_text AS existing_question_text
            FROM question_conflicts c
            JOIN questions q ON q.id = c.question_id
            WHERE (c.resolved_at IS NOT NULL) = %s
            ORDER BY c.created_at DESC
            """,
            (resolved,),
        )
        return cur.fetchall()


def get_conflict(conn, conflict_id):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM question_conflicts WHERE id = %s", (conflict_id,))
        return cur.fetchone()


def resolve_conflict(conn, conflict_id, resolution):
    """resolution:
      'kept_existing' -- discard the new extraction
      'adopted_new'   -- overwrite the stored question with the new extraction
      'kept_both'     -- they're genuinely different questions: keep the existing
                         one untouched AND insert the new extraction as its own
                         question. The collision means one of their item numbers
                         was misread, so the new row gets item_number NULL and a
                         review flag telling the user to set the right number
                         from its screenshot."""
    conflict = get_conflict(conn, conflict_id)
    if not conflict:
        return
    with conn.cursor() as cur:
        if resolution == "kept_both":
            cur.execute(
                """
                INSERT INTO questions
                    (source_image_id, exam_label, item_number, question_text,
                     confidence, needs_review, updated_at)
                VALUES (%s, NULL, NULL, %s, 'low', true, now())
                RETURNING id
                """,
                (conflict["source_image_id"], conflict["question_text"]),
            )
            new_id = cur.fetchone()[0]
            for opt in conflict["options_json"]:
                cur.execute(
                    """
                    INSERT INTO options
                        (question_id, option_letter, option_text, is_correct, color_detected, source_signal)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (new_id, opt["option_letter"], opt["option_text"],
                     opt["is_correct"], opt.get("color_detected"), "manual"),
                )
            cur.execute(
                "INSERT INTO review_queue (question_id, reason) VALUES (%s, %s)",
                (new_id,
                 "saved from a conflict as a separate question -- its item number "
                 "collided with another question's, so set the correct exam/item "
                 "number from the screenshot"),
            )
            cur.execute(
                "UPDATE questions SET needs_review = false WHERE id = %s",
                (conflict["question_id"],),
            )
        elif resolution == "adopted_new":
            cur.execute(
                """
                UPDATE questions
                SET question_text = %s, confidence = 'high', needs_review = false, updated_at = now()
                WHERE id = %s
                """,
                (conflict["question_text"], conflict["question_id"]),
            )
            cur.execute("DELETE FROM options WHERE question_id = %s", (conflict["question_id"],))
            for opt in conflict["options_json"]:
                cur.execute(
                    """
                    INSERT INTO options
                        (question_id, option_letter, option_text, is_correct, color_detected, source_signal)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (conflict["question_id"], opt["option_letter"], opt["option_text"],
                     opt["is_correct"], opt.get("color_detected"), "manual"),
                )
        else:
            cur.execute(
                "UPDATE questions SET needs_review = false WHERE id = %s",
                (conflict["question_id"],),
            )

        cur.execute(
            "UPDATE question_conflicts SET resolved_at = now(), resolution = %s WHERE id = %s",
            (resolution, conflict_id),
        )


def replace_options(conn, question_id, options):
    """options: list of dicts with option_letter, option_text, is_correct, color_detected, source_signal"""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM options WHERE question_id = %s", (question_id,))
        for opt in options:
            cur.execute(
                """
                INSERT INTO options
                    (question_id, option_letter, option_text, is_correct,
                     color_detected, source_signal)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (question_id, opt["option_letter"], opt["option_text"],
                 opt["is_correct"], opt.get("color_detected"),
                 opt.get("source_signal", "color")),
            )


def add_review_reason(conn, question_id, reason):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO review_queue (question_id, reason) VALUES (%s, %s)",
            (question_id, reason),
        )


# List filters shared by the question list, its pagination counts, and the
# detail page's prev/next navigation, so "Next" always walks the same set the
# user is looking at.
QUESTION_FILTERS = {
    "all": "",
    "needs_review": "needs_review = true",
    "read": "read_at IS NOT NULL",
    "unread": "read_at IS NULL",
}


def _filter_clause(question_filter, prefix="WHERE"):
    clause = QUESTION_FILTERS.get(question_filter or "all", "")
    return f" {prefix} {clause}" if clause else ""


def list_questions(conn, question_filter="all", limit=200, offset=0):
    query = (
        "SELECT * FROM questions"
        + _filter_clause(question_filter)
        + " ORDER BY exam_label, item_number LIMIT %s OFFSET %s"
    )
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, (limit, offset))
        return cur.fetchall()


def set_question_read(conn, question_id, read):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE questions SET read_at = CASE WHEN %s THEN now() ELSE NULL END WHERE id = %s",
            (read, question_id),
        )


def list_question_ids_needing_explanation(conn, confidence="high"):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM questions
            WHERE confidence = %s AND explanation IS NULL
            ORDER BY exam_label, item_number
            """,
            (confidence,),
        )
        return [row[0] for row in cur.fetchall()]


def count_questions(conn, question_filter="all"):
    query = "SELECT count(*) FROM questions" + _filter_clause(question_filter)
    with conn.cursor() as cur:
        cur.execute(query)
        (total,) = cur.fetchone()
        return total


def get_question(conn, question_id):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM questions WHERE id = %s", (question_id,))
        question = cur.fetchone()
        if not question:
            return None
        cur.execute(
            "SELECT * FROM options WHERE question_id = %s ORDER BY option_letter",
            (question_id,),
        )
        question["options"] = cur.fetchall()
        cur.execute(
            "SELECT file_path FROM source_images WHERE id = %s",
            (question["source_image_id"],),
        )
        src = cur.fetchone()
        question["source_file_path"] = src["file_path"] if src else None
        return question


def get_adjacent_question_ids(conn, exam_label, item_number, question_filter="all"):
    """Previous/next question id in the same ordering used by the question list
    (exam_label, item_number), for the detail page's prev/next navigation.
    Navigation stays within the given list filter (needs_review/read/unread),
    so paging from a filtered list walks only that set."""
    review_clause = _filter_clause(question_filter, prefix="AND")
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id FROM questions
            WHERE (exam_label, item_number) < (%s, %s){review_clause}
            ORDER BY exam_label DESC, item_number DESC
            LIMIT 1
            """,
            (exam_label, item_number),
        )
        prev_row = cur.fetchone()
        cur.execute(
            f"""
            SELECT id FROM questions
            WHERE (exam_label, item_number) > (%s, %s){review_clause}
            ORDER BY exam_label ASC, item_number ASC
            LIMIT 1
            """,
            (exam_label, item_number),
        )
        next_row = cur.fetchone()
    return (prev_row[0] if prev_row else None), (next_row[0] if next_row else None)


def update_question_manual(conn, question_id, question_text, options,
                            exam_label=None, item_number=None):
    """options: list of {option_letter, option_text, is_correct}.
    exam_label/item_number, when provided, also update the question's identity
    (used to fix misread item numbers, e.g. rows saved from a 'keep both'
    conflict resolution). Raises ValueError if that identity is already taken
    by a different question."""
    with conn.cursor() as cur:
        if exam_label is not None and item_number is not None:
            cur.execute(
                "SELECT id FROM questions WHERE exam_label = %s AND item_number = %s AND id != %s",
                (exam_label, item_number, question_id),
            )
            taken = cur.fetchone()
            if taken:
                raise ValueError(
                    f"{exam_label} item {item_number} is already used by question #{taken[0]}"
                )
            cur.execute(
                "UPDATE questions SET exam_label = %s, item_number = %s WHERE id = %s",
                (exam_label, item_number, question_id),
            )
        cur.execute(
            """
            UPDATE questions
            SET question_text = %s, needs_review = false, confidence = 'high', updated_at = now()
            WHERE id = %s
            """,
            (question_text, question_id),
        )
        cur.execute("DELETE FROM options WHERE question_id = %s", (question_id,))
        for opt in options:
            cur.execute(
                """
                INSERT INTO options
                    (question_id, option_letter, option_text, is_correct, source_signal)
                VALUES (%s, %s, %s, %s, 'manual')
                """,
                (question_id, opt["option_letter"], opt["option_text"], opt["is_correct"]),
            )
        cur.execute(
            "UPDATE review_queue SET resolved_at = now() WHERE question_id = %s AND resolved_at IS NULL",
            (question_id,),
        )


def update_question_reanalysis(conn, question_id, result):
    """Overwrite a question's extracted content with a fresh re-analysis of its
    source image (the 'Re-analyze' button). Identity (exam/item) is left
    untouched; old unresolved review flags are superseded by the new ones."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE questions
            SET question_text = %s, raw_ocr_text = %s, confidence = %s,
                needs_review = %s, updated_at = now()
            WHERE id = %s
            """,
            (result["question_text"], result["raw_ocr_text"], result["confidence"],
             result["needs_review"], question_id),
        )
        cur.execute(
            "UPDATE review_queue SET resolved_at = now() WHERE question_id = %s AND resolved_at IS NULL",
            (question_id,),
        )
    replace_options(conn, question_id, result["options"])
    if result["needs_review"]:
        for reason in result["review_reasons"]:
            add_review_reason(conn, question_id, reason)


def save_explanation(conn, question_id, explanation, provider, model):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE questions
            SET explanation = %s, explanation_provider = %s, explanation_model = %s,
                explanation_generated_at = now()
            WHERE id = %s
            """,
            (explanation, provider, model, question_id),
        )


def list_source_images(conn, status=None):
    query = "SELECT * FROM source_images"
    params = []
    if status is not None:
        query += " WHERE status = %s"
        params.append(status)
    query += " ORDER BY id"
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        return cur.fetchall()


def counts(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT status, count(*) FROM source_images GROUP BY status")
        image_counts = dict(cur.fetchall())
        cur.execute("SELECT needs_review, count(*) FROM questions GROUP BY needs_review")
        review_counts = dict(cur.fetchall())
        cur.execute("SELECT count(*) FROM question_conflicts WHERE resolved_at IS NULL")
        (open_conflicts,) = cur.fetchone()
    return {
        "images_pending": image_counts.get("pending", 0),
        "images_processed": image_counts.get("processed", 0),
        "images_duplicate": image_counts.get("duplicate", 0),
        "images_error": image_counts.get("error", 0),
        "questions_needs_review": review_counts.get(True, 0),
        "questions_ok": review_counts.get(False, 0),
        "open_conflicts": open_conflicts,
    }
