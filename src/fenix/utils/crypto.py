"""Firma de apoyo y verificación a completar por el integrante 3."""
import hashlib  # Selecciona SHA-256 para autenticar el contenido.
import hmac  # Usa una comparación segura para las firmas.
import re  # Exige formatos exactos sin conversiones permisivas.
import time  # Comprueba la ventana temporal.
from fenix.utils.errors import ValidationError  # Rechazo de autenticación público.

WINDOW_SECONDS = 300  # Tolerancia hacia el pasado o el futuro.
EPOCH = re.compile(r"0|[1-9][0-9]{0,11}")  # Solo dígitos ASCII, sin signo ni ceros a la izquierda.
DIGEST = re.compile(r"[0-9a-f]{64}")  # hexdigest siempre produce minúsculas.


def signature(secret: str, timestamp: int, body: bytes) -> str:
    """Firma exactamente timestamp, punto y cuerpo binario original.

    Args:
        secret: Secreto compartido de al menos 16 caracteres, leído del entorno.
        timestamp: Unix epoch en segundos, igual al enviado en la cabecera.
        body: Bytes exactos enviados por HTTP; no volver a serializar el JSON.
    Returns:
        HMAC-SHA256 hexadecimal de 64 caracteres.
    Raises:
        ValidationError: Si el secreto es demasiado corto.
    """
    if len(secret) < 16:  # Se comprueba también al enviar la prueba.
        raise ValidationError("Configurar WEBHOOK_SECRET de 16 caracteres o más")
    signed = str(timestamp).encode("ascii") + b"." + body  # Prefijo autenticado.
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def verify(secret: str, header: str, body: bytes, now: int | None = None) -> None:
    """Rechaza cabeceras ambiguas y firmas o fechas inválidas.

    Args:
        secret: Secreto compartido del entorno, nunca del payload.
        header: Cabecera exacta t=<epoch>,v1=<hex> sin claves duplicadas.
        body: Bytes del cuerpo antes de llamar a json.loads.
        now: Reloj inyectable para pruebas; None usa time.time.
    Returns:
        None si las comprobaciones son satisfactorias.
    Raises:
        ValidationError: Formato inválido, firma incorrecta o diferencia mayor a 300 s.
    Notes:
        La ventana de tiempo mitiga replay antiguo. No impide repetir un evento
        válido dentro de la ventana; event_id e idempotencia cubren ese caso.
        Se rechazan espacios, mayúsculas en v1 y ceros a la izquierda en t: dos
        textos distintos no pueden representar la misma firma.
    """
    if not isinstance(header, str) or not header:  # Cabecera ausente o vacía.
        raise ValidationError("Firma ausente")
    fields = {}  # Cada clave aparece una sola vez.
    for part in header.split(","):  # Formato exacto t=<epoch>,v1=<hex>.
        key, separator, value = part.partition("=")  # Solo el primer = separa.
        if not separator or key not in ("t", "v1") or key in fields:
            raise ValidationError("Cabecera de firma ambigua")  # Clave extra o repetida.
        fields[key] = value
    if len(fields) != 2:  # Falta t o v1.
        raise ValidationError("Cabecera de firma incompleta")
    if not EPOCH.fullmatch(fields["t"]):  # int() aceptaría " 1", "+1" o "1_0".
        raise ValidationError("Timestamp de firma inválido")
    if not DIGEST.fullmatch(fields["v1"]):  # Longitud y alfabeto exactos.
        raise ValidationError("Firma con formato inválido")
    timestamp = int(fields["t"])  # Ya se comprobó que es un entero canónico.
    current = int(time.time()) if now is None else now  # Reloj inyectable en pruebas.
    if abs(current - timestamp) > WINDOW_SECONDS:  # Pasado o futuro lejano.
        raise ValidationError("Firma fuera de la ventana de 300 segundos")
    expected = signature(secret, timestamp, body)  # Firma de los bytes recibidos.
    if not hmac.compare_digest(expected, fields["v1"]):  # Tiempo constante, nunca ==.
        raise ValidationError("Firma HMAC inválida")
