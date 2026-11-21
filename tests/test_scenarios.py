"""Guía ejecutable del integrante 6; sustituir cada pendiente por aserciones reales."""
import importlib  # Recarga remediation para representar un proceso nuevo.
import json  # Serializa el Body de la entrega simulada.
import logging  # Logger mínimo para run().
import time  # Deadline total del ensayo de redrive.
import urllib.request  # Comprueba que LocalStack esté disponible.
import uuid  # Prefijo de colas exclusivo de cada ejecución.
import pytest  # Mantiene visibles los casos sin implementar.
from fenix.config.settings import client
from fenix.services import remediation, sqs, worker
from fenix.utils.errors import RetryableError
from fenix.utils.state import load, save

REDRIVE_DEADLINE = 120  # Segundos máximos para observar el mensaje en la DLQ.


class CountingEC2:
    """Cliente EC2 falso: cuenta start_instances y cambia el estado como el emulador.

    Args:
        state: Estado inicial de la instancia.
    """

    def __init__(self, state: str):
        self.state = state  # Estado observable por describe_instances.
        self.starts = 0  # Acciones efectivas sobre el recurso.

    def describe_instances(self, InstanceIds):
        """Devuelve la instancia pedida con su estado actual."""
        return {"Reservations": [{"Instances": [{"InstanceId": InstanceIds[0],
                                                 "State": {"Name": self.state}, "Tags": []}]}]}

    def start_instances(self, InstanceIds):
        """Arranca la instancia y cuenta la acción."""
        self.starts += 1
        self.state = "running"


def test_duplicate_is_idempotent(tmp_path, monkeypatch):
    """Comprueba que repetir un evento no repite recursos ni acciones completadas.

    Args:
        tmp_path: Carpeta aislada con inventario y ledger propios.
        monkeypatch: Reemplaza cliente EC2 y aviso por contadores.
    Returns:
        None cuando dos ejecuciones conservan el estado final esperado.
    Raises:
        AssertionError: Si la segunda ejecución vuelve a arrancar o avisar.
    Notes:
        Entre ambas ejecuciones se recarga el módulo: solo sobrevive el ledger en disco.
    """
    monkeypatch.chdir(tmp_path)  # data/ exclusivo del ensayo.
    save("data/inventory.json", {"node-01": {"type": "ec2", "id": "i-fijo", "expected_tags": {}}})
    event = {"schema_version": 1, "event_id": "evt-dup-001", "event_type": "instance.stopped",
             "resource_id": "node-01", "occurred_at": "2026-11-10T18:00:00Z",
             "simulate_failures": 0}
    ec2, notices = CountingEC2("stopped"), []
    for _ in range(2):  # Primera ejecución y reinicio con la misma entrega.
        module = importlib.reload(remediation)  # Sin estado en memoria del intento anterior.
        monkeypatch.setattr(module, "client", lambda service: ec2)
        monkeypatch.setattr(module, "notify", notices.append)
        module.handle(dict(event))
        record = load("data/events.json")["evt-dup-001"]  # Relectura desde disco.
        assert record["repair_done"] and record["notified"]
    assert ec2.starts == 1 and len(notices) == 1  # Una sola acción efectiva por etapa.
    assert record["attempts"] == 2 and record["repair_result"] == "repaired"
    assert ec2.state == "running"


def test_ack_is_after_success(monkeypatch):
    """Verifica que un handler fallido jamás confirma la entrega de SQS.

    Args:
        monkeypatch: Sustituye solo el transporte SQS; run() es el real.
    Returns:
        None si fallo=retry sin ACK y éxito=ACK exactamente al finalizar.
    Raises:
        AssertionError: Si hay ACK tras un fallo o antes del handler.
    """
    calls = []  # Orden observado de handler, ACK y backoff.
    message = {"MessageId": "mid-fijo", "ReceiptHandle": "rh-fijo",
               "Body": json.dumps({"event_id": "evt-ack-001"}),
               "Attributes": {"ApproximateReceiveCount": "1"}}
    monkeypatch.setattr(worker, "receive", lambda url: [message])
    monkeypatch.setattr(worker, "acknowledge", lambda url, receipt: calls.append(("ack", receipt)))
    monkeypatch.setattr(worker, "retry_later",
                        lambda url, msg: calls.append(("retry", msg["ReceiptHandle"])) or 2)
    logger = logging.getLogger("test-scenarios")

    def failing(event):  # Falla transitoria del negocio.
        calls.append("handler")
        raise RetryableError("dependencia no disponible")
    worker.run("url", failing, logger, once=True)
    assert calls == ["handler", ("retry", "rh-fijo")]  # Cero ACK.
    calls.clear()
    worker.run("url", lambda event: calls.append("handler"), logger, once=True)
    assert calls == ["handler", ("ack", "rh-fijo")]  # Handler primero, ACK después.


@pytest.mark.integracion
def test_redrive_with_localstack():
    """Comprueba DLQ efectiva usando el broker, no una lista simulada en Python.

    Returns:
        None si un mensaje venenoso termina en la DLQ y el worker sigue vivo.
    Raises:
        AssertionError: Si el Body no aparece en la DLQ dentro de 120 s.
    Side Effects:
        Crea colas de prueba exclusivas y las limpia después de guardar evidencia.
    Notes:
        Usar un prefijo de test nuevo y no purgar colas del laboratorio del grupo.
        La evidencia (MessageId, Body y recepciones) se imprime; ver con pytest -rP.
    """
    try:  # Sin emulador no hay broker que probar.
        with urllib.request.urlopen("http://localhost:4566/_localstack/health", timeout=2):
            pass
    except OSError:
        pytest.skip("LocalStack no disponible en localhost:4566")
    sqs_client = client("sqs")
    work, dlq = sqs.ensure_queues(f"test-redrive-{uuid.uuid4().hex[:8]}")  # RedrivePolicy real.
    body = '{"event_id": "evt-veneno", '  # JSON corrupto inyectado sin pasar por HTTP.
    handled = []  # El handler nunca debe recibir un Body corrupto.
    logger = logging.getLogger("test-redrive")
    try:
        sent = sqs_client.send_message(QueueUrl=work, MessageBody=body)["MessageId"]
        limit = time.monotonic() + REDRIVE_DEADLINE
        found = None
        while found is None:
            assert time.monotonic() < limit, (
                f"El mensaje {sent} no llegó a la DLQ en {REDRIVE_DEADLINE} s")
            worker.run(work, handled.append, logger, once=True)  # El worker sigue vivo.
            response = sqs_client.receive_message(  # Lectura sin borrar ni ocultar.
                QueueUrl=dlq, MaxNumberOfMessages=10, WaitTimeSeconds=1, VisibilityTimeout=0,
                MessageSystemAttributeNames=["ApproximateReceiveCount"])
            found = next((m for m in response.get("Messages", []) if m["Body"] == body), None)
        assert handled == []  # Nunca llegó al negocio ni se confirmó.
        print(json.dumps({"evidencia": "dlq", "message_id_enviado": sent,
                          "message_id_dlq": found["MessageId"], "body": found["Body"],
                          "recepciones": found["Attributes"]["ApproximateReceiveCount"]}))
    finally:  # Solo se borran las colas creadas por este ensayo.
        for url in (work, dlq):
            sqs_client.delete_queue(QueueUrl=url)
