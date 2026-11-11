"""Integrantes 3 y 5: caso de negocio (repair) y etapas durables (handle)."""
import copy  # Compara el ledger antes y después de un conflicto.
import urllib.request  # Comprueba si LocalStack está disponible.
import uuid  # Nombres de recursos exclusivos de cada ejecución.
import pytest  # Fixtures, marcas y excepciones esperadas.
from botocore.exceptions import ClientError, EndpointConnectionError
from fenix.config.settings import client
from fenix.services import cloud_support, remediation
from fenix.utils.errors import PermanentError, RetryableError, ValidationError
from fenix.utils.state import load, save

GROUP_TAGS = {"ManagedBy": "ppa2", "Project": "fenix", "GroupId": "grupo-test",
              "Owner": "grupo-test"}
NODE = {"type": "ec2", "id": "i-node", "expected_tags": GROUP_TAGS}
BUCKET = {"type": "s3", "id": "fenix-grupo-test-demo", "expected_tags": GROUP_TAGS}


def client_error(code: str, status: int = 400, operation: str = "Operation") -> ClientError:
    """Fabrica un ClientError con código y estado HTTP."""
    return ClientError({"Error": {"Code": code},
                        "ResponseMetadata": {"HTTPStatusCode": status}}, operation)


class FakeEC2:
    """EC2 mínimo: estado por consulta y registro de llamadas a start."""

    def __init__(self, states, tags=None, error=None):
        self.states = list(states)  # Estados devueltos en cada describe sucesivo.
        self.tags = GROUP_TAGS if tags is None else tags
        self.error = error
        self.started = 0

    def describe_instances(self, InstanceIds):
        if self.error:
            raise self.error
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        tags = [{"Key": k, "Value": v} for k, v in self.tags.items()]
        return {"Reservations": [{"Instances": [{"InstanceId": InstanceIds[0],
                                                 "State": {"Name": state}, "Tags": tags}]}]}

    def start_instances(self, InstanceIds):
        self.started += 1
        return {"StartingInstances": []}


class FakeS3:
    """S3 mínimo con etiquetas en memoria."""

    def __init__(self, tags=None, missing=False, put_error=None):
        self.tags = tags
        self.missing = missing
        self.put_error = put_error
        self.puts = 0

    def get_bucket_tagging(self, Bucket):
        if self.missing:
            raise client_error("NoSuchBucket", 404)
        if self.tags is None:
            raise client_error("NoSuchTagSet", 404)
        return {"TagSet": [{"Key": k, "Value": v} for k, v in self.tags.items()]}

    def put_bucket_tagging(self, Bucket, Tagging):
        if self.put_error:
            raise self.put_error
        self.puts += 1
        self.tags = {row["Key"]: row["Value"] for row in Tagging["TagSet"]}


def use(monkeypatch, fake):
    """Hace que remediation use el cliente falso."""
    monkeypatch.setattr(remediation, "client", lambda service: fake)


class TestRepairInstance:
    """Máquina de estados de EC2: start solo sobre stopped."""

    def test_stopped_is_started_and_verified(self, monkeypatch):
        fake = FakeEC2(["stopped", "running"])
        use(monkeypatch, fake)
        assert remediation.repair(NODE, "instance.stopped") == "repaired"
        assert fake.started == 1

    def test_running_converges_without_actions(self, monkeypatch):
        fake = FakeEC2(["running"])
        use(monkeypatch, fake)
        assert remediation.repair(NODE, "instance.stopped") == "already_ok"
        assert fake.started == 0

    @pytest.mark.parametrize("state", ["pending", "stopping"])
    def test_transition_is_retryable_without_start(self, monkeypatch, state):
        fake = FakeEC2([state])
        use(monkeypatch, fake)
        with pytest.raises(RetryableError):
            remediation.repair(NODE, "instance.stopped")
        assert fake.started == 0

    @pytest.mark.parametrize("state", ["terminated", "shutting-down"])
    def test_terminated_is_permanent_without_start(self, monkeypatch, state):
        fake = FakeEC2([state])
        use(monkeypatch, fake)
        with pytest.raises(PermanentError):
            remediation.repair(NODE, "instance.stopped")
        assert fake.started == 0

    def test_start_not_yet_running_is_retryable(self, monkeypatch):
        use(monkeypatch, FakeEC2(["stopped", "pending"]))
        with pytest.raises(RetryableError):
            remediation.repair(NODE, "instance.stopped")

    def test_foreign_group_is_never_started(self, monkeypatch):
        fake = FakeEC2(["stopped"], tags={**GROUP_TAGS, "GroupId": "otro-grupo"})
        use(monkeypatch, fake)
        with pytest.raises(PermanentError):
            remediation.repair(NODE, "instance.stopped")
        assert fake.started == 0

    @pytest.mark.parametrize("error, expected", [
        (client_error("RequestLimitExceeded", 503), RetryableError),
        (client_error("InternalError", 500), RetryableError),
        (EndpointConnectionError(endpoint_url="http://localhost:4566"), RetryableError),
        (client_error("InvalidInstanceID.NotFound", 400), PermanentError),
        (client_error("UnauthorizedOperation", 403), PermanentError),
    ])
    def test_errors_are_classified_by_cause(self, monkeypatch, error, expected):
        use(monkeypatch, FakeEC2(["stopped"], error=error))
        with pytest.raises(expected) as caught:
            remediation.repair(NODE, "instance.stopped")
        assert caught.value.__cause__ is error  # raise ... from error conserva la causa.


class TestRepairBucket:
    """Lectura, mezcla, escritura y verificación de etiquetas."""

    def test_missing_owner_is_restored_and_foreign_tags_preserved(self, monkeypatch):
        current = {k: v for k, v in GROUP_TAGS.items() if k != "Owner"}
        fake = FakeS3({**current, "CostCenter": "lab-42"})
        use(monkeypatch, fake)
        assert remediation.repair(BUCKET, "bucket.tags_missing") == "repaired"
        assert fake.tags == {**GROUP_TAGS, "CostCenter": "lab-42"}

    def test_no_tag_set_means_empty_tags(self, monkeypatch):
        fake = FakeS3(None)
        use(monkeypatch, fake)
        assert remediation.repair(BUCKET, "bucket.tags_missing") == "repaired"
        assert fake.tags == GROUP_TAGS

    def test_compliant_bucket_is_not_rewritten(self, monkeypatch):
        fake = FakeS3({**GROUP_TAGS, "Extra": "x"})
        use(monkeypatch, fake)
        assert remediation.repair(BUCKET, "bucket.tags_missing") == "already_ok"
        assert fake.puts == 0

    def test_wrong_owner_value_is_corrected(self, monkeypatch):
        fake = FakeS3({**GROUP_TAGS, "Owner": "nadie"})
        use(monkeypatch, fake)
        assert remediation.repair(BUCKET, "bucket.tags_missing") == "repaired"
        assert fake.tags["Owner"] == "grupo-test"

    def test_foreign_bucket_is_not_adopted(self, monkeypatch):
        fake = FakeS3({"GroupId": "otro-grupo"})
        use(monkeypatch, fake)
        with pytest.raises(PermanentError):
            remediation.repair(BUCKET, "bucket.tags_missing")
        assert fake.puts == 0

    def test_missing_bucket_is_permanent(self, monkeypatch):
        use(monkeypatch, FakeS3(missing=True))
        with pytest.raises(PermanentError):
            remediation.repair(BUCKET, "bucket.tags_missing")

    def test_throttled_write_is_retryable(self, monkeypatch):
        use(monkeypatch, FakeS3({}, put_error=client_error("SlowDown", 503)))
        with pytest.raises(RetryableError):
            remediation.repair(BUCKET, "bucket.tags_missing")


@pytest.mark.parametrize("resource, event_type", [
    (NODE, "bucket.tags_missing"), (BUCKET, "instance.stopped"), ({"type": "ec2"},
                                                                  "instance.stopped")])
def test_resource_and_event_must_match(monkeypatch, resource, event_type):
    """Un evento nunca repara un recurso de otro tipo o sin id."""
    use(monkeypatch, None)
    with pytest.raises(PermanentError):
        remediation.repair(resource, event_type)


# ---------------------------------------------------------------- handle (I5)

def event(event_id: str = "evt-node-500", **changes) -> dict:
    """Evento válido del contrato con cambios opcionales."""
    base = {"schema_version": 1, "event_id": event_id, "event_type": "instance.stopped",
            "resource_id": "node-01", "occurred_at": "2026-11-10T18:00:00Z",
            "simulate_failures": 0}
    return {**base, **changes}


@pytest.fixture
def stages(tmp_path, monkeypatch):
    """Ledger real en disco; reparación y aviso reemplazados por contadores.

    Returns:
        Objeto con listas repairs y notices y el resultado configurable.
    """
    monkeypatch.chdir(tmp_path)
    save("data/inventory.json", {"node-01": NODE, "bucket-01": BUCKET})

    class Stages:
        repairs = []
        notices = []
        repair_error = None
        notify_errors = []

    def fake_repair(resource, event_type):
        if Stages.repair_error:
            raise Stages.repair_error
        Stages.repairs.append((resource["id"], event_type))
        return "repaired"

    def fake_notify(text):
        if Stages.notify_errors:
            raise Stages.notify_errors.pop(0)
        Stages.notices.append(text)

    monkeypatch.setattr(remediation, "repair", fake_repair)
    monkeypatch.setattr(remediation, "notify", fake_notify)
    return Stages


def ledger() -> dict:
    """Lee data/events.json tal como lo vería un proceso nuevo."""
    return load("data/events.json")


def test_f02_full_ledger_after_repair_and_notice(stages):
    """F02 (sin LocalStack): ledger completo y una sola acción por etapa."""
    remediation.handle(event())
    record = ledger()["evt-node-500"]
    assert record["repair_done"] and record["notified"]
    assert record["repair_result"] == "repaired" and record["status"] == "completed"
    assert record["attempts"] == 1 and record["event"] == event()
    assert len(stages.repairs) == 1 and len(stages.notices) == 1
    assert "evt-node-500" in stages.notices[0]


def test_f11_duplicate_and_restart_do_not_repeat_actions(stages):
    """F11: repetir un evento completo (incluso tras reiniciar) no repara ni avisa."""
    remediation.handle(event())
    remediation.handle(event())  # Nueva entrega del mismo event_id.
    remediation.handle(dict(event()))  # Proceso nuevo: solo existe el ledger en disco.
    assert len(stages.repairs) == 1 and len(stages.notices) == 1
    assert ledger()["evt-node-500"]["attempts"] == 3


def test_f09_two_simulated_failures_then_repair(stages):
    """F09: los dos primeros intentos fallan antes de reparar; el tercero completa."""
    flaky = event("evt-node-509", simulate_failures=2)
    for attempt in (1, 2):
        with pytest.raises(RetryableError):
            remediation.handle(flaky)
        record = ledger()["evt-node-509"]  # La cuenta está en disco antes de fallar.
        assert record["attempts"] == attempt and record["status"] == "retry_pending"
        assert stages.repairs == []
    remediation.handle(flaky)
    assert ledger()["evt-node-509"]["status"] == "completed"
    assert len(stages.repairs) == 1


def test_f12_crash_between_repair_and_notice_resumes_only_notice(stages):
    """F12: el proceso cae después de guardar repair_done; al reiniciar solo avisa."""
    stages.notify_errors = [SystemExit(1)]  # Caída del proceso, no un error controlado.
    with pytest.raises(SystemExit):
        remediation.handle(event())
    record = ledger()["evt-node-500"]
    assert record["repair_done"] is True and record["notified"] is False
    remediation.handle(event())  # Reinicio del worker con la misma entrega.
    assert len(stages.repairs) == 1 and len(stages.notices) == 1
    assert ledger()["evt-node-500"]["status"] == "completed"


def test_f14_notice_429_then_success_without_new_repair(stages):
    """F14: aviso transitorio fallido; el reintento solo vuelve a avisar."""
    stages.notify_errors = [RetryableError("Telegram 429")]
    with pytest.raises(RetryableError):
        remediation.handle(event())
    record = ledger()["evt-node-500"]
    assert record["status"] == "notify_pending" and record["last_error"] == "RetryableError"
    remediation.handle(event())
    assert len(stages.repairs) == 1 and len(stages.notices) == 1
    assert ledger()["evt-node-500"]["notified"] is True


def test_f15_same_event_id_with_other_content_is_conflict(stages):
    """F15: conflicto; el primer registro y los recursos quedan intactos."""
    remediation.handle(event())
    before = copy.deepcopy(ledger())
    with pytest.raises(PermanentError):
        remediation.handle(event(occurred_at="2026-11-10T19:00:00Z"))
    assert ledger() == before
    assert len(stages.repairs) == 1


def test_permanent_repair_failure_is_recorded_without_notice(stages):
    """Un recurso irreparable deja rastro en el ledger y no se avisa éxito."""
    stages.repair_error = PermanentError("Instancia en estado terminated")
    with pytest.raises(PermanentError):
        remediation.handle(event())
    record = ledger()["evt-node-500"]
    assert record["repair_done"] is False and record["status"] == "repair_failed"
    assert stages.notices == []


def test_injected_invalid_event_has_no_effects(stages):
    """Un mensaje que no pasó por HTTP se valida en el worker."""
    with pytest.raises(ValidationError):
        remediation.handle(event(resource_id="node-99"))
    assert ledger() == {} and stages.repairs == []


def test_ledger_keeps_other_events(stages):
    """Cada guardado preserva el resto de los eventos."""
    remediation.handle(event("evt-node-501"))
    remediation.handle(event("evt-bucket-502", event_type="bucket.tags_missing",
                             resource_id="bucket-01"))
    assert set(ledger()) == {"evt-node-501", "evt-bucket-502"}


# ------------------------------------------------- repair contra LocalStack (I3)

def localstack_ready() -> bool:
    """True si el emulador responde en el puerto local."""
    try:
        with urllib.request.urlopen("http://localhost:4566/_localstack/health", timeout=2):
            return True
    except OSError:
        return False


@pytest.fixture
def lab():
    """Instancia mock y bucket exclusivos de esta prueba; limpia solo esos IDs.

    Returns:
        Diccionario con los registros de inventario node y bucket.
    """
    if not localstack_ready():
        pytest.skip("LocalStack no disponible en localhost:4566")
    suffix = uuid.uuid4().hex[:8]
    tags = {**GROUP_TAGS, "GroupId": f"t{suffix}", "Owner": f"t{suffix}"}
    vpc_id, image_id = cloud_support.mock_inputs()
    sg_id = cloud_support.create_group(f"fenix-t{suffix}-sg", vpc_id, tags)
    instance_id = cloud_support.create_instance(image_id, sg_id, f"fenix-t{suffix}", tags)
    bucket = cloud_support.create_bucket(f"fenix-t{suffix}-demo", tags)
    yield {"node": {"type": "ec2", "id": instance_id, "expected_tags": tags},
           "bucket": {"type": "s3", "id": bucket, "expected_tags": tags}}
    client("ec2").terminate_instances(InstanceIds=[instance_id])
    client("s3").delete_bucket(Bucket=bucket)
    client("ec2").delete_security_group(GroupId=sg_id)


def instance_state(instance_id: str) -> str:
    """Estado consultado por Boto3, no por el log del programa."""
    reservations = client("ec2").describe_instances(InstanceIds=[instance_id])["Reservations"]
    return reservations[0]["Instances"][0]["State"]["Name"]


@pytest.mark.integracion
def test_localstack_stopped_node_is_running_again(lab):
    """F02 por API: stopped -> start_instances -> running verificado."""
    node = lab["node"]
    client("ec2").stop_instances(InstanceIds=[node["id"]])
    assert instance_state(node["id"]) == "stopped"
    assert remediation.repair(node, "instance.stopped") == "repaired"
    assert instance_state(node["id"]) == "running"
    assert remediation.repair(node, "instance.stopped") == "already_ok"


@pytest.mark.integracion
def test_localstack_bucket_tags_restored_preserving_foreign(lab):
    """F03 por API: vuelve Owner y se conserva una etiqueta ajena."""
    bucket = lab["bucket"]
    s3 = client("s3")
    damaged = {k: v for k, v in bucket["expected_tags"].items() if k != "Owner"}
    damaged["CostCenter"] = "lab-42"
    s3.put_bucket_tagging(Bucket=bucket["id"],
                          Tagging={"TagSet": cloud_support.aws_tags(damaged)})
    assert remediation.repair(bucket, "bucket.tags_missing") == "repaired"
    tags = remediation.tag_map(s3.get_bucket_tagging(Bucket=bucket["id"])["TagSet"])
    assert tags == {**bucket["expected_tags"], "CostCenter": "lab-42"}
    assert remediation.repair(bucket, "bucket.tags_missing") == "already_ok"


@pytest.mark.integracion
def test_localstack_terminated_node_is_not_restarted(lab):
    """El mock acepta start sobre terminated; repair no debe pedirlo."""
    node = lab["node"]
    client("ec2").terminate_instances(InstanceIds=[node["id"]])
    with pytest.raises(PermanentError):
        remediation.repair(node, "instance.stopped")
    assert instance_state(node["id"]) == "terminated"
