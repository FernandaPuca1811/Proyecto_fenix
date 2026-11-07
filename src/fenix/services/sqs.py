"""Mensajería a completar por el integrante 2; colas estándar, no FIFO."""
import json  # Convierte eventos y políticas a texto JSON.
from fenix.config.settings import client  # Garantiza el endpoint local.


def ensure_queues(prefix: str) -> tuple[str, str]:
    """Crea primero la DLQ y después configura la cola de trabajo.

    Args:
        prefix: Prefijo aprobado como fenix-grupo01 o efimero-grupo01.
    Returns:
        Tupla (work_url, dlq_url), tomada de las respuestas de Boto3.
    Raises:
        ClientError: Si SQS rechaza una operación; la CLI lo registra.
        NotImplementedError: Mientras falte TODO I2-A.
    Side Effects:
        Crea dos colas y actualiza RedrivePolicy en la cola principal.
    Notes:
        Repetir con la misma configuración conserva ambas colas.
    """
    sqs = client("sqs")  # Construye el cliente una vez en esta operación.
    dead = sqs.create_queue(QueueName=f"{prefix}-dlq", Attributes={
        "MessageRetentionPeriod": "1209600"})["QueueUrl"]  # Retiene 14 días.
    arn = sqs.get_queue_attributes(QueueUrl=dead,
        AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]  # No inventa ARN.
    # TODO I2-A: crear {prefix}-events con VisibilityTimeout="45" y retención "345600".
    # Crear RedrivePolicy con json.dumps, deadLetterTargetArn=arn y maxReceiveCount="3".
    # Usar set_queue_attributes para asegurar la política aunque la cola exista.
    # Retornar las URLs devueltas por SQS; no construirlas con strings.
    raise NotImplementedError("I2-A: completar cola principal y RedrivePolicy")


def queue_url(prefix: str) -> str:
    """Busca la URL actual de la cola de trabajo ya creada.

    Args:
        prefix: Prefijo configurado por el grupo.
    Returns:
        URL retornada por SQS.
    Raises:
        ClientError: Si la cola todavía no existe.
    """
    return client("sqs").get_queue_url(QueueName=f"{prefix}-events")["QueueUrl"]


def publish(url: str, event: dict, delay: int = 0) -> str:
    """Publica el evento sin confirmarlo ni procesarlo.

    Args:
        url: QueueUrl obtenida del SDK.
        event: Diccionario validado, serializable a JSON, con event_id.
        delay: Retardo inicial entre 0 y 900 segundos, inclusive.
    Returns:
        MessageId asignado por SQS.
    Raises:
        ValueError: Si el retardo está fuera de rango.
        TypeError: Si event no se puede serializar.
        ClientError: Si falla el envío a SQS.
    Side Effects:
        Añade un mensaje a una cola estándar; puede haber entregas repetidas.
    """
    if not 0 <= delay <= 900:  # No confundir con VisibilityTimeout.
        raise ValueError("DelaySeconds debe estar entre 0 y 900")  # Falla antes del SDK.
    response = client("sqs").send_message(  # Una aceptación confirma almacenamiento.
        QueueUrl=url, MessageBody=json.dumps(event), DelaySeconds=delay,
        MessageAttributes={"EventId": {"DataType": "String",
                                     "StringValue": event["event_id"]}},
    )  # Se conserva el event_id original durante todos los reintentos.
    return response["MessageId"]  # Este identificador no sustituye al event_id.


def receive(url: str) -> list[dict]:
    """Obtiene como máximo un mensaje para simplificar la defensa inicial.

    Args:
        url: URL de la cola principal.
    Returns:
        Lista vacía o un mensaje crudo con Body, ReceiptHandle y Attributes.
    Raises:
        ClientError: Si SQS no responde; el bucle externo decide cómo continuar.
    Side Effects:
        Incrementa la recepción y oculta temporalmente el mensaje obtenido.
    """
    response = client("sqs").receive_message(  # La espera evita polling intensivo.
        QueueUrl=url, MaxNumberOfMessages=1, WaitTimeSeconds=10,
        VisibilityTimeout=45,
        MessageSystemAttributeNames=["ApproximateReceiveCount"],
        MessageAttributeNames=["All"],
    )  # Un receive exitoso todavía no es una confirmación de trabajo.
    return response.get("Messages", [])  # Una cola vacía no es una excepción.


def acknowledge(url: str, receipt: str) -> None:
    """Elimina exclusivamente la entrega actual después del éxito durable.

    Args:
        url: URL de la cola fuente.
        receipt: ReceiptHandle de la recepción más reciente, no MessageId.
    Returns:
        None.
    Raises:
        ClientError: Si SQS no pudo confirmar la eliminación.
    Side Effects:
        Solicita borrar el mensaje; el negocio debe tolerar duplicados.
    """
    client("sqs").delete_message(QueueUrl=url, ReceiptHandle=receipt)  # ACK explícito.


def retry_later(url: str, message: dict) -> int:
    """Programa el próximo intento sin copiar ni borrar el mensaje original.

    Args:
        url: URL de la cola fuente.
        message: Mensaje con ReceiptHandle y ApproximateReceiveCount.
    Returns:
        Segundos asignados: min(30, 2 ** número_de_recepción).
    Raises:
        ClientError: Si no se pudo cambiar la visibilidad.
        NotImplementedError: Mientras falte TODO I2-B.
    Side Effects:
        Cambia la visibilidad; RedrivePolicy sigue controlando la DLQ.
    """
    # TODO I2-B: leer int(message["Attributes"]["ApproximateReceiveCount"]).
    # Calcular min(30, 2 ** count) y llamar change_message_visibility.
    # Pasar el ReceiptHandle actual; retornar segundos para registrarlos.
    # Nunca send_message aquí: una copia reiniciaría la cuenta de recepciones.
    raise NotImplementedError("I2-B: programar backoff con visibilidad")
