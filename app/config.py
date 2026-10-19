import os
import yaml

_CONFIG_PATH = os.environ.get("CONFIG_PATH", "/app/config.yaml")


def _load_yaml():
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


_raw = _load_yaml()


class Config:
    db_host = os.environ.get("DB_HOST", _raw["database"]["host"])
    db_port = int(os.environ.get("DB_PORT", _raw["database"]["port"]))
    db_name = os.environ.get("POSTGRES_DB", _raw["database"]["name"])
    db_user = os.environ.get("POSTGRES_USER", _raw["database"]["user"])
    db_password = os.environ.get("POSTGRES_PASSWORD", _raw["database"]["password"])

    ollama_base_url = os.environ.get("OLLAMA_BASE_URL", _raw["ollama"]["base_url"])
    ollama_model = os.environ.get("OLLAMA_MODEL", _raw["ollama"]["model"])
    ollama_timeout_seconds = int(_raw["ollama"].get("timeout_seconds", 120))

    incoming_dir = _raw["paths"]["incoming_dir"]
    cleaned_dir = _raw["paths"]["cleaned_dir"]
    debug_dir = _raw["paths"]["debug_dir"]

    color_thresholds = _raw["color_thresholds"]
    dedupe_similarity_threshold = float(_raw["dedupe"]["similarity_threshold"])

    # When true, skip the per-image Ollama advisory cross-check during ingestion.
    # That call is never used to decide correctness (see structure_builder.py) --
    # only to surface an optional "local vision model disagrees" review note --
    # so it's safe to disable for large bulk backfills where its latency dominates.
    skip_llm_advisory = os.environ.get("SKIP_LLM_ADVISORY", "false").lower() == "true"

    @classmethod
    def db_dsn(cls):
        return (
            f"host={cls.db_host} port={cls.db_port} dbname={cls.db_name} "
            f"user={cls.db_user} password={cls.db_password}"
        )


config = Config()
