"""Integrante 5: demostración integral sobre LocalStack (receptor -> SQS -> worker -> Boto3).

Cada ejecución usa un grupo y un prefijo de colas propios; la limpieza solo toca
los IDs que esta misma ejecución inventarió. No envía avisos reales (modo console).
"""
import functools  # Inyecta el logger del worker como lo hace la CLI.
import json  # Firma cuerpos exactos y lee logs JSON.
import time  # Plazos acotados para esperar al broker.
import urllib.request  # Comprueba que el emulador esté disponible.
import uuid  # Sufijo exclusivo por ejecución.
from pathlib import Path  # Lectura de logs del proceso.
import pytest  # Fixtures y marcas.
from botocore.exceptions import BotoCoreError, ClientError
from fastapi.testclient import TestClient  # Receptor real con su lifespan.
from fenix.config.settings import client
from fenix.services import demo, receiver, remediation, sqs, worker
from fenix.utils.crypto import signature
from fenix.utils.logging_json import setup
from fenix.utils.state import load

pytestmark = pytest.mark.integracion
SECRET = "clave-ficticia-solo-test-0002"  # Solo para este ensayo local.
DEADLINE = 120  # Segundos máximos de espera por condición; nunca pasa por timeout.


def localstack_ready() -> bool:
    """True si LocalStack responde en loopback."""
    try:
        with urllib.request.urlopen("http://localhost:4566/_localstack/health", timeout=2):
            return True
    except OSError:
        return False


class Lab:
    """Estado compartido del laboratorio de esta ejecución."""

    def __init__(self, group, work, dlq, inventory, http, logger):
        self.group, self.work, self.dlq = group, work, dlq
        self.inventory, self.http, self.logger = inventory, http, logger
        self.handler = functools.partial(remediation.handle, logger=logger)

    def event(self, name: str, **changes) -> dict:
        """Evento del contrato con event_id exclusivo de esta ejecución."""
        base = {"schema_version": 1, "event_id": f"evt-{self.group}-{name}",
                "event_type": "instance.stopped", "resource_id": "node-01",
                "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "simulate_failures": 0}
        return {**base, **changes}

    def send(self, event: dict):
        """Firma los bytes exactos y los envía al receptor, como la CLI send."""
        body = json.dumps(event).encode()
        stamp = int(time.time())
        header = f"t={stamp},v1={signature(SECRET, stamp, body)}"
        return self.http.post("/webhook", content=body, headers={"X-Webhook-Signature": header})

    def work_until(self, condition, deadline: int = DEADLINE) -> None:
        """Ejecuta consultas únicas del worker hasta cumplir la condición o fallar."""
        limit = time.monotonic() + deadline
        while not condition():
            assert time.monotonic() < limit, "condición no alcanzada dentro del plazo"
            worker.run(self.work, self.handler, self.logger, once=True)

    def pending(self) -> int:
        """Mensajes visibles más en vuelo de la cola de trabajo."""
        attributes = client("sqs").get_queue_attributes(QueueUrl=self.work, AttributeNames=[
            "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
        return sum(int(value) for value in attributes.values())

    def dlq_bodies(self) -> list[str]:
        """Lee la DLQ sin eliminar ni ocultar los mensajes."""
        response = client("sqs").receive_message(QueueUrl=self.dlq, MaxNumberOfMessages=10,
                                                 WaitTimeSeconds=1, VisibilityTimeout=0)
        return [message["Body"] for message in response.get("Messages", [])]


def record(event_id: str) -> dict:
    """Registro del ledger leído desde disco."""
    return load("data/events.json").get(event_id, {})


def instance_state(instance_id: str) -> str:
    """Estado real consultado por Boto3."""
    reservations = client("ec2").describe_instances(InstanceIds=[instance_id])["Reservations"]
    return reservations[0]["Instances"][0]["State"]["Name"]


def bucket_tags(bucket: str) -> dict:
    """Etiquetas reales del bucket consultadas por Boto3."""
    return remediation.tag_map(client("s3").get_bucket_tagging(Bucket=bucket)["TagSet"])


def cleanup(inventory: dict, urls: tuple) -> None:
    """Elimina únicamente los recursos inventariados por esta ejecución."""
    node, bucket = inventory.get("node-01", {}), inventory.get("bucket-01", {})
    steps = []
    if node.get("id"):
        steps.append(lambda: client("ec2").terminate_instances(InstanceIds=[node["id"]]))
    if bucket.get("id"):
        steps.append(lambda: client("s3").delete_bucket(Bucket=bucket["id"]))
    if node.get("security_group_id"):
        steps.append(lambda: client("ec2").delete_security_group(
            GroupId=node["security_group_id"]))
    steps += [lambda url=url: client("sqs").delete_queue(QueueUrl=url) for url in urls]
    for step in steps:
        try:
            step()
        except (ClientError, BotoCoreError) as error:  # Se informa, no se oculta.
            print("limpieza incompleta:", type(error).__name__)


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    """Colas, inventario y receptor reales en una carpeta y un grupo exclusivos."""
    if not localstack_ready():
        pytest.skip("LocalStack no disponible en localhost:4566")
    group = f"t{uuid.uuid4().hex[:8]}"
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(tmp_path_factory.mktemp("fenix-integracion"))
        patch.setenv("QUEUE_PREFIX", f"test-{group}")
        patch.setenv("GROUP_ID", group)
        patch.setenv("WEBHOOK_SECRET", SECRET)
        patch.setenv("NOTIFY_PROVIDER", "console")  # Sin spam en el canal real.
        work, dlq = sqs.ensure_queues(f"test-{group}")  # Integrante 2.
        inventory = {}
        logger, listener = setup("fenix-worker")
        try:
            inventory = demo.setup_demo(group)  # Integrante 2.
            with TestClient(receiver.app) as http:
                yield Lab(group, work, dlq, inventory, http, logger)
        finally:
            listener.stop()
            cleanup(inventory, (work, dlq))


def log_rows(prefix: str) -> list[dict]:
    """Líneas JSON de los archivos de log de un componente."""
    rows = []
    for path in Path("logs").glob(f"{prefix}-*.jsonl"):
        rows += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return rows


def wait_log(prefix: str, condition) -> list[dict]:
    """Espera brevemente a que el hilo del QueueListener escriba la línea buscada."""
    limit = time.monotonic() + 5
    while time.monotonic() < limit:
        rows = [row for row in log_rows(prefix) if condition(row)]
        if rows:
            return rows
        time.sleep(0.1)
    return []


def test_f02_stopped_node_is_repaired_notified_and_acked(lab):
    """F02: nodo detenido + evento firmado -> running, ledger completo, aviso y ACK."""
    node = lab.inventory["node-01"]
    demo.inject_fault("node-01")
    assert instance_state(node["id"]) == "stopped"
    event = lab.event("f02")
    response = lab.send(event)
    assert response.status_code == 202
    message_id = response.json()["message_id"]
    lab.work_until(lambda: record(event["event_id"]).get("status") == "completed"
                   and lab.pending() == 0)
    assert instance_state(node["id"]) == "running"
    stored = record(event["event_id"])
    assert stored["repair_done"] and stored["notified"] and stored["repair_result"] == "repaired"
    acked = wait_log("fenix-worker", lambda row: row["status"] == "acknowledged"
                     and row["event_id"] == event["event_id"])
    assert acked and acked[0]["message_id"] == message_id
    accepted = wait_log("fenix-receiver", lambda row: row["status"] == "accepted"
                        and row["message_id"] == message_id)
    assert accepted and accepted[0]["event_id"] == event["event_id"]


def test_f03_bucket_without_owner_recovers_tags(lab):
    """F03: vuelve Owner y se preserva una etiqueta ajena."""
    bucket = lab.inventory["bucket-01"]["id"]
    extra = {**bucket_tags(bucket), "CostCenter": "lab-42"}
    client("s3").put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": [
        {"Key": key, "Value": value} for key, value in extra.items()]})
    demo.inject_fault("bucket-01")
    assert "Owner" not in bucket_tags(bucket)
    event = lab.event("f03", event_type="bucket.tags_missing", resource_id="bucket-01")
    assert lab.send(event).status_code == 202
    lab.work_until(lambda: record(event["event_id"]).get("status") == "completed"
                   and lab.pending() == 0)
    tags = bucket_tags(bucket)
    assert tags["Owner"] == lab.group and tags["CostCenter"] == "lab-42"


def test_f11_completed_event_is_not_a_new_incident(lab):
    """F11: reenviar un evento completo no repara aunque el nodo vuelva a fallar."""
    node = lab.inventory["node-01"]
    demo.inject_fault("node-01")
    event = lab.event("f11")
    first = lab.send(event).json()["message_id"]
    lab.work_until(lambda: record(event["event_id"]).get("status") == "completed"
                   and lab.pending() == 0)
    demo.inject_fault("node-01")  # Nueva falla real, pero se reenvía el evento viejo.
    second = lab.send(event)
    assert second.status_code == 202 and second.json()["message_id"] != first
    lab.work_until(lambda: record(event["event_id"]).get("attempts") == 2
                   and lab.pending() == 0)
    assert instance_state(node["id"]) == "stopped"  # Sin nueva reparación.
    assert record(event["event_id"])["status"] == "completed"
    client("ec2").start_instances(InstanceIds=[node["id"]])  # Restituye el laboratorio.


def test_f09_backoff_2_and_4_then_repair_and_ack(lab, monkeypatch):
    """F09: simulate_failures=2 -> demoras 2 y 4 s, luego reparación y ACK."""
    delays = []
    original = worker.retry_later

    def recording(url, message):  # Observa el valor real devuelto por I2.
        delays.append(original(url, message))
        return delays[-1]
    monkeypatch.setattr(worker, "retry_later", recording)
    node = lab.inventory["node-01"]
    demo.inject_fault("node-01")
    event = lab.event("f09", simulate_failures=2)
    assert lab.send(event).status_code == 202
    lab.work_until(lambda: record(event["event_id"]).get("status") == "completed"
                   and lab.pending() == 0)
    assert delays == [2, 4]
    assert record(event["event_id"])["attempts"] == 3
    assert instance_state(node["id"]) == "running"


def test_f08_corrupt_json_injected_into_sqs_reaches_dlq(lab):
    """F08: JSON corrupto directo a SQS; el worker sigue y el broker lo aísla."""
    body = '{"event_id": "evt-roto", '  # Nunca pasó por el receptor.
    client("sqs").send_message(QueueUrl=lab.work, MessageBody=body)
    lab.work_until(lambda: body in lab.dlq_bodies())
    assert lab.pending() == 0


def test_f10_persistent_failure_reaches_dlq_without_ack(lab):
    """F10: falla persistente -> sin ACK, tres intentos y redrive automático."""
    node = lab.inventory["node-01"]
    before = instance_state(node["id"])
    event = lab.event("f10", simulate_failures=5)
    assert lab.send(event).status_code == 202
    lab.work_until(lambda: any(json.loads(text).get("event_id") == event["event_id"]
                               for text in lab.dlq_bodies() if text.startswith("{")
                               and text.endswith("}")))
    stored = record(event["event_id"])
    assert stored["attempts"] == 3 and stored["repair_done"] is False
    assert instance_state(node["id"]) == before


def test_f13_unavailable_queue_is_503(lab, monkeypatch):
    """F13: SQS no confirma (cola inexistente) -> 503 sin aceptación."""
    monkeypatch.setenv("QUEUE_PREFIX", f"inexistente-{lab.group}")
    response = lab.send(lab.event("f13"))
    assert response.status_code == 503
    assert "accepted" not in response.text
