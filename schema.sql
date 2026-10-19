CREATE TABLE IF NOT EXISTS source_images (
    id                        SERIAL PRIMARY KEY,
    file_hash                 TEXT NOT NULL UNIQUE,
    file_path                 TEXT NOT NULL,
    status                    TEXT NOT NULL DEFAULT 'pending', -- pending | processed | duplicate | error
    error_message             TEXT,
    duplicate_of_question_id  INTEGER, -- FK added below, after `questions` exists (circular reference)
    ingested_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at              TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS questions (
    id               SERIAL PRIMARY KEY,
    source_image_id  INTEGER NOT NULL REFERENCES source_images(id) ON DELETE CASCADE,
    exam_label       TEXT,
    item_number      INTEGER,
    question_text    TEXT NOT NULL,
    raw_ocr_text     TEXT,
    confidence       TEXT NOT NULL DEFAULT 'low', -- high | low
    needs_review     BOOLEAN NOT NULL DEFAULT true,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (exam_label, item_number)
);

CREATE TABLE IF NOT EXISTS options (
    id              SERIAL PRIMARY KEY,
    question_id     INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    option_letter   TEXT NOT NULL,
    option_text     TEXT NOT NULL,
    is_correct      BOOLEAN NOT NULL DEFAULT false,
    color_detected  TEXT, -- green | red | other | null
    source_signal   TEXT NOT NULL DEFAULT 'color', -- color | answer_text | llm | manual
    UNIQUE (question_id, option_letter)
);

CREATE TABLE IF NOT EXISTS review_queue (
    id           SERIAL PRIMARY KEY,
    question_id  INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    reason       TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at  TIMESTAMPTZ
);

-- Holds an alternate extraction when a re-processed screenshot shares an existing
-- question's (exam_label, item_number) but its content meaningfully disagrees with
-- what's already stored -- left for a human to pick a side rather than silently
-- overwritten. A screenshot whose extraction instead agrees with the stored
-- question is treated as a plain duplicate and never reaches this table.
CREATE TABLE IF NOT EXISTS question_conflicts (
    id              SERIAL PRIMARY KEY,
    question_id     INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    source_image_id INTEGER NOT NULL REFERENCES source_images(id) ON DELETE CASCADE,
    question_text   TEXT NOT NULL,
    options_json     JSONB NOT NULL, -- [{option_letter, option_text, is_correct, color_detected}, ...]
    similarity       REAL,           -- question_text similarity ratio vs the stored version, for triage
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at      TIMESTAMPTZ,
    resolution        TEXT -- kept_existing | adopted_new
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'source_images_duplicate_of_question_id_fkey'
    ) THEN
        ALTER TABLE source_images
            ADD CONSTRAINT source_images_duplicate_of_question_id_fkey
            FOREIGN KEY (duplicate_of_question_id) REFERENCES questions(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_questions_needs_review ON questions(needs_review);
CREATE INDEX IF NOT EXISTS idx_options_question_id ON options(question_id);
CREATE INDEX IF NOT EXISTS idx_review_queue_question_id ON review_queue(question_id);
CREATE INDEX IF NOT EXISTS idx_question_conflicts_question_id ON question_conflicts(question_id);
CREATE INDEX IF NOT EXISTS idx_question_conflicts_unresolved ON question_conflicts(question_id) WHERE resolved_at IS NULL;

-- Simple key/value store for user-configurable settings (LLM provider, API keys,
-- model names, the editable "Explain" prompt template). schema.sql re-runs on
-- every container start, so new columns/tables below use IF NOT EXISTS guards.
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE questions ADD COLUMN IF NOT EXISTS explanation TEXT;
ALTER TABLE questions ADD COLUMN IF NOT EXISTS explanation_provider TEXT;
ALTER TABLE questions ADD COLUMN IF NOT EXISTS explanation_model TEXT;
ALTER TABLE questions ADD COLUMN IF NOT EXISTS explanation_generated_at TIMESTAMPTZ;
-- Study-progress marker: NULL = unread; set when the user marks a question read.
ALTER TABLE questions ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ;

-- Exam practice module: a session is one sitting (simulated exam, retry-failed
-- drill, or study quiz); its exam_answers rows are pre-created at session start
-- (fixing the random question order) and filled in as the user answers.
-- is_correct stays NULL until grading: at submit time for exam mode, or
-- immediately per answer in quiz (study) mode.
CREATE TABLE IF NOT EXISTS exam_sessions (
    id                  SERIAL PRIMARY KEY,
    mode                TEXT NOT NULL, -- exam | retry_failed | quiz
    question_count      INTEGER NOT NULL,
    time_limit_seconds  INTEGER, -- NULL = untimed
    pass_threshold      REAL NOT NULL DEFAULT 0.72,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    score_correct       INTEGER,
    score_total         INTEGER,
    passed              BOOLEAN
);

CREATE TABLE IF NOT EXISTS exam_answers (
    id                SERIAL PRIMARY KEY,
    session_id        INTEGER NOT NULL REFERENCES exam_sessions(id) ON DELETE CASCADE,
    question_id       INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    seq               INTEGER NOT NULL,
    selected_letters  TEXT[],
    marked            BOOLEAN NOT NULL DEFAULT false,
    is_correct        BOOLEAN,
    answered_at       TIMESTAMPTZ,
    UNIQUE (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_exam_answers_session ON exam_answers(session_id);
CREATE INDEX IF NOT EXISTS idx_exam_answers_question ON exam_answers(question_id);
