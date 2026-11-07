"""Laboratorio acotado de recursos. Integrante 2 prepara, integrante 6 verifica."""
from fenix.config.settings import client
from fenix.utils.state import load, save
from fenix.utils.validation import identifier


def setup_demo(group: str) -> dict:
    """Prepara una instancia mock y un bucket a partir de identificadores estables.

    Args:
        group: ID validado del grupo; determina prefijos de los recursos.
    Returns:
        Inventario con aliases node-01 y bucket-01 y sus IDs reales del emulador.
    Raises:
        ClientError: Si el emulador no soporta una operación o falla su ejecución.
        NotImplementedError: Mientras falte TODO I2-C.
    Side Effects:
        Crea recursos de laboratorio etiquetados y data/inventory.json.
    Notes:
        Reutilizar por tags antes de crear y guardar IDs después de cada creación.
        Obtener una ImageId con describe_images; no copiar una AMI de AWS real.
    """
    # TODO I2-C: validar group; localizar VPC predeterminada mediante describe_vpcs.
    # Crear/reusar SG fenix-{group}-sg con tag ManagedBy=ppa2 y GroupId=group.
    # Usar describe_images y registrar la ImageId mock elegida en inventory.json.
    # Crear/reusar instancia por tags Project=fenix/GroupId/Name; ClientToken estable.
    # run_instances: MinCount=MaxCount=1, tipo t3.micro y SecurityGroupIds=[sg_id].
    # Crear/reusar bucket fenix-{group}-demo; región us-east-1 para este laboratorio.
    # Aplicar tags ManagedBy=ppa2, Project=fenix, GroupId=group y Owner=group.
    # Publicar inventario solo con recursos cuya propiedad haya sido verificada.
    raise NotImplementedError("I2-C: crear laboratorio e inventario")


def inject_fault(alias: str) -> None:
    """Detiene el nodo o elimina una etiqueta del bucket de laboratorio.

    Args:
        alias: node-01 o bucket-01 presente en inventory.json.
    Returns:
        None cuando el fallo simulado se verifica mediante el SDK.
    Raises:
        ValidationError: Si el alias no pertenece al inventario.
        NotImplementedError: Mientras falte TODO I6-A.
    Side Effects:
        Modifica únicamente el recurso de prueba de ese grupo.
    """
    # TODO I6-A: resolver el alias en inventario; jamás recibir InstanceId libre.
    # node-01: stop_instances y comprobar stopped, con consultas limitadas.
    # bucket-01: quitar Owner conservando el resto de etiquetas.
    # Esta función fabrica el problema; no debe enviar el evento automáticamente.
    raise NotImplementedError("I6-A: inyectar y comprobar fallo")
