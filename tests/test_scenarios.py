"""Guía ejecutable del integrante 6; sustituir cada pendiente por aserciones reales."""
import pytest  # Mantiene visibles los casos sin implementar.

@pytest.mark.pendiente
def test_duplicate_is_idempotent():
    """Comprueba que repetir un evento no repite recursos ni acciones completadas.

    Returns:
        None cuando dos ejecuciones conservan el estado final esperado.
    Raises:
        NotImplementedError: Hasta que I6 implemente la preparación y aserciones.
    Notes:
        Usar un cliente falso con contador de llamadas; reinicializar el handler
        leyendo el mismo ledger para que el ensayo represente un reinicio.
    """
    # TODO I6-B: preparar inventario/manifiesto válido en tmp_path y evento fijo.
    # Ejecutar handler dos veces; verificar IDs y una sola acción efectiva.
    # Guardar/releer el estado antes de la segunda ejecución; no usar un set en memoria.
    raise NotImplementedError("I6-B: prueba durable de duplicados")

@pytest.mark.pendiente
def test_ack_is_after_success():
    """Verifica que un handler fallido jamás confirma la entrega de SQS.

    Returns:
        None si fallo=retry sin ACK y éxito=ACK exactamente al finalizar.
    Raises:
        NotImplementedError: Hasta que I6 conecte fixtures y mocks del transporte.
    Notes:
        No reemplazar el worker entero por un mock: observar llamadas reales de run.
    """
    # TODO I6-C: monkeypatch receive para devolver una entrega con ReceiptHandle fijo.
    # Reemplazar acknowledge y retry_later por funciones que anoten el orden.
    # Ejecutar run(once=True) con handler fallido y exigir cero ACK.
    # Repetir con handler exitoso y exigir handler primero, acknowledge después.
    raise NotImplementedError("I6-C: probar orden y condición del ACK")

@pytest.mark.integracion
def test_redrive_with_localstack():
    """Comprueba DLQ efectiva usando el broker, no una lista simulada en Python.

    Returns:
        None si un mensaje venenoso termina en la DLQ y el worker sigue vivo.
    Raises:
        NotImplementedError: Hasta implementar el ensayo con espera total acotada.
    Side Effects:
        Crea colas de prueba exclusivas y las limpia después de guardar evidencia.
    Notes:
        Usar un prefijo de test nuevo y no purgar colas del laboratorio del grupo.
    """
    # TODO I6-D: publicar JSON corrupto en una cola con RedrivePolicy real.
    # Consumir con el worker y consultar DLQ con deadline total de 120 segundos.
    # Comparar el Body original; un contador aproximado por sí solo no demuestra redrive.
    # Si se agota el deadline, fallar con diagnóstico; no pasar el test por timeout.
    raise NotImplementedError("I6-D: verificar redrive real en LocalStack")
