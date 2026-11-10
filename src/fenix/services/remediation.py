"""Acciones convergentes a completar por integrante 3 y coordinadas por 5."""
from fenix.config.settings import client
from fenix.utils.state import load, save
from fenix.services.events import validate
from fenix.services.notifications import notify
from fenix.utils.errors import RetryableError, PermanentError


def repair(resource: dict, event_type: str) -> str:
    """Acerca un recurso autorizado a su estado deseado y verifica el resultado.

    Args:
        resource: Registro de inventario con type/id y etiquetas esperadas.
        event_type: instance.stopped o bucket.tags_missing ya validado.
    Returns:
        "repaired" si cambió el estado; "already_ok" si ya cumplía la política.
    Raises:
        RetryableError: La API falla temporalmente o el estado final aún no se observa.
        PermanentError: Recurso inexistente o configuración no autorizada.
        NotImplementedError: Mientras no se complete TODO I3-C.
    Side Effects:
        Arranca una instancia EC2 mock detenida o restaura tags de un bucket S3.
    Notes:
        No usar reboot_instances: repetir un reinicio no es convergencia.
        Un estado pending requiere otra recepción, sin esperas largas en el worker.
    """
    # TODO I3-C EC2: describe_instances por el InstanceId leído del inventario.
    # running -> already_ok; stopped -> start_instances; pending -> RetryableError.
    # Volver a consultar y exigir running; stopping/terminated -> error explicado.
    # TODO I3-C S3: get_bucket_tagging; solo NoSuchTagSet equivale a tags vacías.
    # Combinar tags actuales con obligatorias; put_bucket_tagging si hay desvío.
    # Volver a leer tags; preservar etiquetas ajenas a la política.
    # Clasificar ClientError por código: throttling/5xx transitorio, no todo ClientError.
    raise NotImplementedError("I3-C: implementar remediación convergente")


def handle(event: dict) -> None:
    """Completa reparación y notificación usando un registro durable por evento.

    Args:
        event: Objeto recibido de SQS, que se vuelve a validar aquí.
    Returns:
        None solo cuando reparación y aviso quedaron registrados como exitosos.
    Raises:
        ValidationError: Si el evento fue inyectado directamente a SQS y no es válido.
        RetryableError: Si la simulación o una dependencia temporal falla.
        PermanentError: Conflicto de event_id o recurso no autorizado.
        NotImplementedError: Mientras falte TODO I5-B.
    Side Effects:
        Actualiza recursos, data/events.json y el canal de comunicación.
    Notes:
        Un solo proceso modifica el ledger. Un timeout al notificar puede repetir
        un aviso: no existe una transacción común entre JSON, AWS y Telegram/Slack.
    """
    # TODO I5-B: cargar inventario y validar; cargar ledger por event_id.
    # Vincular event_id al contenido del evento; rechazar reutilización con otro contenido.
    # Guardar attempts incrementado antes de simular fallas; persiste entre reinicios.
    # Si attempts <= simulate_failures, lanzar RetryableError sin modificar recursos.
    # Si repair_done es falso: repair; guardar resultado y repair_done=True.
    # Si notified es falso: notify resumen; guardar notified=True después del éxito.
    # Si ambas etapas ya estaban completas, retornar sin efectos repetidos.
    # Cada save debe preservar el resto del ledger; no sobrescribir otros eventos.
    raise NotImplementedError("I5-B: integrar etapas y ledger durable")
