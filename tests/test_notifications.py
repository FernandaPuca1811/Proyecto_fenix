"""Pruebas locales del adaptador de notificaciones."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from fenix.services.notifications import _wait_retry_after, notify
from fenix.utils.errors import PermanentError, RetryableError


def _response(
    status_code: int, payload: object, headers: dict | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        status_code=status_code,
        json=Mock(return_value=payload),
        headers={} if headers is None else headers,
    )


@pytest.mark.parametrize(
    ("header", "max_wait", "expected"),
    [("4", 10, 4), ("45", 30, 30), ("-3", 30, 0), ("invalid", 30, 0)],
)
def test_wait_retry_after_returns_bounded_delay(monkeypatch, header, max_wait, expected):
    sleeps = []
    monkeypatch.setattr("fenix.services.notifications.time.sleep", sleeps.append)

    waited = _wait_retry_after(_response(429, {}, {"Retry-After": header}), max_wait)

    assert waited == expected
    assert sleeps == ([expected] if expected else [])


def test_telegram_sends_limited_message(monkeypatch):
    """Publica solo el texto acotado y confirma ok=true."""
    monkeypatch.setenv("NOTIFY_PROVIDER", "telegram")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    post = Mock(return_value=_response(200, {"ok": True}))
    monkeypatch.setattr("fenix.services.notifications.requests.post", post)

    notify("x" * 1001)

    args, kwargs = post.call_args
    assert args == ("https://api.telegram.org/bottest-token/sendMessage",)
    assert kwargs["json"] == {"chat_id": "test-chat", "text": "x" * 1000}
    assert kwargs["timeout"] == (2, 5)


@pytest.mark.parametrize("error", [requests.Timeout("private"), requests.ConnectionError("private")])
def test_telegram_transport_errors_are_retryable(monkeypatch, error):
    """No propaga texto de excepción que pudiera incluir el token de la URL."""
    monkeypatch.setenv("NOTIFY_PROVIDER", "telegram")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    post = Mock(side_effect=error)
    monkeypatch.setattr("fenix.services.notifications.requests.post", post)

    with pytest.raises(RetryableError) as raised:
        notify("falló reparación")

    assert "private" not in str(raised.value)


def test_unknown_request_exception_is_retryable(monkeypatch):
    """Convierte excepciones genéricas de requests en un error de dominio."""
    monkeypatch.setenv("NOTIFY_PROVIDER", "telegram")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setattr(
        "fenix.services.notifications.requests.post",
        Mock(side_effect=requests.RequestException("private")),
    )

    with pytest.raises(RetryableError) as raised:
        notify("falló reparación")

    assert "private" not in str(raised.value)


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(429, RetryableError), (503, RetryableError), (401, PermanentError)],
)
def test_request_exception_response_is_classified(monkeypatch, status, error_type):
    """Clasifica HTTPError según su respuesta sin filtrar el texto original."""
    monkeypatch.setenv("NOTIFY_PROVIDER", "telegram")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    response = _response(status, {})
    error = requests.HTTPError("private", response=response)
    monkeypatch.setattr(
        "fenix.services.notifications.requests.post",
        Mock(side_effect=error),
    )

    with pytest.raises(error_type) as raised:
        notify("falló reparación")

    assert "private" not in str(raised.value)


def test_http_error_response_honors_retry_after(monkeypatch):
    """También usa Retry-After si requests lanza con la respuesta adjunta."""
    monkeypatch.setenv("NOTIFY_PROVIDER", "telegram")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    response = _response(429, {}, {"Retry-After": "7"})
    error = requests.HTTPError("private", response=response)
    sleeps = []
    monkeypatch.setattr("fenix.services.notifications.time.sleep", sleeps.append)
    monkeypatch.setattr(
        "fenix.services.notifications.requests.post",
        Mock(side_effect=error),
    )

    with pytest.raises(RetryableError):
        notify("falló reparación")

    assert sleeps == [7]


@pytest.mark.parametrize("status", [429, 500, 503])
def test_telegram_rate_limit_and_server_errors_are_retryable(monkeypatch, status):
    """Los 429/5xx son reintentables y honran Retry-After con tope de 30 s."""
    monkeypatch.setenv("NOTIFY_PROVIDER", "telegram")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    sleeps = []
    monkeypatch.setattr("fenix.services.notifications.time.sleep", sleeps.append)
    monkeypatch.setattr(
        "fenix.services.notifications.requests.post",
        Mock(return_value=_response(status, {}, {"Retry-After": "45"})),
    )

    with pytest.raises(RetryableError):
        notify("falló reparación")

    assert sleeps == [30]


def test_telegram_retry_after_accepts_short_delay(monkeypatch):
    """Respeta un Retry-After corto sin excederlo."""
    monkeypatch.setenv("NOTIFY_PROVIDER", "telegram")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    sleeps = []
    monkeypatch.setattr("fenix.services.notifications.time.sleep", sleeps.append)
    monkeypatch.setattr(
        "fenix.services.notifications.requests.post",
        Mock(return_value=_response(429, {}, {"Retry-After": "4"})),
    )

    with pytest.raises(RetryableError):
        notify("falló reparación")

    assert sleeps == [4]


def test_invalid_retry_after_does_not_hide_retryable_error(monkeypatch):
    """Un encabezado inválido no oculta ni transforma el error transitorio."""
    monkeypatch.setenv("NOTIFY_PROVIDER", "telegram")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    sleeps = []
    monkeypatch.setattr("fenix.services.notifications.time.sleep", sleeps.append)
    monkeypatch.setattr(
        "fenix.services.notifications.requests.post",
        Mock(return_value=_response(429, {}, {"Retry-After": "not-a-delay"})),
    )

    with pytest.raises(RetryableError):
        notify("falló reparación")

    assert sleeps == []


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_telegram_client_errors_are_permanent(monkeypatch, status):
    """No reintenta errores definitivos de configuración o destinatario."""
    monkeypatch.setenv("NOTIFY_PROVIDER", "telegram")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setattr(
        "fenix.services.notifications.requests.post",
        Mock(return_value=_response(status, {})),
    )

    with pytest.raises(PermanentError):
        notify("falló reparación")


def test_telegram_requires_success_in_response_body(monkeypatch):
    """Un HTTP 200 no cuenta como éxito si Telegram devuelve ok=false."""
    monkeypatch.setenv("NOTIFY_PROVIDER", "telegram")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setattr(
        "fenix.services.notifications.requests.post",
        Mock(return_value=_response(200, {"ok": False, "description": "private"})),
    )

    with pytest.raises(PermanentError) as raised:
        notify("falló reparación")

    assert "private" not in str(raised.value)


def test_missing_telegram_configuration_is_permanent(monkeypatch):
    """Falla explícitamente antes de llamar a Telegram si falta configuración."""
    monkeypatch.setenv("NOTIFY_PROVIDER", "telegram")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    post = Mock()
    monkeypatch.setattr("fenix.services.notifications.requests.post", post)

    with pytest.raises(PermanentError):
        notify("falló reparación")

    post.assert_not_called()


def test_unknown_provider_is_permanent(monkeypatch):
    """No convierte una opción mal escrita en un falso éxito por consola."""
    monkeypatch.setenv("NOTIFY_PROVIDER", "slack")

    with pytest.raises(PermanentError):
        notify("falló reparación")


def test_console_provider_remains_available(monkeypatch, capsys):
    """Conserva el modo local sin credenciales externas."""
    monkeypatch.setenv("NOTIFY_PROVIDER", "console")

    notify("aviso")

    assert capsys.readouterr().out == "aviso\n"
