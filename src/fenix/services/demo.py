"""Laboratorio acotado de recursos. Integrante 2 prepara, integrante 6 verifica."""
import time  # Espera acotada hasta observar stopped.
from botocore.exceptions import ClientError
from fenix.config.settings import client
from fenix.services import cloud_support
from fenix.utils.errors import PermanentError, RetryableError, ValidationError
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
        PermanentError: Recurso inexistente, terminado o con identidad ajena.
        RetryableError: El nodo no llegó a stopped dentro de la espera acotada.
        ClientError: Si el emulador rechaza la operación; la CLI lo registra.
    Side Effects:
        Modifica únicamente el recurso de prueba de ese grupo.
    Notes:
        Es idempotente: si la falla ya está presente, no repite la acción.
        Fabrica el problema; no envía el evento: eso lo hace la CLI send.
    """
    resource = load(_INVENTORY_PATH).get(alias) if isinstance(alias, str) else None
    if not isinstance(resource, dict) or not resource.get("id"):  # Nunca un ID libre.
        raise ValidationError("El alias no pertenece al inventario")
    expected = _tag_map(resource.get("expected_tags"))
    if resource.get("type") == "ec2":
        _stop_node(resource["id"], expected)
    elif resource.get("type") == "s3":
        _drop_owner(resource["id"], expected)
    else:
        raise ValidationError("Tipo de recurso no soportado por inject_fault")


# ── Inyección de fallas (I6-A) ─────────────────────────────────────────
FAULT_POLLS = 15  # Consultas máximas al inyectar la falla del nodo.
FAULT_PAUSE = 1  # Segundos entre consultas.


def _tag_map(tags: list[dict] | dict | None) -> dict:
    """Normaliza etiquetas de Boto3 (lista Key/Value) o del inventario (dict) a dict."""
    if tags is None:
        return {}
    if isinstance(tags, dict):
        return dict(tags)
    return {row["Key"]: row["Value"] for row in tags}


def _identity_ok(current: dict, expected: dict) -> None:
    """Falla si una etiqueta de identidad del recurso contradice el inventario."""
    for key in ("ManagedBy", "Project", "GroupId"):
        if key in current and key in expected and current[key] != expected[key]:
            raise PermanentError(f"La etiqueta {key} contradice el inventario")


def _node_state(ec2, instance_id: str) -> dict:
    """Devuelve la instancia del inventario o falla si ya no existe."""
    reservations = ec2.describe_instances(InstanceIds=[instance_id]).get("Reservations", [])
    if not reservations or not reservations[0].get("Instances"):
        raise PermanentError("La instancia del inventario no existe")
    return reservations[0]["Instances"][0]


def _stop_node(instance_id: str, expected: dict) -> None:
    """Detiene la instancia del grupo y comprueba stopped con consultas limitadas."""
    ec2 = client("ec2")
    instance = _node_state(ec2, instance_id)
    _identity_ok(_tag_map(instance.get("Tags")), expected)  # Solo recursos propios.
    state = instance["State"]["Name"]
    if state in ("terminated", "shutting-down"):
        raise PermanentError("La instancia está terminada; volver a ejecutar setup-demo")
    if state in ("pending", "running"):  # stopped/stopping no necesitan otra orden.
        ec2.stop_instances(InstanceIds=[instance_id])
    for _ in range(FAULT_POLLS):  # Espera acotada: nunca un bucle infinito.
        if _node_state(ec2, instance_id)["State"]["Name"] == "stopped":
            return
        time.sleep(FAULT_PAUSE)
    raise RetryableError("La instancia no llegó a stopped dentro de la espera")


def _drop_owner(bucket: str, expected: dict) -> None:
    """Quita Owner del bucket del grupo conservando las demás etiquetas."""
    s3 = client("s3")
    try:
        current = _tag_map(s3.get_bucket_tagging(Bucket=bucket)["TagSet"])
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code == "NoSuchTagSet":  # Sin etiquetas: Owner ya falta.
            return
        if code in ("NoSuchBucket", "404"):
            raise PermanentError("El bucket del inventario no existe") from error
        raise
    _identity_ok(current, expected)  # No altera un bucket ajeno.
    if "Owner" not in current:  # Falla ya presente: idempotente.
        return
    remaining = {key: value for key, value in current.items() if key != "Owner"}
    if remaining:
        s3.put_bucket_tagging(Bucket=bucket,
                              Tagging={"TagSet": cloud_support.aws_tags(remaining)})
    else:  # Un TagSet vacío se expresa borrando el etiquetado.
        s3.delete_bucket_tagging(Bucket=bucket)
    after = _tag_map(s3.get_bucket_tagging(Bucket=bucket)["TagSet"]) if remaining else {}
    if "Owner" in after or after != remaining:  # Verificación por relectura.
        raise RetryableError("No se pudo verificar la falla del bucket")
