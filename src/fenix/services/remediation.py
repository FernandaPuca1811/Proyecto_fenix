"""Acciones convergentes a completar por integrante 3 y coordinadas por 5."""
import logging  # Registro por etapas con el logger del proceso worker.
from datetime import datetime, timezone  # Marcas UTC del ledger.
from botocore.exceptions import BotoCoreError, ClientError
from botocore.exceptions import ConnectionError as BotoConnectionError, HTTPClientError
from fenix.config.settings import client
from fenix.utils.state import load, save
from fenix.services.cloud_support import aws_tags
from fenix.services.events import validate
from fenix.services.notifications import notify
from fenix.utils.errors import ProjectError, RetryableError, PermanentError

INVENTORY = "data/inventory.json"  # Alias autorizados preparados por setup-demo.
LEDGER = "data/events.json"  # Registro durable por event_id; un único proceso lo escribe.
RESOURCE_TYPE = {"instance.stopped": "ec2", "bucket.tags_missing": "s3"}  # Evento -> recurso.
IDENTITY_TAGS = ("ManagedBy", "Project", "GroupId")  # Identifican al dueño; Owner se repara.
TRANSIENT_CODES = {  # Códigos AWS que una nueva recepción puede resolver.
    "Throttling", "ThrottlingException", "RequestLimitExceeded", "SlowDown",
    "TooManyRequestsException", "RequestTimeout", "RequestTimeoutException",
    "ServiceUnavailable", "Unavailable", "InternalError", "InternalFailure",
}


def classify(error: Exception, action: str) -> ProjectError:
    """Traduce un fallo del SDK a transitorio o permanente según su causa.

    Args:
        error: ClientError o BotoCoreError capturado alrededor de una llamada.
        action: Operación pública que falló, por ejemplo start_instances.
    Returns:
        RetryableError para throttling, 5xx, conexión o timeout; PermanentError
        para el resto (recurso inexistente, permisos, parámetros inválidos).
    Notes:
        No todo ClientError es transitorio: reintentar un NotFound solo demora la DLQ.
    """
    if isinstance(error, ClientError):  # Respuesta de la API con código propio.
        code = error.response.get("Error", {}).get("Code", "")
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        if code in TRANSIENT_CODES or status >= 500:  # Limitación temporal o falla del servicio.
            return RetryableError(f"{action}: fallo transitorio {code or status}")
        return PermanentError(f"{action}: rechazo {code or status}")
    if isinstance(error, (BotoConnectionError, HTTPClientError)):  # Red o timeout.
        return RetryableError(f"{action}: sin respuesta del emulador ({type(error).__name__})")
    return PermanentError(f"{action}: error del SDK {type(error).__name__}")  # Credenciales, etc.


def tag_map(tags) -> dict:
    """Normaliza etiquetas a dict, tanto desde el formato SDK como desde el inventario.

    Args:
        tags: Lista de {Key, Value}, dict o None.
    Returns:
        Diccionario clave -> valor.
    Raises:
        PermanentError: Si el formato no es reconocible.
    """
    if tags is None:  # Recurso sin etiquetas.
        return {}
    if isinstance(tags, dict):  # Formato propuesto para expected_tags.
        return dict(tags)
    if isinstance(tags, list):  # Formato de respuesta de EC2/S3.
        return {row["Key"]: row["Value"] for row in tags}
    raise PermanentError("expected_tags con formato desconocido")


def check_identity(current: dict, expected: dict) -> None:
    """Rechaza un recurso cuyas etiquetas de identidad contradicen el inventario.

    Args:
        current: Etiquetas leídas del recurso real.
        expected: Etiquetas obligatorias del inventario.
    Returns:
        None si ninguna etiqueta de identidad presente tiene otro valor.
    Raises:
        PermanentError: Si el recurso declara pertenecer a otro grupo o proyecto.
    Notes:
        Una etiqueta ausente se completa; una con otro valor indica un recurso ajeno
        o un inventario viejo (por ejemplo, LocalStack reiniciado).
    """
    for key in IDENTITY_TAGS:  # Owner queda fuera: es la etiqueta que se repara.
        if key in current and key in expected and current[key] != expected[key]:
            raise PermanentError(f"La etiqueta {key} contradice el inventario; no se repara")


def describe_instance(ec2, instance_id: str) -> dict:
    """Consulta el estado real de una instancia del inventario.

    Args:
        ec2: Cliente EC2 local.
        instance_id: InstanceId leído del inventario, nunca del evento.
    Returns:
        Diccionario de la instancia según describe_instances.
    Raises:
        RetryableError: Fallo transitorio de la API.
        PermanentError: Instancia inexistente o consulta rechazada.
    """
    try:  # Un solo InstanceId por consulta.
        response = ec2.describe_instances(InstanceIds=[instance_id])
    except (ClientError, BotoCoreError) as error:
        raise classify(error, "describe_instances") from error
    reservations = response.get("Reservations", [])  # Vacío si el ID no existe.
    if not reservations or not reservations[0].get("Instances"):
        raise PermanentError("La instancia del inventario no existe")
    return reservations[0]["Instances"][0]


def repair_instance(resource: dict) -> str:
    """Lleva una instancia EC2 detenida a running y lo verifica.

    Args:
        resource: Registro ec2 del inventario con id y expected_tags.
    Returns:
        "repaired" si se arrancó; "already_ok" si ya estaba running.
    Raises:
        RetryableError: pending/stopping, API transitoria o running aún no observado.
        PermanentError: terminated, inexistente o identidad contradictoria.
    """
    ec2 = client("ec2")  # Siempre contra el emulador local.
    instance = describe_instance(ec2, resource["id"])  # Estado real antes de actuar.
    check_identity(tag_map(instance.get("Tags")), tag_map(resource.get("expected_tags")))
    state = instance["State"]["Name"]
    if state == "running":  # Estado deseado: repetir no cambia nada.
        return "already_ok"
    if state in ("pending", "stopping"):  # Transición en curso: otra recepción decide.
        raise RetryableError(f"Instancia en transición ({state})")
    if state != "stopped":  # shutting-down/terminated no se pueden arrancar.
        raise PermanentError(f"Instancia en estado {state}; no se puede arrancar")
    try:  # start sobre stopped converge a running; reboot repetiría el efecto.
        ec2.start_instances(InstanceIds=[resource["id"]])
    except (ClientError, BotoCoreError) as error:
        raise classify(error, "start_instances") from error
    after = describe_instance(ec2, resource["id"])["State"]["Name"]  # Verificación posterior.
    if after != "running":  # Sin espera larga: la próxima recepción lo comprobará.
        raise RetryableError(f"start solicitado; running aún no verificado ({after})")
    return "repaired"


def bucket_tags(s3, bucket: str) -> dict:
    """Lee las etiquetas actuales de un bucket del inventario.

    Args:
        s3: Cliente S3 local.
        bucket: Nombre del bucket leído del inventario.
    Returns:
        Etiquetas actuales; {} únicamente ante NoSuchTagSet.
    Raises:
        RetryableError: Fallo transitorio de la API.
        PermanentError: Bucket inexistente o lectura rechazada.
    """
    try:  # get_bucket_tagging falla si el bucket no tiene ninguna etiqueta.
        return tag_map(s3.get_bucket_tagging(Bucket=bucket)["TagSet"])
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "NoSuchTagSet":
            return {}  # Solo este código equivale a "sin etiquetas".
        raise classify(error, "get_bucket_tagging") from error
    except BotoCoreError as error:
        raise classify(error, "get_bucket_tagging") from error


def repair_bucket(resource: dict) -> str:
    """Restaura las etiquetas obligatorias de un bucket preservando las ajenas.

    Args:
        resource: Registro s3 del inventario con id y expected_tags.
    Returns:
        "repaired" si se reescribieron etiquetas; "already_ok" si ya cumplía.
    Raises:
        RetryableError: API transitoria o etiquetas aún no verificadas.
        PermanentError: Bucket inexistente o identidad contradictoria.
    Notes:
        put_bucket_tagging reemplaza el conjunto completo: por eso se mezcla.
    """
    s3 = client("s3")  # Path-style contra LocalStack.
    expected = tag_map(resource.get("expected_tags"))
    current = bucket_tags(s3, resource["id"])  # Lectura.
    check_identity(current, expected)  # No adoptar un bucket ajeno.
    desired = {**current, **expected}  # Mezcla: las obligatorias pisan, las ajenas quedan.
    if desired == current:  # Sin desvío respecto de la política.
        return "already_ok"
    try:  # Escritura del conjunto completo.
        s3.put_bucket_tagging(Bucket=resource["id"], Tagging={"TagSet": aws_tags(desired)})
    except (ClientError, BotoCoreError) as error:
        raise classify(error, "put_bucket_tagging") from error
    if bucket_tags(s3, resource["id"]) != desired:  # Verificación por relectura.
        raise RetryableError("Etiquetas escritas pero no verificadas")
    return "repaired"


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
    Side Effects:
        Arranca una instancia EC2 mock detenida o restaura tags de un bucket S3.
    Notes:
        No usar reboot_instances: repetir un reinicio no es convergencia.
        Un estado pending requiere otra recepción, sin esperas largas en el worker.
    """
    if not isinstance(resource, dict) or not resource.get("id"):  # Inventario incompleto.
        raise PermanentError("Recurso de inventario sin id")
    kind = RESOURCE_TYPE.get(event_type)  # Segunda comprobación además de validate.
    if kind is None or resource.get("type") != kind:
        raise PermanentError("El tipo de evento no corresponde al recurso")
    if kind == "ec2":
        return repair_instance(resource)
    return repair_bucket(resource)


def utc_now() -> str:
    """Devuelve la hora actual en ISO 8601 UTC para el ledger.

    Returns:
        Texto como 2026-11-10T18:00:00+00:00.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def persist(event_id: str, record: dict) -> None:
    """Guarda un registro sin perder los demás eventos del ledger.

    Args:
        event_id: Clave del evento.
        record: Estado actual de sus etapas.
    Returns:
        None.
    Raises:
        OSError: Si no se puede escribir el archivo.
        ValueError: Si el ledger existente está corrupto.
    Side Effects:
        Reemplaza data/events.json de forma atómica.
    """
    record["updated_at"] = utc_now()  # Última transición persistida.
    ledger = load(LEDGER)  # Relee para preservar el resto del ledger.
    ledger[event_id] = record
    save(LEDGER, ledger)


def summary(event: dict, resource: dict, result: str) -> str:
    """Arma el aviso público de una reparación, sin secretos ni cuerpos HTTP.

    Args:
        event: Evento validado.
        resource: Registro del inventario.
        result: repaired o already_ok.
    Returns:
        Texto breve para Telegram/Slack.
    """
    if event["event_type"] == "instance.stopped":
        detail = "instancia en running verificada"
    else:
        detail = "etiquetas obligatorias verificadas"
    outcome = "reparado" if result == "repaired" else "ya cumplía la política"
    return (f"Fénix: {event['resource_id']} ({resource['type']}) {outcome}; {detail}. "
            f"event_id={event['event_id']}")


def handle(event: dict, logger: logging.Logger | None = None) -> None:
    """Completa reparación y notificación usando un registro durable por evento.

    Args:
        event: Objeto recibido de SQS, que se vuelve a validar aquí.
        logger: Logger JSON del worker; None usa el logger del módulo.
    Returns:
        None solo cuando reparación y aviso quedaron registrados como exitosos.
    Raises:
        ValidationError: Si el evento fue inyectado directamente a SQS y no es válido.
        RetryableError: Si la simulación o una dependencia temporal falla.
        PermanentError: Conflicto de event_id o recurso no autorizado.
        OSError / ValueError: Ledger o inventario ilegibles; el worker se detiene sin ACK.
    Side Effects:
        Actualiza recursos, data/events.json y el canal de comunicación.
    Notes:
        Un solo proceso modifica el ledger. Un timeout al notificar puede repetir
        un aviso: no existe una transacción común entre JSON, AWS y Telegram/Slack.
        Cada etapa se guarda antes de empezar la siguiente.
    """
    log = logger or logging.getLogger(__name__)
    inventory = load(INVENTORY)  # Inventario vigente, no el del momento del envío.
    event = validate(event, inventory)  # SQS puede recibir mensajes que no pasaron por HTTP.
    event_id = event["event_id"]
    context = {"event_id": event_id, "resource_id": event["resource_id"]}
    record = load(LEDGER).get(event_id)  # Etapas ya completadas para este evento.
    if record is None:  # Primera vez: el contenido queda asociado al event_id.
        record = {"event": event, "attempts": 0, "repair_done": False, "repair_result": None,
                  "notified": False, "status": "received", "last_error": None,
                  "created_at": utc_now()}
    elif record.get("event") != event:  # Mismo ID con otro contenido: no se toca nada.
        log.error("conflicto_event_id", extra={**context, "action": "ledger",
                                               "status": "conflict"})
        raise PermanentError("event_id reutilizado con contenido distinto")
    already_done = record["repair_done"] and record["notified"]
    record["attempts"] += 1  # Se guarda antes de simular: sobrevive a reinicios.
    attempt = record["attempts"]
    context["attempt"] = attempt
    persist(event_id, record)
    if already_done:  # Duplicado de un evento completo: sin reparar ni avisar.
        log.info("evento_ya_completado", extra={**context, "action": "handle",
                                                "status": "duplicate"})
        return
    if attempt <= event["simulate_failures"]:  # Falla de laboratorio antes de reparar.
        record.update(status="retry_pending", last_error="simulated_failure")
        persist(event_id, record)
        log.warning("falla_simulada", extra={**context, "action": "simulate",
                                             "status": "retry_pending"})
        raise RetryableError(f"Falla simulada {attempt} de {event['simulate_failures']}")
    resource = inventory[event["resource_id"]]  # validate garantizó el alias.
    if not record["repair_done"]:  # Etapa 1: reparar y verificar.
        try:
            result = repair(resource, event["event_type"])
        except ProjectError as error:  # Se registra el intento y se propaga.
            record.update(status="repair_failed", last_error=type(error).__name__)
            persist(event_id, record)
            raise
        record.update(repair_done=True, repair_result=result, status="repaired",
                      last_error=None)
        persist(event_id, record)  # Durable antes de avisar.
        log.info("reparacion_verificada", extra={**context, "action": "repair",
                                                 "status": result})
    else:  # Reanudación: no se repite el efecto externo.
        log.info("reparacion_previa", extra={**context, "action": "repair",
                                             "status": "skipped"})
    try:  # Etapa 2: avisar; un fallo no borra el mensaje ni repite la reparación.
        notify(summary(event, resource, record["repair_result"]))
    except ProjectError as error:
        record.update(status="notify_pending", last_error=type(error).__name__)
        persist(event_id, record)
        log.warning("aviso_pendiente", extra={**context, "action": "notify",
                                              "status": type(error).__name__})
        raise
    record.update(notified=True, status="completed", last_error=None)
    persist(event_id, record)  # El worker hace ACK solo después de este guardado.
    log.info("aviso_confirmado", extra={**context, "action": "notify", "status": "notified"})
