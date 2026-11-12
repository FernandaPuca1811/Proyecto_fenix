"""Pruebas del integrante 1 para identifier, ttl_seconds y utc_text.

No requieren Docker ni red. Ejecutar: python -m pytest -m i1 -q
"""
from datetime import datetime, timedelta, timezone

import pytest

from fenix.utils.errors import ProjectError, ValidationError
from fenix.utils.validation import identifier, ttl_seconds, utc_text

pytestmark = pytest.mark.i1  # Permite correr solo las pruebas de I1.


# --------------------------------------------------------------------------- identifier
@pytest.mark.parametrize("value", [
    "grupo-01", "abc", "a" * 32, "evt-node-001", "node-01", "---", "000",
])
def test_identifier_accepts_valid_and_does_not_normalize(value):
    """Un identificador válido vuelve idéntico: no se transforma ni se recorta."""
    assert identifier(value) == value


@pytest.mark.parametrize("value", [
    pytest.param("ab", id="demasiado-corto"),
    pytest.param("a" * 33, id="demasiado-largo"),
    pytest.param("", id="vacio"),
    pytest.param("AJENO", id="mayusculas"),
    pytest.param("../ajeno", id="ruta-relativa"),
    pytest.param("../AJENO", id="ruta-con-mayusculas"),
    pytest.param("abc\n", id="salto-de-linea-final"),
    pytest.param("\nabc", id="salto-de-linea-inicial"),
    pytest.param(" abc", id="espacio-inicial"),
    pytest.param("a b c", id="espacio-interno"),
    pytest.param("abc_def", id="guion-bajo"),
    pytest.param("ñandú", id="no-ascii"),
    pytest.param("arn:aws:s3:::bucket", id="arn"),
    pytest.param("٠٠٠", id="digitos-arabes"),
])
def test_identifier_rejects_invalid_text(value):
    """Rechaza longitud, alfabeto y rutas; \\n final no se cuela (fullmatch)."""
    with pytest.raises(ValidationError):
        identifier(value)


@pytest.mark.parametrize("value", [None, 123, True, b"abc", ["abc"], {"id": "abc"}, 1.5])
def test_identifier_rejects_non_text(value):
    """Solo los str pueden ser identificadores, aunque su contenido 'parezca' válido."""
    with pytest.raises(ValidationError):
        identifier(value)


# --------------------------------------------------------------------------- ttl_seconds
@pytest.mark.parametrize("text,expected", [("60", 60), ("600", 600), ("300", 300), ("61", 61), ("599", 599)])
def test_ttl_accepts_limits_inclusive(text, expected):
    """Los extremos 60 y 600 están incluidos."""
    assert ttl_seconds(text) == expected
    assert type(ttl_seconds(text)) is int


@pytest.mark.parametrize("text", ["59", "601", "0", "1", "10000"])
def test_ttl_rejects_out_of_range(text):
    """Fuera de 60..600 no se redondea ni se corrige: se rechaza."""
    with pytest.raises(ValidationError):
        ttl_seconds(text)


@pytest.mark.parametrize("text", [
    pytest.param("60.5", id="decimal"),
    pytest.param("-5", id="negativo"),
    pytest.param("-60", id="negativo-en-rango-absoluto"),
    pytest.param("+60", id="signo-mas"),
    pytest.param(" 60", id="espacio-inicial"),
    pytest.param("60 ", id="espacio-final"),
    pytest.param("6_0", id="guion-bajo"),
    pytest.param("1e2", id="notacion-cientifica"),
    pytest.param("٦٠", id="digitos-arabes"),
    pytest.param("", id="vacio"),
    pytest.param("abc", id="letras"),
])
def test_ttl_rejects_what_int_would_tolerate(text):
    """int() aceptaría varias de estas entradas; el contrato exige solo dígitos ASCII."""
    with pytest.raises(ValidationError):
        ttl_seconds(text)


@pytest.mark.parametrize("value", [None, 60, 60.0, True, ["60"]])
def test_ttl_rejects_non_text(value):
    """La CLI entrega texto: otro tipo es un error de uso, aunque valga 60."""
    with pytest.raises(ValidationError):
        ttl_seconds(value)


def test_ttl_huge_number_chains_original_cause():
    """Una cadena enorme de dígitos hace fallar int(): se traduce y se conserva la causa."""
    with pytest.raises(ValidationError) as info:
        ttl_seconds("9" * 5000)
    assert isinstance(info.value.__cause__, ValueError)  # raise ... from error


# --------------------------------------------------------------------------- utc_text
@pytest.mark.parametrize("text", [
    "2026-10-22T18:00:00Z",
    "2026-10-22T18:00:00+00:00",
    "2026-10-22T18:00:00.123456Z",
    "2026-10-22T18:00:00.5+00:00",
])
def test_utc_accepts_explicit_utc(text):
    """Acepta Z y +00:00, con o sin fracción, y siempre devuelve zona UTC."""
    parsed = utc_text(text)
    assert parsed.tzinfo is timezone.utc
    assert parsed.utcoffset() == timedelta(0)


def test_utc_z_and_offset_are_the_same_instant():
    """Z y +00:00 describen el mismo instante y se leen igual."""
    assert utc_text("2026-10-22T18:00:00Z") == utc_text("2026-10-22T18:00:00+00:00")
    assert utc_text("2026-10-22T18:00:00Z") == datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)


def test_utc_returns_iso_with_plus_zero_offset():
    """La representación coincide con la del logger (+00:00)."""
    assert utc_text("2026-10-22T18:00:00Z").isoformat() == "2026-10-22T18:00:00+00:00"


@pytest.mark.parametrize("text", [
    pytest.param("2026-10-22T18:00:00", id="ingenua-sin-zona"),
    pytest.param("2026-10-22", id="solo-fecha"),
    pytest.param("2026-10-22T18:00:00-03:00", id="hora-argentina"),
    pytest.param("2026-10-22T18:00:00+01:00", id="otro-offset-positivo"),
    pytest.param("2026-10-22T18:00:00+00:30", id="offset-no-nulo"),
])
def test_utc_rejects_missing_or_non_utc_zone(text):
    """Sin zona no hay comparación segura; con otro offset la fecha no es UTC."""
    with pytest.raises(ValidationError):
        utc_text(text)


@pytest.mark.parametrize("text", [
    pytest.param("2026-10-22 18:00:00Z", id="espacio-en-lugar-de-T"),
    pytest.param("20261022T180000Z", id="formato-basico"),
    pytest.param("2026-10-22T18:00Z", id="sin-segundos"),
    pytest.param("2026-10-22T18:00:00z", id="z-minuscula"),
    pytest.param("2026-10-22T18:00:00ZZ", id="doble-z"),
    pytest.param("2026-10-22T18:00:00Z\n", id="salto-de-linea-final"),
    pytest.param("2026-10-22T18:00:00.1234567Z", id="fraccion-de-7-digitos"),
    pytest.param("２０２６-10-22T18:00:00Z", id="digitos-fullwidth"),
    pytest.param("ayer", id="texto-libre"),
    pytest.param("", id="vacio"),
])
def test_utc_rejects_ambiguous_formats(text):
    """Formatos que fromisoformat toleraría pero que vuelven ambigua la entrada."""
    with pytest.raises(ValidationError):
        utc_text(text)


@pytest.mark.parametrize("text", [
    "2026-13-22T18:00:00Z",  # mes 13
    "2026-02-30T18:00:00Z",  # 30 de febrero
    "2026-04-31T18:00:00Z",  # 31 de abril
    "2026-10-22T25:00:00Z",  # hora 25
    "2026-10-22T18:60:00Z",  # minuto 60
])
def test_utc_rejects_impossible_calendar_dates(text):
    """El patrón fija la forma; el calendario lo valida fromisoformat (causa encadenada)."""
    with pytest.raises(ValidationError) as info:
        utc_text(text)
    assert isinstance(info.value.__cause__, ValueError)


@pytest.mark.parametrize("value", [None, 1761156000, 1.5, b"2026-10-22T18:00:00Z", datetime(2026, 10, 22, tzinfo=timezone.utc)])
def test_utc_rejects_non_text(value):
    """Un epoch o un datetime no son texto ISO: se rechazan sin TypeError."""
    with pytest.raises(ValidationError):
        utc_text(value)


# --------------------------------------------------------------------------- errores
@pytest.mark.parametrize("call", [
    lambda: identifier("TOKEN-SECRETO-ABC123"),
    lambda: ttl_seconds("TOKEN-SECRETO-ABC123"),
    lambda: utc_text("TOKEN-SECRETO-ABC123"),
])
def test_error_messages_never_echo_the_rejected_value(call):
    """El valor rechazado podría ser un secreto: no debe aparecer en el mensaje."""
    with pytest.raises(ValidationError) as info:
        call()
    assert "SECRETO" not in str(info.value)
    assert "ABC123" not in str(info.value)


def test_validation_error_is_a_project_error():
    """La CLI captura ProjectError: un ValidationError debe ser uno."""
    assert issubclass(ValidationError, ProjectError)
    assert ProjectError.__bases__ == (Exception,)  # Hereda de Exception, no de BaseException.
