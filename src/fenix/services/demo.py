"""Laboratorio acotado de recursos. Integrante 2 prepara, integrante 6 verifica."""
from botocore.exceptions import ClientError
from fenix.config.settings import client
from fenix.services import cloud_support
from fenix.utils.state import load, save
from fenix.utils.validation import identifier


# ── Constantes del laboratorio ─────────────────────────────────────────
_INVENTORY_PATH = "data/inventory.json"
_REQUIRED_TAGS_KEYS = ("ManagedBy", "Project", "GroupId", "Owner")


def _resource_tags(group: str) -> dict[str, str]:
    """Genera las etiquetas obligatorias para cualquier recurso del grupo.

    Args:
        group: Identificador validado del grupo.
    Returns:
        Diccionario con las cuatro etiquetas requeridas por la cátedra.
    """
    return {
        "ManagedBy": "ppa2",
        "Project": "fenix",
        "GroupId": group,
        "Owner": group,
    }


def _find_instance_by_tags(ec2, group: str) -> str | None:
    """Busca una instancia existente por etiquetas del proyecto.

    Args:
        ec2: Cliente EC2 de Boto3.
        group: Identificador del grupo para filtrar por tags.
    Returns:
        InstanceId de la instancia encontrada (no terminada) o None.
    Raises:
        ClientError: Si la API del emulador rechaza la consulta.
    """
    response = ec2.describe_instances(Filters=[
        {"Name": "tag:Project", "Values": ["fenix"]},
        {"Name": "tag:GroupId", "Values": [group]},
        {"Name": "tag:ManagedBy", "Values": ["ppa2"]},
    ])  # Filtra únicamente recursos del grupo en el emulador.
    for reservation in response.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            state = inst.get("State", {}).get("Name", "")
            if state not in ("terminated", "shutting-down"):
                return inst["InstanceId"]  # Reutiliza la instancia existente.
    return None  # No encontró instancia activa con esos tags.


def _find_sg_by_name(ec2, sg_name: str) -> str | None:
    """Busca un Security Group existente por nombre.

    Args:
        ec2: Cliente EC2 de Boto3.
        sg_name: Nombre del Security Group a buscar.
    Returns:
        GroupId del SG encontrado o None.
    Raises:
        ClientError: Si la API del emulador rechaza la consulta.
    """
    try:
        response = ec2.describe_security_groups(
            GroupNames=[sg_name],
        )  # Busca por nombre exacto; falla si no existe.
        groups = response.get("SecurityGroups", [])
        if groups:
            return groups[0]["GroupId"]
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code", "")
        if code in ("InvalidGroup.NotFound",):
            return None  # El SG todavía no fue creado.
        raise  # Propaga errores inesperados sin ocultarlos.
    return None


def _bucket_exists(s3, bucket_name: str) -> bool:
    """Verifica si un bucket S3 ya existe en el emulador.

    Args:
        s3: Cliente S3 de Boto3.
        bucket_name: Nombre del bucket a verificar.
    Returns:
        True si el bucket existe, False en caso contrario.
    Raises:
        ClientError: Si falla por razones distintas a la inexistencia.
    """
    try:
        s3.head_bucket(Bucket=bucket_name)
        return True  # El bucket ya fue creado.
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchBucket"):
            return False  # El bucket no existe; se puede crear.
        raise  # Propaga errores inesperados.


def setup_demo(group: str) -> dict:
    """Prepara una instancia mock y un bucket a partir de identificadores estables.

    Args:
        group: ID validado del grupo; determina prefijos de los recursos.
    Returns:
        Inventario con aliases node-01 y bucket-01 y sus IDs reales del emulador.
    Raises:
        ClientError: Si el emulador no soporta una operación o falla su ejecución.
        ValidationError: Si el identificador del grupo no es válido.
        PermanentError: Si el emulador no tiene VPC o imágenes disponibles.
    Side Effects:
        Crea recursos de laboratorio etiquetados y data/inventory.json.
    Notes:
        Reutilizar por tags antes de crear y guardar IDs después de cada creación.
        Obtener una ImageId con describe_images; no copiar una AMI de AWS real.
    """
    group = identifier(group)  # Valida formato; propaga ValidationError.
    tags = _resource_tags(group)
    ec2 = client("ec2")
    s3 = client("s3")

    # ── Descubrir VPC e imagen mock del emulador ───────────────────────
    vpc_id, image_id = cloud_support.mock_inputs()  # PermanentError si no hay.

    # ── Security Group: crear o reutilizar ─────────────────────────────
    sg_name = f"fenix-{group}-sg"
    sg_id = _find_sg_by_name(ec2, sg_name)
    if sg_id is None:
        sg_id = cloud_support.create_group(sg_name, vpc_id, tags)

    # ── Instancia EC2 mock: crear o reutilizar ─────────────────────────
    instance_id = _find_instance_by_tags(ec2, group)
    client_token = f"fenix-{group}-node-01"  # Token estable para idempotencia.
    instance_tags = {**tags, "Name": f"fenix-{group}-node-01"}
    if instance_id is None:
        instance_id = cloud_support.create_instance(
            image_id, sg_id, client_token, instance_tags,
        )

    # ── Bucket S3 mock: crear o reutilizar ─────────────────────────────
    bucket_name = f"fenix-{group}-demo"
    if not _bucket_exists(s3, bucket_name):
        cloud_support.create_bucket(bucket_name, tags)

    # ── Construir inventario ───────────────────────────────────────────
    inventory = {
        "node-01": {
            "type": "ec2",
            "id": instance_id,
            "security_group_id": sg_id,
            "expected_tags": tags,
        },
        "bucket-01": {
            "type": "s3",
            "id": bucket_name,
            "expected_tags": tags,
        },
    }

    # ── Persistencia atómica ───────────────────────────────────────────
    save(_INVENTORY_PATH, inventory)  # Reemplazo atómico via os.replace.
    return inventory  # Retorna el inventario para inspección y CLI.


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
