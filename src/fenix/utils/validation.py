"""Contratos de validación a completar por el integrante 1."""
import re  # Comprueba el formato de los identificadores.
from datetime import datetime, timezone  # Trabaja con fechas conscientes de UTC.
from fenix.utils.errors import ValidationError  # Error público del dominio.


def identifier(value: str) -> str:
    """Valida un identificador corto y devuelve su texto sin modificar.

    Args:
        value: Texto entre 3 y 32 caracteres; letras minúsculas, dígitos o guion.
    Returns:
        El identificador original válido.
    Raises:
        ValidationError: Si el tipo o el patrón no cumplen el contrato.
        NotImplementedError: Mientras el alumno no complete TODO I1-A.
    Examples:
        identifier("grupo-01") devuelve "grupo-01"; "../ajeno" debe fallar.
    """
    # TODO I1-A: usar isinstance y re.fullmatch con r"[a-z0-9-]{3,32}".
    # No convertir automáticamente a minúsculas: podría cambiar la identidad.
    raise NotImplementedError("I1-A: validar identifier")  # Quitar al completar.


def ttl_seconds(value: str) -> int:
    """Valida el TTL didáctico empleado en la opción de entornos efímeros.

    Args:
        value: Texto con un entero entre 60 y 600 segundos inclusive.
    Returns:
        El número entero de segundos.
    Raises:
        ValidationError: Si no es entero o está fuera de los límites.
        NotImplementedError: Mientras no se complete TODO I1-B.
    Notes:
        La CLI traduce ValidationError a parser.error para salir con código 2.
    """
    # TODO I1-B: convertir con int y capturar ValueError con encadenamiento.
    # Comprobar 60 <= ttl <= 600; no aceptar decimales ni valores negativos.
    raise NotImplementedError("I1-B: validar TTL")  # Sustituir por la validación.


def utc_text(value: str) -> datetime:
    """Interpreta una fecha UTC explícita sin aceptar fechas ingenuas.

    Args:
        value: ISO 8601 terminado en Z o con offset +00:00.
    Returns:
        Fecha con zona UTC; por ejemplo 2026-10-22T18:00:00+00:00.
    Raises:
        ValidationError: Si falta zona, hay offset no nulo o formato inválido.
        NotImplementedError: Mientras no se complete TODO I1-C.
    """
    # TODO I1-C: usar fromisoformat, reemplazando Z por +00:00.
    # Rechazar tzinfo ausente y utcoffset distinto de timedelta(0).
    raise NotImplementedError("I1-C: interpretar UTC")  # No usar hora local.
