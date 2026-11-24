
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import os
import time

import requests
from fenix.utils.errors import PermanentError, RetryableError


def _wait_retry_after(
    response: requests.Response, max_wait: float = 30.0
) -> float:
    """Espera el Retry-After de la respuesta, limitado por ``max_wait``.

    Args:
        response: Respuesta que puede incluir el encabezado Retry-After.
        max_wait: Máximo de segundos a esperar; debe ser finito y no negativo.
    Returns:
        Segundos efectivamente esperados, o ``0.0`` si falta o no se puede
        interpretar Retry-After.
    Raises:
        ValueError: Si ``max_wait`` no es finito o es negativo.
    Side Effects:
        Llama a ``time.sleep`` solo cuando la espera resultante es positiva.
    """
    if not math.isfinite(max_wait) or max_wait < 0:
        raise ValueError("max_wait debe ser finito y no negativo")
    headers = getattr(response, "headers", None)
    value = headers.get("Retry-After") if headers else None
    if not value:
        return 0.0
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
    if not math.isfinite(delay):
        return 0.0
    wait = min(max_wait, max(0.0, delay))
    if wait > 0:
        time.sleep(wait)
    return wait


def notify(text: str) -> None:
    """Envía un mensaje breve y confirma el éxito del proveedor seleccionado.

    Args:
        text: Resumen público; se limita a 1000 caracteres.
    Returns:
        None únicamente después de confirmar éxito del proveedor.
    Raises:
        RetryableError: Timeout, conexión, 429 o respuesta 5xx.
        PermanentError: Configuración inválida, 4xx definitivo o rechazo funcional.
    Side Effects:
        Publica en el chat configurado. Un timeout puede causar duplicados.
    Notes:
        El modo console sirve para desarrollo; no reemplaza la evidencia final real.
        Retry-After se respeta hasta 30 segundos; SQS administra los reintentos siguientes.
    """
    if not isinstance(text, str) or not text.strip():
        raise PermanentError("Mensaje de notificación inválido")

    message = text[:1000]
    mode = os.getenv("NOTIFY_PROVIDER", "console")
    if mode == "console":
        print(message)
        return
    if mode != "telegram":
        raise PermanentError("Proveedor de notificaciones no configurado")

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token.strip() or not chat_id.strip():
        raise PermanentError("Falta configuración de Telegram")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        response = requests.post(
            url,
            json={"chat_id": chat_id, "text": message},
            timeout=(2, 5),
        )
    except requests.exceptions.RequestException as error:
        response = error.response
        if response is not None:
            status = response.status_code
            if status == 429 or status >= 500:
                _wait_retry_after(response)
                raise RetryableError("Telegram no disponible temporalmente") from None
            if 400 <= status < 500:
                raise PermanentError("Telegram rechazó la notificación") from None
            raise PermanentError("Respuesta HTTP inesperada de Telegram") from None
        if isinstance(error, (
            requests.exceptions.InvalidURL,
            requests.exceptions.InvalidSchema,
            requests.exceptions.MissingSchema,
            requests.exceptions.URLRequired,
            requests.exceptions.InvalidHeader,
            requests.exceptions.TooManyRedirects,
        )):
            raise PermanentError("Configuración de Telegram inválida") from None
        raise RetryableError("Fallo temporal de conexión con Telegram") from None

    status = response.status_code
    if status == 429 or status >= 500:
        _wait_retry_after(response)
        raise RetryableError("Telegram no disponible temporalmente")
    if 400 <= status < 500:
        raise PermanentError("Telegram rechazó la notificación")
    if not 200 <= status < 300:
        raise PermanentError("Respuesta HTTP inesperada de Telegram")

    try:
        payload = response.json()
    except (ValueError, requests.exceptions.RequestException):
        raise RetryableError("Telegram devolvió una respuesta inválida") from None

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        error_code = payload.get("error_code") if isinstance(payload, dict) else None
        if error_code == 429 or (isinstance(error_code, int) and error_code >= 500):
            raise RetryableError("Telegram no disponible temporalmente")
        raise PermanentError("Telegram no confirmó la notificación")

