"""Adaptador HTTP guiado. Elegir Telegram o Slack, no ambos obligatoriamente."""

import logging
import os

import requests

from fenix.utils.errors import PermanentError, RetryableError

logger = logging.getLogger("fenix.notifications")


def notify(text: str) -> None:
    """Envía un mensaje breve y valida la respuesta del proveedor seleccionado.

    Args:
        text: Resumen público de hasta 1000 caracteres, sin secretos ni raw body.

    Returns:
        None únicamente después de confirmar éxito del proveedor.

    Raises:
        RetryableError: Timeout, conexión, 429 o respuesta 5xx.
        PermanentError: Configuración inválida, 4xx definitivo o rechazo funcional.
    """
    mode = os.getenv("NOTIFY_PROVIDER", "console")

    # Selecciona un adaptador explícito.
    if mode == "console":
        # Ensayo local sin credenciales de terceros.
        print("\n=== [NOTIFICACIÓN CONSOLA] ===")
        print(text[:1000])
        # No imprimir eventos completos o URLs privadas.
        print("===============================\n")
        return

    if mode == "telegram":
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not bot_token or not chat_id:
            raise PermanentError(
                "Configuración de Telegram incompleta: faltan TELEGRAM_BOT_TOKEN o"
                " TELEGRAM_CHAT_ID"
            )

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text[:1000],
        }

        try:
            # Timeouts estrictos: 2s conexión, 5s lectura.
            response = requests.post(url, json=payload, timeout=(2.0, 5.0))

            # Errores 5xx o 429 -> fallo transitorio (reintentable por SQS).
            if response.status_code == 429 or response.status_code >= 500:
                raise RetryableError(
                    f"Telegram devolvió código HTTP {response.status_code}"
                )

            # Errores 4xx -> configuración/solicitud inválida.
            if response.status_code >= 400:
                raise PermanentError(
                    f"Error de cliente en Telegram: HTTP {response.status_code}"
                )

            data = response.json()
            if not data.get("ok"):
                raise PermanentError(f"Telegram rechazó el mensaje: {data}")

            logger.info("Notificación enviada exitosamente a Telegram.")
            return
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            # No exponer en los logs la URL que contiene el token.
            raise RetryableError("Error de red/timeout al conectar con Telegram API")
        except requests.exceptions.RequestException as err:
            raise RetryableError(f"Fallo en la petición a Telegram: {type(err)}")

    if mode == "slack":
        webhook_url = os.getenv("SLACK_WEBHOOK_URL")
        if not webhook_url:
            raise PermanentError(
                "Configuración de Slack incompleta: falta SLACK_WEBHOOK_URL"
            )

        try:
            response = requests.post(
                webhook_url,
                json={"text": text[:1000]},
                timeout=(2.0, 5.0),
            )

            if response.status_code == 429 or response.status_code >= 500:
                raise RetryableError(
                    f"Slack devolvió código HTTP {response.status_code}"
                )

            if response.status_code >= 400:
                raise PermanentError(
                    f"Error de cliente en Slack: HTTP {response.status_code}"
                )

            if response.text.strip() != "ok":
                raise PermanentError(
                    f"Slack devolvió respuesta inesperada: {response.text}"
                )

            logger.info("Notificación enviada exitosamente a Slack.")
            return
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            raise RetryableError("Error de red/timeout al conectar con Slack")
        except requests.exceptions.RequestException as err:
            raise RetryableError(f"Fallo en la petición a Slack: {type(err)}")

    raise PermanentError(f"Proveedor de notificación no soportado: {mode}")
