import click

from app.db import (
    counts,
    get_conn,
    get_question,
    init_schema,
    list_question_ids_needing_explanation,
    save_explanation,
)
from app.explain import explain_question
from app.ingest import process_folder
from app.ingest import retry_failed as retry_failed_images
from app.llm_providers import LLMError


@click.group()
def cli():
    pass


@cli.command()
def init_db():
    init_schema()
    click.echo("schema initialized")


@cli.command()
@click.option("--input-dir", default=None, help="Folder of screenshots to process")
def process(input_dir):
    process_folder(input_dir=input_dir)


@cli.command()
def retry_failed():
    retry_failed_images()


@cli.command()
def status():
    with get_conn() as conn:
        click.echo(counts(conn))


@cli.command()
@click.option("--confidence", default="high", help="Only explain questions with this confidence")
@click.option("--limit", default=None, type=int, help="Max number of questions to process (None = all)")
def backfill_explanations(confidence, limit):
    """Generate+save an explanation (via the configured LLM provider, e.g. local
    Ollama) for every question of the given confidence that doesn't have one yet."""
    with get_conn() as conn:
        ids = list_question_ids_needing_explanation(conn, confidence=confidence)
    if limit:
        ids = ids[:limit]
    total = len(ids)
    click.echo(f"{total} {confidence}-confidence question(s) missing an explanation")

    done = failed = 0
    for i, qid in enumerate(ids, start=1):
        with get_conn() as conn:
            question = get_question(conn, qid)
            if not question:
                continue
            try:
                result = explain_question(conn, question)
                save_explanation(conn, qid, result["raw"], result["provider"], result["model"])
                done += 1
                click.echo(f"[{i}/{total}] saved explanation for question {qid}")
            except LLMError as e:
                failed += 1
                click.echo(f"[{i}/{total}] FAILED question {qid}: {e}")

    click.echo(f"done: {done} saved, {failed} failed")


if __name__ == "__main__":
    cli()
