"""I6-A: inyección controlada de fallas sobre recursos del inventario."""
import pytest  # Fixtures y excepciones esperadas.
from botocore.exceptions import ClientError
from fenix.services import demo  # Módulo bajo prueba.
from fenix.utils.errors import PermanentError, RetryableError, ValidationError
from fenix.utils.state import save

TAGS = {"ManagedBy": "ppa2", "Project": "fenix", "GroupId": "grupo01", "Owner": "grupo01"}


class FakeEC2:
    """EC2 mínimo: estados sucesivos y registro de stop_instances."""

    def __init__(self, states, tags=None):
        self.states = list(states)
        self.tags = TAGS if tags is None else tags
        self.stops = []

    def describe_instances(self, InstanceIds):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        tags = [{"Key": k, "Value": v} for k, v in self.tags.items()]
        return {"Reservations": [{"Instances": [{"InstanceId": InstanceIds[0],
                                                 "State": {"Name": state}, "Tags": tags}]}]}

    def stop_instances(self, InstanceIds):
        self.stops.append(InstanceIds[0])


class FakeS3:
    """S3 mínimo con etiquetas en memoria."""

    def __init__(self, tags):
        self.tags = tags

    def get_bucket_tagging(self, Bucket):
        if not self.tags:
            raise ClientError({"Error": {"Code": "NoSuchTagSet"}}, "GetBucketTagging")
        return {"TagSet": [{"Key": k, "Value": v} for k, v in self.tags.items()]}

    def put_bucket_tagging(self, Bucket, Tagging):
        self.tags = {row["Key"]: row["Value"] for row in Tagging["TagSet"]}

    def delete_bucket_tagging(self, Bucket):
        self.tags = {}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """Inventario propio en una carpeta temporal y esperas instantáneas."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(demo.time, "sleep", lambda seconds: None)
    save("data/inventory.json", {
        "node-01": {"type": "ec2", "id": "i-lab", "expected_tags": TAGS},
        "bucket-01": {"type": "s3", "id": "fenix-grupo01-demo", "expected_tags": TAGS}})

    def use(fake):
        monkeypatch.setattr(demo, "client", lambda service: fake)
        return fake
    return use


@pytest.mark.parametrize("alias", ["node-99", "i-lab", "", None])
def test_only_inventory_aliases_are_accepted(lab, alias):
    """Un alias ajeno o un InstanceId libre no tocan ningún recurso."""
    fake = lab(FakeEC2(["running"]))
    with pytest.raises(ValidationError):
        demo.inject_fault(alias)
    assert fake.stops == []


def test_running_node_is_stopped_and_verified(lab):
    fake = lab(FakeEC2(["running", "stopping", "stopped"]))
    demo.inject_fault("node-01")
    assert fake.stops == ["i-lab"]


def test_stopped_node_is_left_as_is(lab):
    """Idempotente: la falla ya presente no se repite."""
    fake = lab(FakeEC2(["stopped"]))
    demo.inject_fault("node-01")
    assert fake.stops == []


def test_node_that_never_stops_fails_after_bounded_wait(lab):
    lab(FakeEC2(["running"]))
    with pytest.raises(RetryableError):
        demo.inject_fault("node-01")


@pytest.mark.parametrize("state, tags", [("terminated", None),
                                         ("running", {**TAGS, "GroupId": "otro-grupo"})])
def test_terminated_or_foreign_node_is_not_touched(lab, state, tags):
    fake = lab(FakeEC2([state], tags=tags))
    with pytest.raises(PermanentError):
        demo.inject_fault("node-01")
    assert fake.stops == []


def test_bucket_loses_only_owner(lab):
    fake = lab(FakeS3({**TAGS, "CostCenter": "lab-42"}))
    demo.inject_fault("bucket-01")
    assert fake.tags == {"ManagedBy": "ppa2", "Project": "fenix", "GroupId": "grupo01",
                         "CostCenter": "lab-42"}
    demo.inject_fault("bucket-01")  # Repetir no cambia nada.
    assert "Owner" not in fake.tags and len(fake.tags) == 4


def test_bucket_with_only_owner_ends_without_tags(lab):
    fake = lab(FakeS3({"Owner": "grupo01"}))
    demo.inject_fault("bucket-01")
    assert fake.tags == {}


def test_foreign_bucket_is_not_touched(lab):
    fake = lab(FakeS3({**TAGS, "GroupId": "otro-grupo"}))
    with pytest.raises(PermanentError):
        demo.inject_fault("bucket-01")
    assert fake.tags["Owner"] == "grupo01"
