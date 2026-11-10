"""Recetas Boto3 entregadas para no exigir conocimientos previos de EC2.

Las decisiones de pertenencia, idempotencia, TTL y guardado del manifiesto siguen
siendo tarea del equipo. Estas funciones hacen un paso API, no el flujo completo.
"""
from fenix.config.settings import client  # Mantiene el laboratorio en loopback.
from fenix.utils.errors import PermanentError  # Describe un entorno no preparado.


def aws_tags(values: dict[str, str]) -> list[dict[str, str]]:
    """Convierte una política simple al formato de tags de S3 y EC2.

    Args:
        values: Mapa de claves y valores de texto, ya validado por el llamador.
    Returns:
        Lista de objetos con Key y Value.
    Raises:
        TypeError: Si values no es un diccionario apropiado.
    """
    return [{"Key": key, "Value": value} for key, value in values.items()]  # Formato SDK.


def mock_inputs() -> tuple[str, str]:
    """Descubre la VPC predeterminada y una AMI emulada disponible.

    Returns:
        Tupla (vpc_id, image_id) que se debe guardar en el inventario.
    Raises:
        PermanentError: Si el laboratorio no tiene VPC o imágenes para el ejemplo.
        ClientError: Si el emulador rechaza la consulta.
    Notes:
        No crea una VPC ni descarga imágenes. La cátedra prepara el emulador.
        Funciona con EC2_VM_MANAGER=mock; no habilitar modo Docker para este TP.
    """
    ec2 = client("ec2")  # Reutiliza configuración común y credenciales ficticias.
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"]
    images = ec2.describe_images()["Images"]  # Las imágenes pertenecen al emulador.
    if not vpcs or not images:  # Evita inventar IDs para continuar.
        raise PermanentError("La cátedra debe preparar VPC e ImageId mock")  # Falla claro.
    image = sorted(images, key=lambda row: row["ImageId"])[0]  # Selección estable.
    return vpcs[0]["VpcId"], image["ImageId"]  # Guardar ambos antes de crear EC2.


def create_bucket(name: str, tags: dict[str, str]) -> str:
    """Crea un bucket nuevo sin versionado y le aplica las etiquetas iniciales.

    Args:
        name: Nombre exclusivo del grupo, comprobado antes de invocar.
        tags: Etiquetas obligatorias ya validadas.
    Returns:
        Nombre del bucket creado.
    Raises:
        ClientError: Si existe, no hay permisos o falla el etiquetado.
    Side Effects:
        Crea almacenamiento S3 y aplica tags mediante una segunda llamada.
    Notes:
        Solo us-east-1 en este laboratorio. Si falla la segunda llamada, el bucket
        ya existe: el llamador debe recuperarlo y registrar el estado parcial.
    """
    s3 = client("s3")  # Usa path-style para LocalStack.
    s3.create_bucket(Bucket=name)  # En us-east-1 no se pasa LocationConstraint.
    s3.put_bucket_tagging(Bucket=name, Tagging={"TagSet": aws_tags(tags)})  # Segunda etapa.
    return name  # El llamador persiste la identidad y verifica tags al reconciliar.


def create_group(name: str, vpc_id: str, tags: dict[str, str]) -> str:
    """Crea un SG exclusivo de laboratorio sin reglas de entrada.

    Args:
        name: Nombre exclusivo del grupo y del entorno.
        vpc_id: VPC predeterminada obtenida del emulador; no se eliminará.
        tags: Etiquetas que identifican la pertenencia del SG.
    Returns:
        GroupId devuelto por EC2.
    Raises:
        ClientError: Si ya existe, los datos son inválidos o falla el servicio.
    Side Effects:
        Añade un grupo de seguridad; no abre puertos ni modifica otros SG.
    """
    response = client("ec2").create_security_group(  # Un paso de aprovisionamiento.
        GroupName=name, Description="Laboratorio PPAII", VpcId=vpc_id,
        TagSpecifications=[{"ResourceType": "security-group", "Tags": aws_tags(tags)}],
    )  # No equivale a un firewall real en modo mock.
    return response["GroupId"]  # Nunca deducir el ID a partir del nombre.


def create_instance(image_id: str, group_id: str, token: str,
                    tags: dict[str, str]) -> str:
    """Solicita una sola instancia mock con un token estable de idempotencia.

    Args:
        image_id: AMI mock descubierta y guardada por el grupo.
        group_id: SG propio recién creado o recuperado por el llamador.
        token: Texto ASCII estable de hasta 64 caracteres para el mismo entorno.
        tags: Etiquetas obligatorias del recurso.
    Returns:
        InstanceId informado por EC2.
    Raises:
        ClientError: Si la solicitud o el token contradicen una creación anterior.
    Side Effects:
        Crea o recupera la solicitud EC2 asociada al token y mismos parámetros.
    Notes:
        Cambiar parámetros con el mismo token puede producir conflicto.
        Guardar los parámetros originales; no generar un token nuevo en un retry.
    """
    response = client("ec2").run_instances(  # No ejecuta una VM real en modo mock.
        ImageId=image_id, InstanceType="t3.micro", MinCount=1, MaxCount=1,
        SecurityGroupIds=[group_id], ClientToken=token,
        TagSpecifications=[{"ResourceType": "instance", "Tags": aws_tags(tags)}],
    )  # La consulta posterior debe comprobar el estado antes de declararlo listo.
    return response["Instances"][0]["InstanceId"]  # Guardar inmediatamente en manifiesto.
