
import os

import requests
from fenix.utils.errors import PermanentError, RetryableError


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
        No se duerme ante 429: SQS administra los reintentos del worker.
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
