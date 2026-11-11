"""Pruebas operativas del apoyo entregado; no prueban que el final esté resuelto."""
import json  # Lee el estado generado por el helper.
import pytest  # Define marcas y casos repetibles.
from fenix.utils.state import load, save
from fenix.utils.logging_json import setup
from fenix.services.sqs import publish

@pytest.mark.base
def test_state_survives_restart(tmp_path):
    """Comprueba dos escrituras y una nueva lectura del estado.

    Args:
        tmp_path: Carpeta temporal aislada provista por pytest.
    Returns:
        None si se conserva el último valor.
    Raises:
        AssertionError: Si se pierde información persistida.
    """
    path = str(tmp_path / "state.json")  # No usa datos reales del grupo.
    save(path, {"repair_done": True})  # Simula la finalización de un paso.
    assert load(path)["repair_done"] is True  # Comprueba lectura posterior.
    save(path, {"repair_done": True, "notified": True})  # Añade una etapa.
    assert load(path)["notified"] is True  # La nueva versión reemplaza la anterior.

@pytest.mark.base
def test_corrupt_state_is_not_empty(tmp_path):
    """Evita que un JSON roto se trate como estado inicial.

    Args:
        tmp_path: Carpeta temporal aislada de pytest.
    Returns:
        None si la corrupción produce un error visible.
    Raises:
        AssertionError: Si se oculta la corrupción.
    """
    path = tmp_path / "broken.json"  # Construye una ruta de ensayo.
    path.write_text("{bad", encoding="utf-8")  # Introduce un fallo deliberado.
    with pytest.raises(json.JSONDecodeError):  # El helper no debe silenciarlo.
        load(str(path))  # Intenta leer el archivo dañado.

@pytest.mark.base
def test_delay_is_bounded():
    """Rechaza un delay de más de quince minutos antes de conectar con SQS.

    Returns:
        None si se aplica el límite del servicio.
    Raises:
        AssertionError: Si se intenta aceptar un retardo inválido.
    """
    with pytest.raises(ValueError):  # Esta prueba no necesita Docker.
        publish("unused", {"event_id": "evt-001"}, delay=901)  # Caso de borde.

@pytest.mark.base
def test_json_log_context(tmp_path, monkeypatch):
    """Verifica el contexto de correlación y el cierre de QueueListener.

    Args:
        tmp_path: Carpeta donde guardar el log de prueba.
        monkeypatch: Fixture que restaura el directorio después del ensayo.
    Returns:
        None si cada línea es JSON y conserva event_id.
    Raises:
        AssertionError: Si falta correlación o fecha con zona UTC.
    """
    monkeypatch.chdir(tmp_path)  # Aísla los logs del ensayo.
    logger, listener = setup("test")  # Inicia un único pipeline.
    try:  # Asegura vaciar la cola aunque falle la aserción posterior.
        logger.info("recibido", extra={"event_id": "evt-001"})  # Contexto público.
    finally:  # La limpieza no decide éxito del negocio.
        listener.stop()  # Drena los registros pendientes.
    row = json.loads(next((tmp_path / "logs").glob("*.jsonl")).read_text())
    assert row["event_id"] == "evt-001"  # Conserva correlación.
    assert row["timestamp"].endswith("+00:00")  # Fecha UTC explícita.
