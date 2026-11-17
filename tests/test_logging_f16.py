"""Prueba de rotación y compresión del logger JSON del integrante 4."""
import gzip
import json

from fenix.utils import logging_json


def test_rotated_log_is_valid_gzip_jsonl(tmp_path, monkeypatch):
    """Fuerza una rotación pequeña y valida las líneas JSON comprimidas."""
    monkeypatch.chdir(tmp_path)
    original_handler = logging_json.RotatingFileHandler

    class SmallRotatingFileHandler(original_handler):
        def __init__(self, *args, **kwargs):
            kwargs["maxBytes"] = 512
            kwargs["backupCount"] = 3
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(logging_json, "RotatingFileHandler", SmallRotatingFileHandler)
    logger, listener = logging_json.setup("test-logging-f16")

    try:
        for index in range(5):
            logger.info(
                "registro de prueba %s %s",
                index,
                "x" * 512,
                extra={"event_id": f"evt-{index:03d}"},
            )
    finally:
        listener.stop()
        for handler in listener.handlers:
            handler.close()

    compressed_logs = list((tmp_path / "logs").glob("*.gz"))
    assert compressed_logs, "El logger debería haber creado al menos un histórico gzip"

    rows = []
    for path in compressed_logs:
        with gzip.open(path, "rt", encoding="utf-8") as archive:
            rows.extend(json.loads(line) for line in archive if line.strip())

    assert rows, "El histórico gzip debería contener líneas JSON"
    assert all(row["event_id"].startswith("evt-") for row in rows)
    assert all(row["level"] == "INFO" for row in rows)
