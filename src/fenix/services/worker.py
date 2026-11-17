"""Plantilla central de integración: un worker, un mensaje por vez."""
import json  # Convierte Body solo dentro del control por mensaje.
import logging  # Niveles distintos para fallos transitorios y permanentes.
import time  # Evita un bucle intensivo si el emulador no responde.
from botocore.exceptions import BotoCoreError, ClientError
from fenix.services.sqs import receive, acknowledge, retry_later
from fenix.utils.errors import ProjectError, RetryableError, ValidationError

TRANSPORT_PAUSE = 2  # Segundos de espera tras un fallo de transporte.


def message_context(message: dict) -> dict:
    """Extrae el contexto público de un mensaje antes de interpretar su cuerpo.

    Args:
        message: Mensaje crudo devuelto por receive_message.
    Returns:
        message_id, event_id (atributo EventId si existe) y attempt.
    Notes:
        Se arma antes de json.loads para correlacionar incluso un Body corrupto.
    """
    attribute = message.get("MessageAttributes", {}).get("EventId", {})
    try:  # SQS lo informa como texto; un valor raro no debe frenar el log.
        attempt = int(message.get("Attributes", {}).get("ApproximateReceiveCount", 0))
    except (TypeError, ValueError):
        attempt = None
    return {"message_id": message.get("MessageId"), "event_id": attribute.get("StringValue"),
            "attempt": attempt, "resource_id": None}


def parse_body(message: dict) -> dict:
    """Convierte el Body en un objeto JSON sin ejecutar el negocio.

    Args:
        message: Mensaje con Body de texto.
    Returns:
        Diccionario decodificado.
    Raises:
        ValidationError: Body ausente, JSON roto o un valor que no es objeto.
    """
    try:  # JSONDecodeError hereda de ValueError; Body None produce TypeError.
        event = json.loads(message["Body"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValidationError("Body ausente o JSON inválido") from error
    if not isinstance(event, dict):  # Una lista JSON no es un evento.
        raise ValidationError("El Body no es un objeto JSON")
    return event


def schedule_retry(url: str, message: dict, error: Exception, logger, context: dict) -> bool:
    """Registra el fallo y programa el backoff sobre la entrega actual.

    Args:
        url: URL de la cola de trabajo.
        message: Mensaje con su ReceiptHandle actual.
        error: Fallo del parseo, la validación, el negocio o el SDK.
        logger: Logger JSON del worker.
        context: Contexto público del mensaje.
    Returns:
        True si el cambio de visibilidad falló (el llamador pausa el bucle).
    Side Effects:
        change_message_visibility; nunca send_message ni delete_message.
    """
    level = logging.WARNING if isinstance(error, RetryableError) else logging.ERROR
    logger.log(level, f"procesamiento_fallido: {error}" if isinstance(error, ProjectError)
               else "procesamiento_fallido",
               extra={**context, "action": "handle", "status": type(error).__name__})
    try:  # RedrivePolicy decide la DLQ; aquí solo se demora la próxima entrega.
        delay = retry_later(url, message)
    except (ClientError, BotoCoreError) as retry_error:  # Sin ACK: volverá al vencer 45 s.
        logger.error("backoff_no_programado",
                     extra={**context, "action": "retry", "status": type(retry_error).__name__})
        return True
    logger.info(f"reintento_programado en {delay} s",
                extra={**context, "action": "retry", "status": "retry_scheduled"})
    return False


def process(url: str, message: dict, handler, logger) -> bool:
    """Procesa un mensaje y lo confirma solo si el handler terminó.

    Args:
        url: URL de la cola de trabajo.
        message: Mensaje recibido.
        handler: Callable handler(event) idempotente.
        logger: Logger JSON del worker.
    Returns:
        True si hubo un fallo de transporte al reprogramar (pausar el bucle).
    Raises:
        NotImplementedError: Plantilla incompleta; se muestra, no se simula éxito.
        OSError / ValueError: Estado local ilegible; detener sin ACK.
    """
    context = message_context(message)
    logger.info("mensaje_recibido", extra={**context, "action": "receive", "status": "received"})
    try:  # Mensaje venenoso: se conserva y RedrivePolicy lo aislará.
        event = parse_body(message)
    except ValidationError as error:
        return schedule_retry(url, message, error, logger, context)
    if isinstance(event.get("event_id"), str):  # Solo para correlación; handler revalida.
        context["event_id"] = event["event_id"][:64]
    if isinstance(event.get("resource_id"), str):
        context["resource_id"] = event["resource_id"][:64]
    try:  # Negocio: validación, reparación y aviso con etapas durables.
        handler(event)
    except (ProjectError, ClientError, BotoCoreError) as error:
        return schedule_retry(url, message, error, logger, context)
    except (OSError, ValueError) as error:  # Ledger/inventario dañado: no es culpa del mensaje.
        logger.critical("estado_local_ilegible",
                        extra={**context, "action": "handle", "status": type(error).__name__})
        raise
    try:  # ACK únicamente tras el éxito durable del handler.
        acknowledge(url, message["ReceiptHandle"])
    except (ClientError, BotoCoreError) as error:  # El handler idempotente lo absorberá.
        logger.error("ack_fallido",
                     extra={**context, "action": "ack", "status": type(error).__name__})
        return True
    logger.info("mensaje_confirmado", extra={**context, "action": "ack",
                                             "status": "acknowledged"})
    return False


def run(url: str, handler, logger, once: bool = False) -> None:
    """Consume mensajes y confirma únicamente resultados durables.

    Args:
        url: URL de la cola de trabajo, obtenida por get_queue_url.
        handler: Callable handler(event) que revalida y ejecuta el caso de negocio.
        logger: Logger JSON del proceso worker.
        once: Si es True, realiza una sola consulta y termina; útil para pruebas.
    Returns:
        None al terminar la consulta única o al detenerse mediante Ctrl+C.
    Raises:
        NotImplementedError: Si todavía no se completaron las plantillas del alumno.
        ClientError / BotoCoreError: Solo con once=True, si la consulta falla.
        OSError / ValueError: Estado local ilegible; el mensaje queda sin ACK.
    Side Effects:
        Recibe mensajes, ejecuta el handler y cambia o confirma su recepción.
    Notes:
        El parseo ocurre por mensaje para que un JSON corrupto no termine el
        worker ni oculte los otros mensajes. KeyboardInterrupt sube a la CLI,
        que cierra el listener; nada se confirma desde un finally.
    """
    logger.info("worker_iniciado", extra={"action": "startup", "status": "ready"})
    while True:  # Un mensaje por consulta (MaxNumberOfMessages=1).
        try:  # Fallo de transporte: no hay respuesta que procesar ni ACK que fabricar.
            messages = receive(url)
        except (ClientError, BotoCoreError) as error:
            logger.error("consulta_fallida",
                         extra={"action": "receive", "status": type(error).__name__})
            if once:  # Una sola consulta: informar el fallo con código 1.
                raise
            time.sleep(TRANSPORT_PAUSE)
            continue
        if not messages:  # Cola sin mensajes visibles: no es un error.
            logger.debug("sin_mensajes", extra={"action": "receive", "status": "empty"})
        pause = False
        for message in messages:
            pause = process(url, message, handler, logger) or pause
        if once:
            return
        if pause:  # retry_later o ACK fallaron por transporte.
            time.sleep(TRANSPORT_PAUSE)
