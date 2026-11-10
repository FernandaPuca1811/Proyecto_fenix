"""Contrato de eventos de autorreparación; integrante 1."""
from fenix.utils.errors import ValidationError
from fenix.utils.validation import identifier, utc_text


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
        NotImplementedError: Mientras no se complete TODO I1-D.
    Notes:
        event_type solo permite instance.stopped y bucket.tags_missing.
        simulate_failures solo es de laboratorio: entero de 0 a 5, excluyendo bool.
    """
    # TODO I1-D: exigir dict, campos exactos y schema_version entero igual a 1.
    # Aplicar identifier a event_id/resource_id y utc_text a occurred_at.
    # Exigir resource_id en inventory; no aceptar ARN/InstanceId desde la petición.
    # Comprobar que el tipo del evento coincide con el tipo del recurso del inventario.
    # Rechazar bool como entero: type(value) is int en schema_version/simulate_failures.
    # Guardar el contenido asociado a event_id; el mismo ID con distinto evento es conflicto.
    raise NotImplementedError("I1-D: validar contrato del evento Fénix")
