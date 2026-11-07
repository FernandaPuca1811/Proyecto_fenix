"""Módulo de Logging JSON y Observabilidad No Bloqueante (Integrante 4)."""

from datetime import datetime, timezone
import gzip
import json
import logging
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
import os
from pathlib import Path
import queue
import shutil


class JSONFormatter(logging.Formatter):
    """Serializa campos permitidos; no copia indiscriminadamente record.__dict__."""

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": datetime.fromtimestamp(
                record.created, timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "process": record.process,
            "thread": record.threadName,
        }

        # Lista cerrada de campos de correlación.
        for name in (
            "event_id",
            "message_id",
            "resource_id",
            "attempt",
            "action",
            "status",
        ):
            data[name] = getattr(record, name, None)

        return json.dumps(data, ensure_ascii=False)


def gzip_name(name: str) -> str:
    """Calcula el nombre comprimido de un histórico agregando .gz."""
    return name + ".gz"


def gzip_rotate(source: str, dest: str) -> None:
    """Comprime y elimina el histórico original."""
    with open(source, "rb") as origin, gzip.open(dest, "wb") as target:
        shutil.copyfileobj(origin, target)
    os.remove(source)


def setup(
    name: str,
    max_bytes: int = 2 * 1024 * 1024,
    backup_count: int = 3,
) -> tuple[logging.Logger, QueueListener]:
    """Inicia el pipeline JSON en un archivo exclusivo de este proceso."""
    if max_bytes <= 0 or backup_count < 0:
        raise ValueError("Los límites de rotación deben ser positivos")

    Path("logs").mkdir(exist_ok=True)
    sink = RotatingFileHandler(
        f"logs/{name}-{os.getpid()}.jsonl",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    sink.setFormatter(JSONFormatter())
    sink.namer, sink.rotator = gzip_name, gzip_rotate

    records = queue.Queue()
    logger = logging.getLogger("fenix")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False
    logger.addHandler(QueueHandler(records))

    listener = QueueListener(records, sink)
    listener.start()
    return logger, listener
