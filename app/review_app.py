import threading

from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

from app.db import (
    count_questions,
    counts,
    get_adjacent_question_ids,
    get_conn,
    get_question,
    list_conflicts,
    list_questions,
    resolve_conflict,
    save_explanation,
    set_question_read,
    update_question_manual,
    update_question_reanalysis,
)
from app import exam
from app.explain import explain_question, render_markdown
from app.config import config
from app.image_prep import load_and_prepare
from app.ingest import process_folder, process_single, retry_failed
from app.structure_builder import build_question
from app.llm_providers import LLMError
from app.settings import DEFAULTS, PROVIDERS, get_all_settings, set_settings

app = Flask(__name__)


@app.context_processor
def inject_counts():
    with get_conn() as conn:
        return {"counts": counts(conn)}


_log_lines = []
_log_lock = threading.Lock()
_run_lock = threading.Lock()
_running = False


def _log(line):
    with _log_lock:
        _log_lines.append(line)
        del _log_lines[:-500]
    print(line, flush=True)


def _run_pipeline(mode):
    global _running
    try:
        _log(f"--- run started ({mode}) ---")
        if mode == "retry_failed":
            retry_failed(log=_log)
        else:
            process_folder(log=_log)
        _log("--- run finished ---")
    finally:
        with _run_lock:
            _running = False


PAGE_SIZE = 50


@app.route("/")
def index():
    filter_arg = request.args.get("filter", "needs_review")
    if filter_arg not in ("all", "needs_review", "read", "unread"):
        filter_arg = "all"
    page = max(1, request.args.get("page", 1, type=int))
    with get_conn() as conn:
        total = count_questions(conn, question_filter=filter_arg)
        filter_counts = {f: count_questions(conn, question_filter=f)
                         for f in ("all", "needs_review", "unread", "read")}
        total_pages = max(1, -(-total // PAGE_SIZE))  # ceil div
        page = min(page, total_pages)
        questions = list_questions(
            conn, question_filter=filter_arg, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE,
        )
    return render_template(
        "index.html", questions=questions, filter=filter_arg, active_tab=filter_arg,
        page=page, total_pages=total_pages, total=total, filter_counts=filter_counts,
    )


@app.route("/run", methods=["POST"])
def run():
    global _running
    mode = request.form.get("mode", "process")
    with _run_lock:
        if _running:
            return jsonify({"started": False, "reason": "already running"}), 409
        _running = True
    thread = threading.Thread(target=_run_pipeline, args=(mode,), daemon=True)
    thread.start()
    return jsonify({"started": True})


@app.route("/process_file", methods=["POST"])
def process_file():
    """Synchronously extract+ingest ONE freshly-captured screenshot (used by the
    interactive capture loop, which gates advancing to the next question on this
    response). The file is renamed to item_NNNN.png from its own header before
    the DB row is written, so the stored path is always final."""
    import os.path

    filename = request.form.get("filename", "")
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        return jsonify({"error": "invalid filename"}), 400
    path = os.path.join(config.incoming_dir, filename)
    if not os.path.isfile(path):
        return jsonify({"error": f"file not found: {filename}"}), 404
    try:
        result = process_single(path, log=_log)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


@app.route("/run/status")
def run_status():
    with _run_lock:
        running = _running
    with _log_lock:
        lines = list(_log_lines)
    return jsonify({"running": running, "log": lines})


@app.route("/question/<int:question_id>")
def question_detail(question_id):
    # Prev/next honor the list filter the user came from: opened from the
    # Needs Review (or Read/Unread) tab, navigation walks only that set.
    nav_filter = request.args.get("filter", "all")
    # Practice-first: the page opens as a self-test (no answer shown) unless the
    # user explicitly asks to edit -- or came from the Needs Review queue, where
    # the whole point of opening a question is to fix it.
    mode = "edit" if (request.args.get("mode") == "edit" or nav_filter == "needs_review") else "practice"
    with get_conn() as conn:
        question = get_question(conn, question_id)
        if not question:
            return "not found", 404
        prev_id, next_id = get_adjacent_question_ids(
            conn, question["exam_label"], question["item_number"],
            question_filter=nav_filter,
        )
    multi_select = sum(1 for o in question["options"] if o["is_correct"]) > 1
    explanation_html = render_markdown(question["explanation"]) if question["explanation"] else None
    saved = request.args.get("saved") == "1"
    return render_template(
        "detail.html", q=question, explanation_html=explanation_html,
        prev_id=prev_id, next_id=next_id, nav_filter=nav_filter, saved=saved,
        mode=mode, multi_select=multi_select,
    )


@app.route("/question/<int:question_id>/check", methods=["POST"])
def question_check(question_id):
    """Practice-mode self-test: grade the user's selection against the stored
    answer key (exact set match, like the exam module). The key is only ever
    sent in this response, never embedded in the practice page HTML."""
    data = request.get_json(force=True, silent=True) or {}
    letters = sorted(set(data.get("letters", [])))
    with get_conn() as conn:
        correct = exam.correct_letters_for(conn, question_id)
    if not correct:
        return jsonify({"error": "this question has no confirmed answer yet"}), 400
    return jsonify({
        "is_correct": bool(letters) and letters == correct,
        "correct_letters": correct,
    })


@app.route("/question/<int:question_id>/read", methods=["POST"])
def question_read_toggle(question_id):
    """Mark a question read/unread (study-progress tracker)."""
    data = request.get_json(force=True, silent=True) or {}
    read = bool(data.get("read", True))
    with get_conn() as conn:
        set_question_read(conn, question_id, read)
    return jsonify({"ok": True, "read": read})


@app.route("/question/<int:question_id>/reanalyze", methods=["POST"])
def question_reanalyze(question_id):
    """Re-run the full extraction (OCR + color detection + the local vision
    model advisory, forced on regardless of SKIP_LLM_ADVISORY) against this
    question's source image and overwrite its stored content."""
    with get_conn() as conn:
        question = get_question(conn, question_id)
    if not question:
        return jsonify({"error": "question not found"}), 404
    path = question["source_file_path"]
    import os.path
    if not path or not os.path.isfile(path):
        return jsonify({"error": f"source image missing: {path}"}), 404
    try:
        cropped, cleaned = load_and_prepare(path)
        result = build_question(cropped, cleaned, path, force_llm=True)
        with get_conn() as conn:
            update_question_reanalysis(conn, question_id, result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    correct = sorted(o["option_letter"] for o in result["options"] if o["is_correct"])
    return jsonify({
        "ok": True,
        "num_options": len(result["options"]),
        "correct_letters": correct,
        "confidence": result["confidence"],
        "needs_review": result["needs_review"],
        "reasons": result["review_reasons"],
    })


@app.route("/question/<int:question_id>/explain", methods=["POST"])
def question_explain(question_id):
    """Generates a fresh explanation but does NOT persist it -- the result is a
    draft the user previews and explicitly keeps via /explain/save, so Regenerate
    can never silently clobber a saved explanation."""
    with get_conn() as conn:
        question = get_question(conn, question_id)
        if not question:
            return jsonify({"error": "question not found"}), 404
        try:
            result = explain_question(conn, question)
        except LLMError as e:
            return jsonify({"error": str(e)}), 502
    return jsonify({
        "html": result["html"],
        "raw": result["raw"],
        "provider": result["provider"],
        "model": result["model"],
    })


@app.route("/question/<int:question_id>/explain/save", methods=["POST"])
def question_explain_save(question_id):
    explanation = request.form.get("explanation_raw", "").strip()
    provider = request.form.get("provider", "")
    model = request.form.get("model", "")
    if not explanation:
        return jsonify({"error": "nothing to save"}), 400
    with get_conn() as conn:
        save_explanation(conn, question_id, explanation, provider, model)
    return jsonify({"ok": True})


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    with get_conn() as conn:
        if request.method == "POST":
            form = request.form
            updates = {
                "llm_provider": form.get("llm_provider", "ollama"),
                "explain_prompt_template": form.get("explain_prompt_template") or DEFAULTS["explain_prompt_template"],
            }
            for provider, meta in PROVIDERS.items():
                updates[meta["model_key"]] = form.get(meta["model_key"]) or meta["default_model"]
                if meta["needs_key"]:
                    # Field is left blank in the form (masked); only overwrite the
                    # stored key if the user actually typed a new one.
                    new_key = form.get(f"{provider}_api_key", "").strip()
                    if new_key:
                        updates[f"{provider}_api_key"] = new_key
            set_settings(conn, updates)
            return redirect(url_for("settings_page"))
        settings = get_all_settings(conn)

    providers_ui = {}
    for provider, meta in PROVIDERS.items():
        providers_ui[provider] = {
            "model": settings[meta["model_key"]],
            "needs_key": meta["needs_key"],
            "has_key": bool(settings.get(f"{provider}_api_key")),
        }
    return render_template(
        "settings.html",
        settings=settings,
        providers=providers_ui,
        default_prompt=DEFAULTS["explain_prompt_template"],
        active_tab="settings",
    )


@app.route("/conflicts")
def conflicts():
    with get_conn() as conn:
        open_conflicts = list_conflicts(conn, resolved=False)
    return render_template("conflicts.html", conflicts=open_conflicts, active_tab="conflicts")


@app.route("/conflicts/<int:conflict_id>/resolve", methods=["POST"])
def conflict_resolve(conflict_id):
    resolution = request.form["resolution"]
    with get_conn() as conn:
        resolve_conflict(conn, conflict_id, resolution)
    return redirect(url_for("conflicts"))


@app.route("/question/<int:question_id>/image")
def question_image(question_id):
    with get_conn() as conn:
        question = get_question(conn, question_id)
    if not question or not question["source_file_path"]:
        return "not found", 404
    return send_file(question["source_file_path"])


@app.route("/question/<int:question_id>/save", methods=["POST"])
def question_save(question_id):
    question_text = request.form["question_text"]
    letters = request.form.getlist("option_letter")
    texts = request.form.getlist("option_text")
    correct_letters = set(request.form.getlist("correct_letter"))

    options = [
        {"option_letter": letter, "option_text": text, "is_correct": letter in correct_letters}
        for letter, text in zip(letters, texts)
    ]
    exam_label = (request.form.get("exam_label") or "").strip().upper() or None
    item_raw = (request.form.get("item_number") or "").strip()
    item_number = int(item_raw) if item_raw.isdigit() else None
    try:
        with get_conn() as conn:
            update_question_manual(
                conn, question_id, question_text, options,
                exam_label=exam_label, item_number=item_number,
            )
    except ValueError as e:
        return f"Cannot save: {e}", 400
    nav_filter = request.args.get("filter", "needs_review")
    # Stay in edit mode after saving -- the user is mid-editing.
    return redirect(url_for("question_detail", question_id=question_id,
                            filter=nav_filter, saved=1, mode="edit"))


# ---------------------------------------------------------------------------
# Exam practice module
# ---------------------------------------------------------------------------

@app.route("/exam")
def exam_home():
    with get_conn() as conn:
        resumable = exam.unfinished_session(conn)
        eligible = exam.eligible_count(conn)
        failed_count = exam.failed_pool_count(conn)
        history = exam.list_sessions(conn, limit=10)
    return render_template(
        "exam_home.html", resumable=resumable, eligible=eligible,
        failed_count=failed_count, history=history, active_tab="exam",
        default_count=exam.DEFAULT_QUESTION_COUNT,
        default_minutes=exam.DEFAULT_TIME_LIMIT_SECONDS // 60,
    )


@app.route("/exam/start", methods=["POST"])
def exam_start():
    mode = request.form.get("mode", "exam")
    question_count = request.form.get("question_count", type=int)
    timed = request.form.get("timed") == "on"
    minutes = request.form.get("minutes", type=int)
    time_limit = (minutes * 60) if (timed and minutes) else None
    if mode == "quiz":
        time_limit = None
    with get_conn() as conn:
        session_id = exam.create_session(
            conn, mode, question_count=question_count, time_limit_seconds=time_limit,
        )
    if session_id is None:
        return redirect(url_for("exam_home"))
    return redirect(url_for("exam_question_page", session_id=session_id, seq=1))


@app.route("/exam/<int:session_id>/q/<int:seq>")
def exam_question_page(session_id, seq):
    with get_conn() as conn:
        session = exam.get_session(conn, session_id)
        if not session:
            return "exam session not found", 404
        if session["finished_at"]:
            return redirect(url_for("exam_results_page", session_id=session_id))
        question = exam.get_exam_question(conn, session_id, seq)
        if not question:
            return redirect(url_for("exam_question_page", session_id=session_id, seq=1))
        grid = exam.palette(conn, session_id)
    return render_template(
        "exam_question.html", s=session, question=question, palette=grid,
        active_tab="exam",
    )


@app.route("/exam/<int:session_id>/q/<int:seq>/answer", methods=["POST"])
def exam_answer(session_id, seq):
    data = request.get_json(force=True, silent=True) or {}
    letters = data.get("letters", [])
    marked = data.get("marked")
    with get_conn() as conn:
        session = exam.get_session(conn, session_id)
        if not session or session["finished_at"]:
            return jsonify({"error": "session not active"}), 400
        result = exam.save_answer(
            conn, session_id, seq, letters, marked=marked,
            grade_now=(session["mode"] == "quiz"),
        )
    if result is None:
        return jsonify({"error": "question not found"}), 404
    return jsonify(result)


@app.route("/exam/<int:session_id>/submit", methods=["POST"])
def exam_submit(session_id):
    with get_conn() as conn:
        exam.submit_session(conn, session_id)
    return redirect(url_for("exam_results_page", session_id=session_id))


@app.route("/exam/<int:session_id>/results")
def exam_results_page(session_id):
    with get_conn() as conn:
        session = exam.get_session(conn, session_id)
        if not session:
            return "exam session not found", 404
        if not session["finished_at"]:
            return redirect(url_for("exam_question_page", session_id=session_id, seq=1))
        results = exam.session_results(conn, session_id)
    wrong_only = request.args.get("wrong") == "1"
    if wrong_only:
        results = [r for r in results if not r["is_correct"]]
    return render_template(
        "exam_results.html", s=session, results=results, wrong_only=wrong_only,
        active_tab="exam",
    )


@app.route("/exam/history")
def exam_history():
    with get_conn() as conn:
        history = exam.list_sessions(conn, limit=100)
        weak = exam.weak_questions(conn)
        failed_count = exam.failed_pool_count(conn)
    return render_template(
        "exam_history.html", history=history, weak=weak,
        failed_count=failed_count, active_tab="exam",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
