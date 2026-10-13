import click

from app.db import counts, get_conn, init_schema
from app.ingest import process_folder
from app.ingest import retry_failed as retry_failed_images


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


if __name__ == "__main__":
    cli()
