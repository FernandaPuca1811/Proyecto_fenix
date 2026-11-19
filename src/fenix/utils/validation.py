"""Contratos de validación de entradas (integrante 1).

Tres funciones puras, sin red ni disco: reciben un valor, devuelven el valor
interpretado o lanzan ``ValidationError``. Nunca modifican su entrada y nunca
copian el valor rechazado dentro del mensaje, porque podría contener secretos.
"""
import re  # Comprueba el formato de los identificadores y de las fechas.
from datetime import datetime, timedelta, timezone  # Trabaja con fechas conscientes de UTC.
from fenix.utils.errors import ValidationError  # Error público del dominio.

# Un identificador completo: minúsculas ASCII, dígitos o guion, de 3 a 32 caracteres.
_IDENTIFIER = re.compile(r"[a-z0-9-]{3,32}")
# Solo dígitos ASCII. Evita lo que int() tolera: espacios, "+", "_" y dígitos Unicode.
_TTL_TEXT = re.compile(r"[0-9]+")
# Forma extendida de ISO 8601 con zona opcional (la ausencia se informa aparte).
# Se usa [0-9] y no \d porque \d también acepta dígitos de otros alfabetos.
_ISO_UTC = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?"
    r"(?P<zone>Z|[+-][0-9]{2}:[0-9]{2})?"
)

TTL_MIN = 60  # Segundos: límite inferior permitido por el contrato didáctico.
TTL_MAX = 600  # Segundos: límite superior permitido.


def identifier(value: str) -> str:
    """Valida un identificador corto y devuelve su texto sin modificar.

    Args:
        value: Texto entre 3 y 32 caracteres; letras minúsculas, dígitos o guion.
    Returns:
        El identificador original válido, sin normalizar.
    Raises:
        ValidationError: Si el tipo o el patrón no cumplen el contrato.
    Notes:
        Se usa fullmatch y no match/search: con "$" un salto de línea final
        ("abc\\n") pasaría. Tampoco se pasa a minúsculas: "AJENO" y "ajeno" son
        identidades distintas y corregirlo en silencio abriría una ambigüedad.
    Examples:
        identifier("grupo-01") devuelve "grupo-01"; "../ajeno" debe fallar.
    """
    if not isinstance(value, str):  # Un número, None o una lista no son identificadores.
        raise ValidationError("identifier debe ser texto")
    if _IDENTIFIER.fullmatch(value) is None:  # Cubre longitud, alfabeto y saltos de línea.
        raise ValidationError("identifier debe cumplir [a-z0-9-]{3,32}")
    return value  # Se devuelve el mismo texto: la identidad no cambia.


def ttl_seconds(value: str) -> int:
    """Valida el TTL didáctico empleado en la opción de entornos efímeros.

    Args:
        value: Texto con un entero entre 60 y 600 segundos inclusive.
    Returns:
        El número entero de segundos.
    Raises:
        ValidationError: Si no es entero o está fuera de los límites.
    Notes:
        La CLI traduce ValidationError a parser.error para salir con código 2.
        Solo se aceptan dígitos ASCII: int() por sí solo admitiría " 60 ", "+60",
        "6_0" o "٦٠", que no son entradas que el operador deba poder escribir.
        Aun así int() puede fallar con ValueError (cadenas de miles de dígitos
        superan el límite de conversión del intérprete), por eso se captura.
    """
    if not isinstance(value, str):  # La CLI entrega texto; otro tipo es un error de uso.
        raise ValidationError("ttl debe ser un entero en texto")
    if _TTL_TEXT.fullmatch(value) is None:  # Rechaza decimales, negativos y vacío.
        raise ValidationError("ttl debe contener solo dígitos")
    try:  # La conversión sigue siendo falible: ver Notes.
        ttl = int(value)
    except ValueError as error:  # Se conserva la causa original (sin datos sensibles).
        raise ValidationError("ttl no pudo convertirse a entero") from error
    if not TTL_MIN <= ttl <= TTL_MAX:  # Ambos extremos están incluidos.
        raise ValidationError(f"ttl debe estar entre {TTL_MIN} y {TTL_MAX} segundos")
    return ttl


def utc_text(value: str) -> datetime:
    """Interpreta una fecha UTC explícita sin aceptar fechas ingenuas.

    Args:
        value: ISO 8601 extendido (AAAA-MM-DDThh:mm:ss) terminado en Z o con
            offset +00:00; admite fracción de segundo de hasta 6 dígitos.
    Returns:
        Fecha con zona UTC; por ejemplo 2026-10-22T18:00:00+00:00.
    Raises:
        ValidationError: Si falta zona, hay offset no nulo o formato inválido.
    Notes:
        Una fecha sin zona es ambigua: depende del reloj de quien la lea. Un
        offset como -03:00 (hora argentina) es una fecha válida pero no UTC, y
        el contrato exige UTC explícito, así que también se rechaza. El patrón
        previo descarta variantes que fromisoformat toleraría (espacio en lugar
        de T, formato básico sin guiones) para que el formato no sea ambiguo.
    """
    if not isinstance(value, str):  # fromisoformat lanzaría TypeError con otro tipo.
        raise ValidationError("occurred_at debe ser texto")
    match = _ISO_UTC.fullmatch(value)  # Fija la forma antes de interpretarla.
    if match is None:
        raise ValidationError("fecha con formato ISO 8601 inválido")
    if match.group("zone") is None:  # Sin Z ni offset: no hay forma segura de comparar.
        raise ValidationError("la fecha debe indicar zona UTC explícita")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value  # Z -> +00:00.
    try:  # El patrón no valida el calendario: el mes 13 o el día 31 de abril llegan aquí.
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValidationError("fecha inexistente o fuera de rango") from error
    if parsed.utcoffset() != timedelta(0):  # Ej. -03:00: válida, pero no es UTC.
        raise ValidationError("la fecha debe estar en UTC (offset +00:00)")
    return parsed.astimezone(timezone.utc)  # Normaliza al objeto timezone.utc.