"""Contrato de eventos de autorreparación; integrante 1.

Es la única puerta de entrada de datos externos al sistema. Se invoca dos veces:
en el receptor HTTP (ValidationError -> 422) y otra vez en el worker, porque un
mensaje puede llegar a SQS sin pasar por HTTP.
"""
import hashlib  # Huella estable del contenido de un evento.
import json  # Serialización canónica para esa huella.
from fenix.utils.errors import ValidationError
from fenix.utils.validation import identifier, utc_text

SCHEMA_VERSION = 1  # Única versión del contrato; debe ser el entero 1.
MAX_SIMULATED_FAILURES = 5  # Tope de fallas simuladas de laboratorio (0 a 5).
# Campos exactos del contrato cerrado: ni más ni menos.
EVENT_FIELDS = frozenset(
    {"schema_version", "event_id", "event_type", "resource_id", "occurred_at", "simulate_failures"}
)
# event_type admitido -> tipo de recurso del inventario con el que es compatible.
EVENT_RESOURCE_TYPES = {"instance.stopped": "ec2", "bucket.tags_missing": "s3"}


def _check(field: str, validator, value):
    """Ejecuta un validador de validation.py añadiendo el nombre del campo.

    Args:
        field: Campo del evento que se valida (dato propio, nunca del remitente).
        validator: Función que devuelve el valor o lanza ValidationError.
        value: Valor recibido en el evento.
    Returns:
        Lo que devuelva el validador.
    Raises:
        ValidationError: Con el campo antepuesto y la causa original encadenada.
    """
    try:
        return validator(value)
    except ValidationError as error:  # El mensaje del validador no dice qué campo falló.
        raise ValidationError(f"{field}: {error}") from error


def _strict_int(field: str, value, low: int, high: int) -> int:
    """Exige un entero verdadero dentro de un rango cerrado.

    Args:
        field: Nombre del campo, para el mensaje.
        value: Valor recibido.
        low: Mínimo permitido (incluido).
        high: Máximo permitido (incluido).
    Returns:
        El mismo entero.
    Raises:
        ValidationError: Si no es int exacto (bool y float se rechazan) o está fuera de rango.
    Notes:
        En Python bool hereda de int y True == 1, por eso isinstance no alcanza:
        se compara type(value) con int.
    """
    if type(value) is not int:  # Rechaza True/False, 1.0, "1" y None.
        raise ValidationError(f"{field} debe ser un entero (no bool ni decimal)")
    if not low <= value <= high:
        raise ValidationError(f"{field} debe estar entre {low} y {high}")
    return value


def validate(event: dict, inventory: dict) -> dict:
    """Valida esquema cerrado y resuelve exclusivamente aliases autorizados.

    Args:
        event: JSON objeto con schema_version, event_id, event_type, resource_id,
            occurred_at y simulate_failures.
        inventory: Mapa alias -> recurso, preparado por setup-demo y revisado por el grupo.
    Returns:
        Copia validada del evento; no modifica inventory.
    Raises:
        ValidationError: Tipo, campo, timestamp o recurso no permitido.
    Notes:
        event_type solo permite instance.stopped y bucket.tags_missing.
        simulate_failures solo es de laboratorio: entero de 0 a 5, excluyendo bool.
        resource_id es un alias (node-01, bucket-01), nunca un InstanceId ni un ARN:
        un InstanceId como i-0abc123 cumple el patrón de identifier, pero no es una
        clave del inventario, así que la comprobación de pertenencia lo rechaza.
        Los mensajes nombran solo campos del contrato; no reproducen valores ni claves
        enviados por el remitente.
    """
    if not isinstance(event, dict):  # Una lista, texto o null JSON son JSON válido, no un evento.
        raise ValidationError("El evento debe ser un objeto JSON")
    if not isinstance(inventory, dict):  # Falla cerrada: sin inventario no se autoriza nada.
        raise ValidationError("Inventario no disponible o con formato inválido")

    keys = set(event)  # Contrato cerrado: se comparan los conjuntos de claves.
    missing = EVENT_FIELDS - keys
    if missing:
        raise ValidationError("Faltan campos: " + ", ".join(sorted(missing)))
    extra = keys - EVENT_FIELDS
    if extra:  # Solo se informa la cantidad: los nombres los controla el remitente.
        raise ValidationError(f"Campos no permitidos en el evento: {len(extra)}")

    version = event["schema_version"]
    if type(version) is not int or version != SCHEMA_VERSION:  # type(): excluye True.
        raise ValidationError(f"schema_version debe ser el entero {SCHEMA_VERSION}")
    _check("event_id", identifier, event["event_id"])
    event_type = event["event_type"]
    # isinstance antes del "in": una lista o dict como clave lanzaría TypeError (no hashable).
    if not isinstance(event_type, str) or event_type not in EVENT_RESOURCE_TYPES:
        raise ValidationError("event_type no admitido")
    resource_id = _check("resource_id", identifier, event["resource_id"])
    _check("occurred_at", utc_text, event["occurred_at"])
    _strict_int("simulate_failures", event["simulate_failures"], 0, MAX_SIMULATED_FAILURES)

    resource = inventory.get(resource_id)  # Solo aliases presentes en el inventario.
    if not isinstance(resource, dict):
        raise ValidationError("resource_id no pertenece al inventario autorizado")
    if resource.get("type") != EVENT_RESOURCE_TYPES[event_type]:  # ec2 <-> stopped, s3 <-> tags.
        raise ValidationError("event_type incompatible con el tipo del recurso")
    return dict(event)  # Copia superficial: todos los valores validados son int o str.


def fingerprint(event: dict) -> str:
    """Calcula una huella estable del contenido de un evento ya validado.

    Args:
        event: Evento devuelto por validate (solo valores int y str).
    Returns:
        SHA-256 hexadecimal (64 caracteres) del JSON canónico del evento.
    Raises:
        TypeError: Si el evento contiene valores no serializables a JSON.
    Notes:
        El mismo event_id con otro contenido debe tratarse como conflicto (F15).
        La huella no depende del orden de las claves, pero cambia si cambia
        cualquier campo, incluidos occurred_at y simulate_failures. El integrante 5
        puede guardarla en el ledger junto al event_id y compararla al reaparecer.
    """
    canonical = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()  # ASCII: ensure_ascii=True.