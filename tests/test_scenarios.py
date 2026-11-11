"""Guía ejecutable del integrante 6; sustituir cada pendiente por aserciones reales."""
import importlib  # Recarga remediation para representar un proceso nuevo.
import json  # Serializa el Body de la entrega simulada.
import logging  # Logger mínimo para run().
import pytest  # Mantiene visibles los casos sin implementar.
from fenix.services import remediation, worker
from fenix.utils.errors import RetryableError
from fenix.utils.state import load, save


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
        NotImplementedError: Hasta implementar el ensayo con espera total acotada.
    Side Effects:
        Crea colas de prueba exclusivas y las limpia después de guardar evidencia.
    Notes:
        Usar un prefijo de test nuevo y no purgar colas del laboratorio del grupo.
    """
    # TODO I6-D: publicar JSON corrupto en una cola con RedrivePolicy real.
    # Consumir con el worker y consultar DLQ con deadline total de 120 segundos.
    # Comparar el Body original; un contador aproximado por sí solo no demuestra redrive.
    # Si se agota el deadline, fallar con diagnóstico; no pasar el test por timeout.
    raise NotImplementedError("I6-D: verificar redrive real en LocalStack")
