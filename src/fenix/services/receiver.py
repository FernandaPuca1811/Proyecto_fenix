"""Receptor FastAPI: autenticación, validación y aceptación durable por SQS."""
import os  # Recupera el secreto sin incluirlo en el repositorio.
import json  # Decodifica después de verificar los bytes.
import logging  # Respaldo cuando la app corre sin lifespan (pruebas unitarias).
import time  # Mide la latencia observada hasta la confirmación de SQS.
from contextlib import asynccontextmanager  # Arranque y cierre del logging por proceso.
from dotenv import load_dotenv  # Permite iniciar uvicorn con o sin --env-file.
from fastapi import FastAPI, Request, HTTPException  # Interfaz HTTP del curso.
from starlette.concurrency import run_in_threadpool  # Evita bloquear el event loop.
from botocore.exceptions import BotoCoreError, ClientError
from fenix.utils.crypto import verify
from fenix.utils.errors import ValidationError
from fenix.utils.logging_json import setup
from fenix.utils.state import load
from fenix.services.events import validate
from fenix.services.sqs import queue_url, publish

INVENTORY = "data/inventory.json"  # Solo lectura: el receptor nunca modifica data/.
MAX_BODY = 16384  # 16 KiB, límite del contrato HTTP.
SIGNATURE_HEADER = "X-Webhook-Signature"  # t=<epoch>,v1=<64 hex>.


class ReceiverConfigError(Exception):
    """Configuración del receptor ausente o insegura; no es culpa del cliente.

    Args:
        message: Nombre de la variable afectada, nunca su valor.
    """


def webhook_secret() -> str:
    """Lee el secreto compartido y exige la longitud mínima de la firma.

    Returns:
        Secreto de al menos 16 caracteres.
    Raises:
        ReceiverConfigError: Si WEBHOOK_SECRET falta o es demasiado corto.
    """
    secret = os.environ.get("WEBHOOK_SECRET", "")  # Nunca proviene del payload.
    if len(secret) < 16:  # Un secreto corto haría que toda firma diera 401.
        raise ReceiverConfigError("WEBHOOK_SECRET ausente o menor a 16 caracteres")
    return secret


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicia el log JSON del proceso receptor y valida la configuración.

    Args:
        app: Aplicación FastAPI; guarda el logger en app.state.
    Returns:
        Generador asíncrono que mantiene el listener activo mientras atiende.
    Raises:
        ReceiverConfigError: Sin secreto válido el proceso no arranca (cierre controlado).
    Side Effects:
        Crea logs/fenix-receiver-<pid>.jsonl y detiene el listener al apagar.
    """
    load_dotenv()  # No sobrescribe variables ya exportadas por uvicorn --env-file.
    logger, listener = setup("fenix-receiver")  # Archivo propio de este proceso.
    app.state.logger = logger  # El endpoint reutiliza el mismo pipeline.
    try:  # El listener se cierra aunque el arranque falle.
        try:  # Configuración obligatoria antes de aceptar tráfico.
            webhook_secret()
        except ReceiverConfigError:  # Error de arranque no recuperable.
            logger.critical("receptor_sin_secreto", extra={"action": "startup",
                                                           "status": "config_error"})
            raise  # uvicorn termina sin atender peticiones.
        logger.info("receptor_iniciado", extra={"action": "startup", "status": "ready"})
        yield  # Atiende peticiones hasta el apagado.
        logger.info("receptor_detenido", extra={"action": "shutdown", "status": "stopped"})
    finally:  # Drena registros pendientes; sin return/break/continue.
        listener.stop()


app = FastAPI(title="Fénix receptor de alertas", lifespan=lifespan)  # Un worker Uvicorn.


def enqueue(event: dict) -> str:
    """Resuelve la cola de trabajo y publica el evento validado.

    Args:
        event: Copia validada del evento, serializable a JSON.
    Returns:
        MessageId asignado por SQS.
    Raises:
        ClientError: La cola no existe o SQS rechazó el envío.
        BotoCoreError: Conexión o timeout contra el emulador.
        ValidationError: Endpoint o credenciales locales mal configurados.
        KeyError: Falta QUEUE_PREFIX en el entorno.
    Side Effects:
        Añade un mensaje a la cola estándar.
    """
    url = queue_url(os.environ["QUEUE_PREFIX"])  # URL devuelta por el SDK.
    return publish(url, event)  # Solo retorna tras la confirmación de send_message.


@app.post("/webhook", status_code=202)
async def webhook(request: Request) -> dict:
    """Recibe un evento firmado y contesta 202 solo después de encolarlo.

    Args:
        request: Petición con X-Webhook-Signature y cuerpo de hasta 16 KiB.
    Returns:
        JSON con status=accepted, event_id y message_id de SQS.
    Raises:
        HTTPException: 401 firma; 400 JSON; 413 tamaño; 422 contrato; 503 SQS
            o inventario; 500 receptor sin secreto.
    Side Effects:
        Publica en SQS; no repara recursos ni llama a Telegram/Slack aquí.
    Notes:
        Leer bytes completos basta para el laboratorio; un proxy limitaría antes
        el tamaño de la transferencia en un despliegue real. El orden de los
        controles evita gastar trabajo en peticiones no autenticadas.
    """
    started = time.perf_counter()  # Latencia observada, no un objetivo rígido.
    logger = getattr(request.app.state, "logger", logging.getLogger("fenix.receiver"))
    raw = await request.body()  # Se conservan los bytes originales.
    if len(raw) > MAX_BODY:  # Mantiene acotados los ejemplos didácticos.
        logger.warning("cuerpo_rechazado", extra={"action": "receive", "status": "413"})
        raise HTTPException(413, "Cuerpo demasiado grande")  # No encola.
    try:  # Configuración del servidor, separada del error del cliente.
        secret = webhook_secret()
    except ReceiverConfigError as error:
        logger.error("receptor_sin_secreto", extra={"action": "verify", "status": "500"})
        raise HTTPException(500, "Receptor sin configurar") from error
    try:  # Firma sobre los bytes exactos, antes de interpretar el JSON.
        verify(secret, request.headers.get(SIGNATURE_HEADER, ""), raw)
    except ValidationError as error:  # Mismo mensaje público para cualquier causa.
        logger.warning(f"firma_rechazada: {error}", extra={"action": "verify", "status": "401"})
        raise HTTPException(401, "Firma ausente o inválida") from error
    try:  # UTF-8 estricto: json.loads(bytes) aceptaría también UTF-16/32.
        event = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        logger.warning("json_invalido", extra={"action": "parse", "status": "400"})
        raise HTTPException(400, "JSON o UTF-8 inválido") from error
    try:  # Lectura del inventario fuera del event loop.
        inventory = await run_in_threadpool(load, INVENTORY)
    except (OSError, ValueError) as error:  # Archivo ilegible o corrupto.
        logger.error("inventario_no_disponible", extra={"action": "validate", "status": "503"})
        raise HTTPException(503, "Inventario no disponible") from error
    try:  # Contrato cerrado y alias autorizados (integrante 1).
        event = validate(event, inventory)
    except ValidationError as error:  # El mensaje describe la regla, no la entrada.
        logger.warning(f"contrato_rechazado: {error}",
                       extra={"action": "validate", "status": "422"})
        raise HTTPException(422, f"Evento fuera de contrato: {error}") from error
    context = {"event_id": event["event_id"], "resource_id": event["resource_id"]}
    try:  # Boto3 es bloqueante: corre en el threadpool.
        message_id = await run_in_threadpool(enqueue, event)
    except (ClientError, BotoCoreError, ValidationError, KeyError) as error:
        logger.error(f"cola_no_disponible: {type(error).__name__}",
                     extra={**context, "action": "publish", "status": "503"})
        raise HTTPException(503, "Cola no disponible; reintentar más tarde") from error
    elapsed = (time.perf_counter() - started) * 1000  # Incluye la espera de SQS.
    logger.info(f"evento_aceptado en {elapsed:.1f} ms",
                extra={**context, "message_id": message_id, "action": "publish",
                       "status": "accepted"})
    return {"status": "accepted", "event_id": event["event_id"], "message_id": message_id}
