"""Adaptador HTTP guiado. Elegir Telegram O Slack, no ambos obligatoriamente."""
import os  # Recupera credenciales fuera del repositorio.
import requests  # Aplica timeout explícito a cada llamada HTTP.
from fenix.utils.errors import PermanentError, RetryableError


def notify(text: str) -> None:
    """Envía un mensaje breve y valida la respuesta del proveedor seleccionado.

    Args:
        text: Resumen público de hasta 1000 caracteres, sin secretos ni raw body.
    Returns:
        None únicamente después de confirmar éxito del proveedor.
    Raises:
        RetryableError: Timeout, conexión, 429 o respuesta 5xx.
        PermanentError: Configuración inválida, 4xx definitivo o rechazo funcional.
        NotImplementedError: Mientras falte TODO I4-A.
    Side Effects:
        Publica en el chat/canal del grupo. Un timeout puede causar duplicados.
    Notes:
        El modo console sirve para desarrollo; no reemplaza la evidencia final real.
        No registrar la excepción requests original: puede contener el token en URL.
    """
    mode = os.getenv("NOTIFY_PROVIDER", "console")  # Selecciona un adaptador explícito.
    if mode == "console":  # Ensayo local sin credenciales de terceros.
        print(text[:1000])  # No imprimir eventos completos o URLs privadas.
        return  # El modo debe figurar en el informe de pruebas.
    # TODO I4-A Telegram: POST https://api.telegram.org/bot{token}/sendMessage.
    # Leer TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID desde entorno; payload chat_id/text.
    # Verificar status HTTP y JSON con ok is True; no basta con un HTTP 200.
    # TODO I4-A Slack: POST a SLACK_WEBHOOK_URL con json={"text": text[:1000]}.
    # Verificar status 200 y body.strip() == "ok"; Slack no exige JSON de respuesta.
    # Usar timeout=(2, 5); capturar Timeout/ConnectionError sin copiar su mensaje.
    # 429 y 5xx son transitorios; leer Retry-After y documentar demora acotada.
    # Otros 4xx son permanentes; no reintentar infinitamente credenciales inválidas.
    raise NotImplementedError("I4-A: implementar proveedor externo elegido")
