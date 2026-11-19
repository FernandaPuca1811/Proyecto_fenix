"""Integrante 5: ciclo de vida del worker y ubicación del ACK."""
import json  # Construye cuerpos de mensajes y errores de ledger.
import logging  # Captura niveles y contexto de los registros.
import pytest  # Fixtures y excepciones esperadas.
from botocore.exceptions import ClientError, EndpointConnectionError
from fenix.services import worker  # Módulo bajo prueba.
from fenix.utils.errors import PermanentError, RetryableError


class Capture(logging.Handler):
    """Guarda los registros emitidos para inspeccionarlos."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def by_message(self, prefix: str) -> list:
        return [r for r in self.records if r.getMessage().startswith(prefix)]


@pytest.fixture
def queue(monkeypatch):
    """SQS reemplazado: entrega fija y anotación ordenada de handler, ACK y retry.

    Returns:
        Objeto con calls, messages configurables, logger y captura.
    """
    class Queue:
        calls = []
        messages = []
        receive_errors = []
        retry_error = None
        ack_error = None
        sleeps = []

    def receive(url):
        if Queue.receive_errors:
            raise Queue.receive_errors.pop(0)
        Queue.calls.append("receive")
        return Queue.messages.pop(0) if Queue.messages else []

    def acknowledge(url, receipt):
        if Queue.ack_error:
            raise Queue.ack_error
        Queue.calls.append(("ack", receipt))

    def retry_later(url, message):
        if Queue.retry_error:
            raise Queue.retry_error
        Queue.calls.append(("retry", message["ReceiptHandle"]))
        return 2

    monkeypatch.setattr(worker, "receive", receive)
    monkeypatch.setattr(worker, "acknowledge", acknowledge)
    monkeypatch.setattr(worker, "retry_later", retry_later)
    monkeypatch.setattr(worker.time, "sleep", Queue.sleeps.append)
    Queue.capture = Capture()
    Queue.logger = logging.getLogger("test-worker")
    Queue.logger.handlers = [Queue.capture]
    Queue.logger.setLevel(logging.DEBUG)
    Queue.logger.propagate = False
    return Queue


def delivery(body, receipt: str = "rh-1", count: int = 1) -> list:
    """Una entrega SQS con Body crudo, ReceiptHandle y contador de recepciones."""
    text = body if isinstance(body, str) else json.dumps(body)
    return [{"MessageId": "mid-1", "ReceiptHandle": receipt, "Body": text,
             "Attributes": {"ApproximateReceiveCount": str(count)},
             "MessageAttributes": {"EventId": {"DataType": "String",
                                               "StringValue": "evt-node-001"}}}]


def test_ack_only_after_handler_success(queue):
    """El ACK usa el ReceiptHandle actual y ocurre después del handler."""
    queue.messages = [delivery({"event_id": "evt-node-001"}, receipt="rh-actual")]
    worker.run("url", lambda event: queue.calls.append("handler"), queue.logger, once=True)
    assert queue.calls == ["receive", "handler", ("ack", "rh-actual")]
    done = queue.capture.by_message("mensaje_confirmado")[0]
    assert done.status == "acknowledged" and done.message_id == "mid-1"
    assert done.event_id == "evt-node-001" and done.attempt == 1


@pytest.mark.parametrize("error, level", [(RetryableError("x"), logging.WARNING),
                                          (PermanentError("x"), logging.ERROR)])
def test_handler_failure_keeps_message_and_schedules_backoff(queue, error, level):
    """Sin ACK; backoff sobre la entrega actual; nivel según el tipo de fallo."""
    def failing(event):
        raise error
    queue.messages = [delivery({"event_id": "evt-node-001"}, count=2)]
    worker.run("url", failing, queue.logger, once=True)
    assert queue.calls == ["receive", ("retry", "rh-1")]
    failed = queue.capture.by_message("procesamiento_fallido")[0]
    assert failed.levelno == level and failed.attempt == 2
    assert failed.status == type(error).__name__


@pytest.mark.parametrize("body", ["{no-json", "[1, 2]", "null", "\"texto\""])
def test_poison_message_does_not_stop_worker(queue, body):
    """F08 (lado worker): JSON corrupto o no-objeto -> retry, nunca handler ni ACK."""
    queue.messages = [delivery(body), delivery({"event_id": "evt-ok"}, receipt="rh-2")]
    handled = []
    worker.run("url", handled.append, queue.logger, once=True)
    worker.run("url", handled.append, queue.logger, once=True)  # El servicio sigue vivo.
    assert queue.calls == ["receive", ("retry", "rh-1"), "receive", ("ack", "rh-2")]
    assert handled == [{"event_id": "evt-ok"}]


def test_template_gaps_are_not_hidden(queue):
    """NotImplementedError se propaga: no se simula éxito ni se reprograma."""
    def pending(event):
        raise NotImplementedError("TODO")
    queue.messages = [delivery({"event_id": "evt-node-001"})]
    with pytest.raises(NotImplementedError):
        worker.run("url", pending, queue.logger, once=True)
    assert queue.calls == ["receive"]


def test_corrupt_ledger_stops_worker_without_ack(queue):
    """Estado local ilegible: CRITICAL y salida sin ACK ni DLQ forzada."""
    def corrupt(event):
        raise json.JSONDecodeError("bad", "{bad", 1)
    queue.messages = [delivery({"event_id": "evt-node-001"})]
    with pytest.raises(ValueError):
        worker.run("url", corrupt, queue.logger, once=True)
    assert queue.calls == ["receive"]
    assert queue.capture.by_message("estado_local_ilegible")[0].levelno == logging.CRITICAL


def test_retry_later_failure_keeps_message_unacked(queue):
    """Si change_message_visibility falla, no hay ACK y el bucle sigue."""
    queue.retry_error = ClientError({"Error": {"Code": "ReceiptHandleIsInvalid"}},
                                    "ChangeMessageVisibility")

    def failing(event):
        raise RetryableError("x")
    queue.messages = [delivery({"event_id": "evt-node-001"})]
    worker.run("url", failing, queue.logger, once=True)
    assert queue.calls == ["receive"]
    assert queue.capture.by_message("backoff_no_programado")[0].levelno == logging.ERROR


def test_ack_failure_is_logged_and_message_will_return(queue):
    """El trabajo ya está en el ledger; el ACK fallido solo se registra."""
    queue.ack_error = EndpointConnectionError(endpoint_url="http://localhost:4566")
    queue.messages = [delivery({"event_id": "evt-node-001"})]
    worker.run("url", lambda event: None, queue.logger, once=True)
    assert queue.capture.by_message("ack_fallido")[0].status == "EndpointConnectionError"
    assert not queue.capture.by_message("mensaje_confirmado")


def test_empty_queue_is_not_an_error(queue):
    """Sin mensajes: DEBUG y fin de la consulta única."""
    worker.run("url", lambda event: pytest.fail("no debe llamarse"), queue.logger, once=True)
    assert queue.capture.by_message("sin_mensajes")[0].levelno == logging.DEBUG


def test_transport_failure_once_is_reported(queue):
    """Con --once, una consulta fallida sale con error (código 1 en la CLI)."""
    queue.receive_errors = [EndpointConnectionError(endpoint_url="http://localhost:4566")]
    with pytest.raises(EndpointConnectionError):
        worker.run("url", lambda event: None, queue.logger, once=True)


def test_transport_failure_in_loop_waits_and_continues(queue):
    """En el bucle continuo: registrar, esperar 2 s y volver a consultar."""
    queue.receive_errors = [EndpointConnectionError(endpoint_url="http://localhost:4566"),
                            KeyboardInterrupt()]
    with pytest.raises(KeyboardInterrupt):  # Ctrl+C sube a la CLI.
        worker.run("url", lambda event: None, queue.logger)
    assert queue.sleeps == [2]
    assert queue.capture.by_message("consulta_fallida")[0].levelno == logging.ERROR
