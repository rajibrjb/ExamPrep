import threading

from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

from app.db import (
    counts,
    get_adjacent_question_ids,
    get_conn,
    get_question,
    list_conflicts,
    list_questions,
    resolve_conflict,
    save_explanation,
    update_question_manual,
)
from app.explain import explain_question, render_markdown
from app.ingest import process_folder, retry_failed
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


@app.route("/")
def index():
    filter_arg = request.args.get("filter", "needs_review")
    needs_review = True if filter_arg == "needs_review" else None
    with get_conn() as conn:
        questions = list_questions(conn, needs_review=needs_review)
    return render_template(
        "index.html", questions=questions, filter=filter_arg, active_tab=filter_arg,
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


@app.route("/run/status")
def run_status():
    with _run_lock:
        running = _running
    with _log_lock:
        lines = list(_log_lines)
    return jsonify({"running": running, "log": lines})


@app.route("/question/<int:question_id>")
def question_detail(question_id):
    with get_conn() as conn:
        question = get_question(conn, question_id)
        if not question:
            return "not found", 404
        prev_id, next_id = get_adjacent_question_ids(conn, question["exam_label"], question["item_number"])
    explanation_html = render_markdown(question["explanation"]) if question["explanation"] else None
    return render_template(
        "detail.html", q=question, explanation_html=explanation_html,
        prev_id=prev_id, next_id=next_id,
    )


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
    correct_letter = request.form.get("correct_letter")

    options = [
        {"option_letter": letter, "option_text": text, "is_correct": letter == correct_letter}
        for letter, text in zip(letters, texts)
    ]
    with get_conn() as conn:
        update_question_manual(conn, question_id, question_text, options)
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
