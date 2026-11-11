"""Integrante 3: HMAC y receptor HTTP (F04, F05, F06, F07, F13 y límites del contrato)."""
import json  # Construye cuerpos y lee los logs generados.
import time  # Fija timestamps relativos al reloj actual.
import pytest  # Fixtures, parametrización y excepciones esperadas.
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi.testclient import TestClient  # Ejecuta la app con su lifespan real.
from fenix.services import receiver  # Módulo bajo prueba.
from fenix.utils.crypto import signature, verify
from fenix.utils.errors import ValidationError
from fenix.utils.state import save

SECRET = "clave-ficticia-solo-test-0001"  # Fixture pública; no es un secreto real.
EVENT = {"schema_version": 1, "event_id": "evt-node-901", "event_type": "instance.stopped",
         "resource_id": "node-01", "occurred_at": "2026-11-10T18:00:00Z",
         "simulate_failures": 0}
INVENTORY = {
    "node-01": {"type": "ec2", "id": "i-0000000000000test", "expected_tags": {}},
    "bucket-01": {"type": "s3", "id": "fenix-test-demo", "expected_tags": {}},
}


def signed(body: bytes, timestamp: int | None = None, secret: str = SECRET) -> dict:
    """Arma la cabecera que enviaría la CLI para estos bytes exactos.

    Args:
        body: Bytes que se firman y se envían sin reserializar.
        timestamp: Epoch del emisor; None usa la hora actual.
        secret: Secreto compartido con el receptor.
    Returns:
        Diccionario de cabeceras HTTP.
    """
    stamp = int(time.time()) if timestamp is None else timestamp
    return {"X-Webhook-Signature": f"t={stamp},v1={signature(secret, stamp, body)}"}


@pytest.fixture
def published(tmp_path, monkeypatch):
    """Laboratorio aislado: inventario propio, secreto ficticio y SQS reemplazado.

    Args:
        tmp_path: Carpeta temporal para data/ y logs/.
        monkeypatch: Restaura entorno y funciones al terminar.
    Returns:
        Lista donde se anotan los eventos que llegarían a SQS.
    """
    monkeypatch.chdir(tmp_path)  # data/ y logs/ del test, no los del grupo.
    monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("QUEUE_PREFIX", "test-fenix")
    save("data/inventory.json", INVENTORY)
    sent = []
    monkeypatch.setattr(receiver, "queue_url", lambda prefix: f"cola://{prefix}-events")

    def fake_publish(url, event):  # Frontera SQS: se anota lo que se publicaría.
        sent.append(event)
        return f"mid-{len(sent)}"
    monkeypatch.setattr(receiver, "publish", fake_publish)
    return sent


@pytest.fixture
def http(published):
    """Cliente HTTP con lifespan (logging JSON del receptor activo).

    Args:
        published: Fixture que prepara el laboratorio.
    Returns:
        TestClient abierto durante la prueba.
    """
    with TestClient(receiver.app) as client:
        yield client


def post(http, body: bytes, headers: dict):
    """Envía bytes crudos al webhook.

    Args:
        http: TestClient.
        body: Cuerpo exacto.
        headers: Cabeceras, incluida la firma si corresponde.
    Returns:
        Respuesta HTTP.
    """
    return http.post("/webhook", content=body, headers=headers)


def test_valid_event_is_accepted_after_publish(http, published):
    """202 con event_id y message_id solo después de publicar."""
    body = json.dumps(EVENT).encode()
    response = post(http, body, signed(body))
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "event_id": "evt-node-901",
                               "message_id": "mid-1"}
    assert published == [EVENT]  # Se publica la copia validada.


def test_f04_missing_signature_is_401(http, published):
    """F04: sin cabecera no hay publicación."""
    response = post(http, json.dumps(EVENT).encode(), {})
    assert response.status_code == 401
    assert published == []


def test_f04_altered_signature_is_401(http, published):
    """F04: firma de otro cuerpo."""
    body = json.dumps(EVENT).encode()
    other = signed(b'{"otro": 1}')
    response = post(http, body, other)
    assert response.status_code == 401
    assert published == []


def test_f04_timestamp_one_hour_old_is_401(http, published):
    """F04: firma correcta pero fuera de la ventana de 300 s."""
    body = json.dumps(EVENT).encode()
    response = post(http, body, signed(body, int(time.time()) - 3600))
    assert response.status_code == 401
    assert published == []


def test_f05_same_json_different_bytes_is_401(http, published):
    """F05: mismos datos JSON con otros bytes después de firmar."""
    body = json.dumps(EVENT, separators=(",", ":")).encode()
    headers = signed(body)
    pretty = json.dumps(EVENT, indent=2).encode()  # Mismo contenido, otros bytes.
    assert json.loads(pretty) == json.loads(body)
    response = post(http, pretty, headers)
    assert response.status_code == 401
    assert published == []


@pytest.mark.parametrize("body", [b'{"schema_version": 1,', b'\xff\xfe{"a": 1}',
                                  '{"a": 1}'.encode("utf-16")])
def test_f06_signed_broken_json_or_utf8_is_400(http, published, body):
    """F06: JSON roto, UTF-8 inválido o UTF-16 con firma válida."""
    response = post(http, body, signed(body))
    assert response.status_code == 400
    assert published == []


def test_f06_signed_json_list_is_422(http, published):
    """F06: una lista JSON firmada no es un evento."""
    body = json.dumps([EVENT]).encode()
    response = post(http, body, signed(body))
    assert response.status_code == 422
    assert published == []


@pytest.mark.parametrize("change", [
    {"resource_id": "node-99"}, {"extra": "x"}, {"resource_id": "i-0000000000000test"},
    {"simulate_failures": True}])
def test_f07_contract_violations_are_422(http, published, change):
    """F07: alias ajeno, ID real en lugar de alias, clave extra y bool como entero."""
    body = json.dumps({**EVENT, **change}).encode()
    response = post(http, body, signed(body))
    assert response.status_code == 422
    assert published == []


def test_body_over_16_kib_is_413_before_signature(http, published):
    """413 aunque la firma sea válida: el tamaño se controla primero."""
    body = b" " * 16385
    response = post(http, body, signed(body))
    assert response.status_code == 413
    assert published == []


@pytest.mark.parametrize("failure", [
    ClientError({"Error": {"Code": "AWS.SimpleQueueService.NonExistentQueue"}}, "GetQueueUrl"),
    EndpointConnectionError(endpoint_url="http://localhost:4566"),
])
def test_f13_sqs_unavailable_is_503(http, published, monkeypatch, failure):
    """F13: si SQS no confirma, no hay aceptación falsa."""
    def broken(prefix):
        raise failure
    monkeypatch.setattr(receiver, "queue_url", broken)
    body = json.dumps(EVENT).encode()
    response = post(http, body, signed(body))
    assert response.status_code == 503
    assert "accepted" not in response.text


def test_receiver_does_not_start_without_secret(published, monkeypatch):
    """Arranque con secreto corto: cierre controlado antes de atender."""
    monkeypatch.setenv("WEBHOOK_SECRET", "corto")
    with pytest.raises(receiver.ReceiverConfigError):
        with TestClient(receiver.app):
            pass


def test_logs_are_json_without_secrets(published, tmp_path):
    """Los logs del receptor son JSON correlacionado y no contienen secreto ni firma."""
    body = json.dumps(EVENT).encode()
    headers = signed(body)
    with TestClient(receiver.app) as http:
        post(http, body, headers)
        post(http, body, {})
    text = "".join(path.read_text(encoding="utf-8")
                   for path in (tmp_path / "logs").glob("fenix-receiver-*.jsonl"))
    rows = [json.loads(line) for line in text.splitlines()]
    accepted = [row for row in rows if row["status"] == "accepted"]
    assert accepted and accepted[0]["event_id"] == "evt-node-901"
    assert accepted[0]["message_id"] == "mid-1"
    assert any(row["status"] == "401" for row in rows)
    assert SECRET not in text
    assert headers["X-Webhook-Signature"].split("v1=")[1] not in text


class TestVerify:
    """Contrato estricto de la cabecera t=<epoch>,v1=<64 hex>."""

    body = b'{"x":1}'

    def header(self, stamp=1000, **overrides):
        """Cabecera válida para self.body con overrides de texto."""
        digest = overrides.get("v1", signature(SECRET, stamp, self.body))
        return overrides.get("raw", f"t={stamp},v1={digest}")

    def test_valid_header_and_window_limits(self):
        verify(SECRET, self.header(), self.body, now=1000)
        verify(SECRET, self.header(), self.body, now=1300)  # Límite incluido.
        verify(SECRET, self.header(), self.body, now=700)  # Firma 300 s en el futuro.
        verify(SECRET, f"v1={signature(SECRET, 1000, self.body)},t=1000", self.body, now=1000)

    @pytest.mark.parametrize("now", [1301, 699])
    def test_outside_window(self, now):
        with pytest.raises(ValidationError):
            verify(SECRET, self.header(), self.body, now=now)

    @pytest.mark.parametrize("raw", [
        "", "t=1000", "v1=" + "a" * 64, "t=1000,t=1000,v1=" + "a" * 64,
        "t=1000,v1=" + "a" * 64 + ",v2=" + "b" * 64, "t=1000;v1=" + "a" * 64,
        " t=1000,v1=" + "a" * 64, "t= 1000,v1=" + "a" * 64, "t=+1000,v1=" + "a" * 64,
        "t=01000,v1=" + "a" * 64, "t=1_000,v1=" + "a" * 64, "t=1000,v1=" + "a" * 63,
        "t=1000,v1=" + "g" * 64, "t=1000,v1=" + "A" * 64, "t=1000,v1",
    ])
    def test_ambiguous_or_malformed_header(self, raw):
        with pytest.raises(ValidationError):
            verify(SECRET, raw, self.body, now=1000)

    def test_uppercase_digest_is_rejected(self):
        digest = signature(SECRET, 1000, self.body).upper()
        with pytest.raises(ValidationError):
            verify(SECRET, f"t=1000,v1={digest}", self.body, now=1000)

    def test_wrong_secret_or_timestamp_inside_signature(self):
        with pytest.raises(ValidationError):
            verify("otra-clave-ficticia-0001", self.header(), self.body, now=1000)
        digest = signature(SECRET, 999, self.body)  # Firma de otro timestamp.
        with pytest.raises(ValidationError):
            verify(SECRET, f"t=1000,v1={digest}", self.body, now=1000)
