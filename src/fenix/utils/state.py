"""Persistencia de apoyo para UNA instancia de CLI/worker por proyecto."""
import json  # Lee y escribe el pequeño estado docente.
from pathlib import Path  # Maneja rutas del sistema anfitrión.
import os  # Publica archivos mediante reemplazo atómico.


def load(path: str) -> dict:
    """Carga estado persistente sin ocultar corrupción.

    Args:
        path: Archivo JSON dentro de data/.
    Returns:
        Diccionario guardado o {} si todavía no existe.
    Raises:
        JSONDecodeError: Si el archivo existe y está corrupto.
        OSError: Si faltan permisos u ocurre un error de disco.
    Notes:
        JSON corrupto detiene la operación; no se supone estado vacío.
    """
    target = Path(path)  # Mantiene el acceso dentro del archivo seleccionado.
    if not target.exists():  # La ausencia inicial es un caso normal.
        return {}  # Un archivo dañado nunca pasa por esta rama.
    return json.loads(target.read_text(encoding="utf-8"))  # Propaga corrupción.


def save(path: str, data: dict) -> None:
    """Reemplaza un estado completo de forma atómica en el mismo directorio.

    Args:
        path: Archivo de destino dentro de data/.
        data: Diccionario serializable; no incluir secretos.
    Returns:
        None.
    Raises:
        OSError: Si no puede escribirse o reemplazarse el archivo.
        TypeError: Si data no es serializable.
    Side Effects:
        Crea el directorio y reemplaza el archivo mediante os.replace.
    Notes:
        No proporciona bloqueo entre procesos ni transacciones con AWS.
    """
    target = Path(path)  # Debe ser una ruta elegida por la aplicación.
    target.parent.mkdir(parents=True, exist_ok=True)  # Prepara data/.
    temp = target.with_suffix(".tmp")  # Escribe dentro del mismo filesystem.
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")  # Serializa antes.
    os.replace(temp, target)  # Los lectores ven la versión anterior o la nueva.
