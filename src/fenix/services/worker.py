"""Plantilla central de integración: un worker, un mensaje por vez."""
import json  # Convierte Body solo dentro del control por mensaje.
import time  # Evita un bucle intensivo si el emulador no responde.
from botocore.exceptions import BotoCoreError, ClientError
from fenix.services.sqs import receive, acknowledge, retry_later
from fenix.utils.errors import ProjectError


def run(url: str, handler, logger, once: bool = False) -> None:
    """Consume mensajes y confirma únicamente resultados durables.

    Args:
        url: URL de la cola de trabajo, obtenida por get_queue_url.
        handler: Callable handler(event) que revalida y ejecuta el caso de negocio.
        logger: Logger JSON del proceso worker.
        once: Si es True, realiza una sola consulta y termina; útil para pruebas.
    Returns:
        None al terminar la consulta única o al detenerse mediante Ctrl+C.
    Raises:
        NotImplementedError: Si todavía no se completaron las plantillas del alumno.
    Side Effects:
        Recibe mensajes, ejecuta el handler y cambia o confirma su recepción.
    Notes:
        Completar I5-A antes de usar. El parseo debe ocurrir por mensaje para que
        un JSON corrupto no termine el worker ni oculte los otros mensajes.
    """
    # TODO I5-A: implementar el algoritmo de la sección 4 de la consigna.
    # 1. Abrir un bucle; receive(url) queda en un try para fallos de transporte.
    # 2. Para cada mensaje, definir el contexto message_id antes de json.loads.
    # 3. Dentro del try por mensaje: decodificar JSON, exigir dict y llamar handler.
    # 4. acknowledge solo si handler terminó y persistió todos sus pasos.
    # 5. JSON/validación/negocio/red: registrar tipo seguro y llamar retry_later.
    # 6. Un fallo del ACK también deja el evento disponible; handler es idempotente.
    # 7. Nunca ACK en finally, ni borrar antes de notificar/persistir resultado.
    # 8. Fallos del transporte o de retry_later: registrar y volver tras 2 segundos.
    # 9. once termina tras una consulta; Ctrl+C sale y el main cierra el listener.
    # 10. Los NotImplementedError deben propagarse: revelan que falta completar.
    raise NotImplementedError("I5-A: implementar ciclo de vida del worker")
