"""Pruebas del integrante 1 para events.validate y events.fingerprint.

Cubren el caso F07 de la matriz (alias ajeno y claves extra) y los rechazos del
contrato. No requieren Docker ni red. Ejecutar: python -m pytest -m i1 -q
"""
import copy
import json
import re
from pathlib import Path

import pytest  # type: ignore[reportMissingImports]

from fenix.services.events import EVENT_FIELDS, fingerprint, validate
from fenix.utils.errors import ValidationError

pytestmark = pytest.mark.i1

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
GROUP_TAGS = {"ManagedBy": "ppa2", "Project": "fenix", "GroupId": "grupo01", "Owner": "grupo01"}
NODE_ID = "i-0123456789abcdef0"  # InstanceId ficticio: cumple el patrón de identifier.
BUCKET_NAME = "fenix-grupo01-demo"


@pytest.fixture
def inventory():
    """Inventario con la forma que deja setup-demo: alias -> recurso."""
    return {
        "node-01": {"type": "ec2", "id": NODE_ID, "security_group_id": "sg-0123456789abcdef0",
                    "expected_tags": dict(GROUP_TAGS)},
        "bucket-01": {"type": "s3", "id": BUCKET_NAME, "expected_tags": dict(GROUP_TAGS)},
    }


@pytest.fixture
def node_event():
    """Evento válido para la instancia."""
    return {"schema_version": 1, "event_id": "evt-node-001", "event_type": "instance.stopped",
            "resource_id": "node-01", "occurred_at": "2026-10-22T18:00:00Z", "simulate_failures": 0}


@pytest.fixture
def bucket_event():
    """Evento válido para el bucket."""
    return {"schema_version": 1, "event_id": "evt-bucket-001", "event_type": "bucket.tags_missing",
            "resource_id": "bucket-01", "occurred_at": "2026-10-22T18:00:00Z", "simulate_failures": 0}


# --------------------------------------------------------------------------- aceptación
def test_valid_node_event_is_accepted(node_event, inventory):
    """Evento correcto sobre node-01: se devuelve igual, como copia."""
    result = validate(node_event, inventory)
    assert result == node_event
    assert result is not node_event


def test_valid_bucket_event_is_accepted(bucket_event, inventory):
    """Evento correcto sobre bucket-01."""
    assert validate(bucket_event, inventory) == bucket_event


@pytest.mark.parametrize("name", ["node-event.json", "bucket-event.json"])
def test_shipped_examples_are_valid(name, inventory):
    """Los ejemplos de examples/ cumplen el contrato (si no, la demo fallaría por ellos)."""
    event = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    assert validate(event, inventory) == event


@pytest.mark.parametrize("failures", [0, 1, 2, 3, 4, 5])
def test_simulate_failures_range_is_inclusive(node_event, inventory, failures):
    """0 y 5 son los extremos permitidos."""
    node_event["simulate_failures"] = failures
    assert validate(node_event, inventory)["simulate_failures"] == failures


def test_validate_does_not_mutate_inputs(node_event, inventory):
    """validate no modifica el evento ni el inventario; la copia devuelta es independiente."""
    event_before, inventory_before = copy.deepcopy(node_event), copy.deepcopy(inventory)
    result = validate(node_event, inventory)
    result["event_id"] = "otro-id"  # Modificar la copia no afecta al original.
    assert node_event == event_before
    assert inventory == inventory_before


def test_occurred_at_keeps_original_text(node_event, inventory):
    """La copia conserva el texto original: debe seguir siendo serializable a JSON."""
    node_event["occurred_at"] = "2026-10-22T18:00:00+00:00"
    result = validate(node_event, inventory)
    assert result["occurred_at"] == "2026-10-22T18:00:00+00:00"
    json.dumps(result)  # No lanza: ningún valor se reemplazó por un datetime.


# --------------------------------------------------------------------------- rechazos
def reject(event, inventory):
    """Ejecuta validate y devuelve el ValidationError esperado."""
    with pytest.raises(ValidationError) as info:
        validate(event, inventory)
    return info.value


@pytest.mark.parametrize("not_an_event", [
    pytest.param([], id="lista-vacia"),
    pytest.param([{"schema_version": 1}], id="lista-con-evento"),
    pytest.param("texto", id="texto"),
    pytest.param(None, id="null"),
    pytest.param(42, id="numero"),
    pytest.param(True, id="bool"),
])
def test_rejects_json_values_that_are_not_objects(not_an_event, inventory):
    """Una lista JSON es JSON válido pero no un evento: el contrato exige un objeto."""
    reject(not_an_event, inventory)


@pytest.mark.parametrize("field", sorted(EVENT_FIELDS))
def test_rejects_each_missing_field(field, node_event, inventory):
    """Todos los campos son obligatorios; el mensaje nombra el que falta."""
    del node_event[field]
    assert field in str(reject(node_event, inventory))


def test_rejects_empty_object(inventory):
    """Un objeto vacío no tiene ningún campo obligatorio."""
    reject({}, inventory)


def test_rejects_extra_key(node_event, inventory):
    """Contrato cerrado: una clave adicional invalida el evento."""
    node_event["instance_id"] = NODE_ID
    reject(node_event, inventory)


def test_rejects_extra_key_without_echoing_its_name(node_event, inventory):
    """El nombre de una clave extra lo controla el remitente: no se copia al mensaje."""
    node_event["TOKEN_SECRETO_XYZ"] = "x"
    assert "SECRETO" not in str(reject(node_event, inventory))


@pytest.mark.parametrize("version", [
    pytest.param(True, id="bool-true"), pytest.param(False, id="bool-false"),
    pytest.param(0, id="cero"), pytest.param(2, id="otra-version"),
    pytest.param("1", id="texto"), pytest.param(1.0, id="decimal"), pytest.param(None, id="null"),
])
def test_rejects_invalid_schema_version(version, node_event, inventory):
    """schema_version es exactamente el entero 1; True == 1 en Python, pero no vale."""
    node_event["schema_version"] = version
    reject(node_event, inventory)


@pytest.mark.parametrize("event_id", ["ab", "x" * 33, "EVT-001", "../evt", 123, None, ["evt-001"]])
def test_rejects_invalid_event_id(event_id, node_event, inventory):
    """event_id cumple [a-z0-9-]{3,32}; el mensaje indica el campo."""
    node_event["event_id"] = event_id
    assert str(reject(node_event, inventory)).startswith("event_id")


@pytest.mark.parametrize("event_type", [
    "instance.started", "instance.terminated", "INSTANCE.STOPPED", "", " instance.stopped",
    None, 5, True, ["instance.stopped"], {"a": 1},
])
def test_rejects_unknown_event_type(event_type, node_event, inventory):
    """Solo instance.stopped y bucket.tags_missing; listas y dicts no deben dar TypeError."""
    node_event["event_type"] = event_type
    reject(node_event, inventory)


# --------------------------------------------------------------------------- recurso ajeno
@pytest.mark.parametrize("resource_id", [
    pytest.param("node-99", id="alias-ajeno"),
    pytest.param(NODE_ID, id="instance-id-real-del-inventario"),
    pytest.param(BUCKET_NAME, id="nombre-real-del-bucket"),
    pytest.param("sg-0123456789abcdef0", id="security-group-real"),
    pytest.param("arn:aws:ec2:us-east-1:000000000000:instance/i-1", id="arn"),
    pytest.param("../node-01", id="ruta"),
    pytest.param("NODE-01", id="mayusculas"),
    pytest.param("no", id="demasiado-corto"),
    pytest.param(5, id="numero"),
    pytest.param(None, id="null"),
    pytest.param(["node-01"], id="lista"),
])
def test_rejects_resource_not_in_inventory(resource_id, node_event, inventory):
    """F07: el webhook acepta aliases conocidos, nunca IDs elegidos por quien envía.

    Un InstanceId como i-0123456789abcdef0 cumple el patrón de identifier;
    lo frena la comprobación de pertenencia al inventario.
    """
    node_event["resource_id"] = resource_id
    reject(node_event, inventory)


def test_alien_resource_message_does_not_echo_value(node_event, inventory):
    """El alias rechazado lo escribe el remitente: no se reproduce en el mensaje."""
    node_event["resource_id"] = "token-secreto-xyz"
    assert "secreto" not in str(reject(node_event, inventory))


def test_rejects_event_type_incompatible_with_resource(node_event, bucket_event, inventory):
    """instance.stopped solo va con un recurso ec2 y bucket.tags_missing solo con s3."""
    node_event["event_type"] = "bucket.tags_missing"  # node-01 es ec2.
    reject(node_event, inventory)
    bucket_event["event_type"] = "instance.stopped"  # bucket-01 es s3.
    reject(bucket_event, inventory)


@pytest.mark.parametrize("bad_inventory", [
    pytest.param({}, id="vacio-sin-setup-demo"),
    pytest.param(None, id="none"),
    pytest.param([], id="lista"),
    pytest.param({"node-01": "i-0123456789abcdef0"}, id="alias-sin-registro"),
    pytest.param({"node-01": {"id": NODE_ID}}, id="registro-sin-type"),
    pytest.param({"node-01": {"type": "s3"}}, id="tipo-incorrecto"),
])
def test_fails_closed_with_unusable_inventory(bad_inventory, node_event):
    """Sin un inventario confiable no se autoriza nada."""
    reject(node_event, bad_inventory)


# --------------------------------------------------------------------------- campos restantes
@pytest.mark.parametrize("occurred_at", [
    pytest.param("2026-10-22T18:00:00", id="sin-zona"),
    pytest.param("2026-10-22T18:00:00-03:00", id="hora-argentina"),
    pytest.param("ayer", id="texto-libre"),
    pytest.param(None, id="null"),
    pytest.param(1761156000, id="epoch"),
])
def test_rejects_invalid_occurred_at(occurred_at, node_event, inventory):
    """occurred_at exige UTC explícito; el mensaje indica el campo."""
    node_event["occurred_at"] = occurred_at
    assert str(reject(node_event, inventory)).startswith("occurred_at")


@pytest.mark.parametrize("failures", [
    pytest.param(True, id="bool-true"), pytest.param(False, id="bool-false"),
    pytest.param(6, id="sobre-el-limite"), pytest.param(-1, id="negativo"),
    pytest.param(2.0, id="decimal"), pytest.param("2", id="texto"), pytest.param(None, id="null"),
])
def test_rejects_invalid_simulate_failures(failures, node_event, inventory):
    """Entero de 0 a 5 y nunca bool: False == 0 en Python, pero no es un entero del contrato."""
    node_event["simulate_failures"] = failures
    assert str(reject(node_event, inventory)).startswith("simulate_failures")


def test_rejections_leave_no_trace(node_event, inventory):
    """Tras un rechazo ni el evento ni el inventario cambian (sin efectos secundarios)."""
    node_event["resource_id"] = "node-99"
    event_before, inventory_before = copy.deepcopy(node_event), copy.deepcopy(inventory)
    reject(node_event, inventory)
    assert node_event == event_before and inventory == inventory_before


def test_validation_error_keeps_original_cause(node_event, inventory):
    """Los errores de campo encadenan la causa (raise ... from error)."""
    node_event["event_id"] = "NO"
    error = reject(node_event, inventory)
    assert isinstance(error.__cause__, ValidationError)


# --------------------------------------------------------------------------- fingerprint
def test_fingerprint_is_stable_hex_digest(node_event):
    """Misma entrada, misma huella; 64 caracteres hexadecimales."""
    assert fingerprint(node_event) == fingerprint(dict(node_event))
    assert re.fullmatch(r"[0-9a-f]{64}", fingerprint(node_event))


def test_fingerprint_ignores_key_order(node_event):
    """El orden de las claves no altera el contenido."""
    shuffled = dict(reversed(list(node_event.items())))
    assert list(shuffled) != list(node_event)
    assert fingerprint(shuffled) == fingerprint(node_event)


@pytest.mark.parametrize("field,value", [
    ("event_type", "bucket.tags_missing"), ("resource_id", "bucket-01"),
    ("occurred_at", "2026-10-22T18:00:01Z"), ("simulate_failures", 2), ("schema_version", 2),
])
def test_fingerprint_changes_with_any_field(field, value, node_event):
    """F15: el mismo event_id con otro contenido debe producir otra huella (conflicto)."""
    changed = dict(node_event, **{field: value})
    assert changed["event_id"] == node_event["event_id"]
    assert fingerprint(changed) != fingerprint(node_event)


def test_fingerprint_of_two_valid_events_differs(node_event, bucket_event, inventory):
    """Eventos distintos, huellas distintas, y la huella sale del evento ya validado."""
    assert fingerprint(validate(node_event, inventory)) != fingerprint(validate(bucket_event, inventory))
