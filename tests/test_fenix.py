"""Primer contrato de HMAC; integrante 6 agrega el resto de la matriz."""
import pytest  # Verifica la excepción pública de autenticación.
from fenix.utils.crypto import signature, verify
from fenix.utils.errors import ValidationError

@pytest.mark.pendiente
def test_signature_authenticates_raw_bytes():
    """Acepta el original y rechaza la misma información con otros bytes.

    Returns:
        None si el verificador respeta el contrato HMAC.
    Raises:
        AssertionError: Si no se rechaza el cuerpo alterado.
        NotImplementedError: Hasta que se implemente el verificador.
    Notes:
        La clave que sigue es solo una fixture pública sin uso fuera del test.
    """
    secret = "clave-ficticia-solo-test"  # No es un secreto de ningún servicio.
    body = b'{"x":1}'  # Espacios distintos producen firmas distintas.
    digest = signature(secret, 1000, body)  # El reloj de la prueba es fijo.
    header = f"t=1000,v1={digest}"  # Mismo formato que el emisor real.
    verify(secret, header, body, now=1000)  # Original debe aceptarse.
    with pytest.raises(ValidationError):  # JSON equivalente no conserva los bytes.
        verify(secret, header, b'{"x": 1}', now=1000)  # Cuerpo modificado.
