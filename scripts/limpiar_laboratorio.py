"""Limpia el laboratorio Fénix borrando únicamente los IDs del inventario del grupo.

Orden: terminar la instancia, vaciar y borrar el bucket, eliminar el grupo de
seguridad y, solo con --colas, borrar la cola de trabajo y la DLQ (hacerlo después
de documentar la DLQ). No toca recursos que no figuren en data/inventory.json.

Uso (raíz del proyecto, entorno activado, worker y receptor detenidos):
    python scripts/limpiar_laboratorio.py            # recursos EC2/S3
    python scripts/limpiar_laboratorio.py --colas    # además, colas SQS
"""
import argparse  # Opción explícita para borrar colas.
import os  # Prefijo de colas del entorno.
import sys  # Código de salida.
import time  # Espera acotada hasta terminated.
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv  # Mismo .env que la CLI.
from fenix.config.settings import client  # Solo endpoint local.
from fenix.utils.state import load  # Inventario del grupo.

INVENTORY = "data/inventory.json"


def terminate_node(record: dict) -> None:
    """Termina la instancia inventariada y espera terminated (máximo 15 consultas)."""
    ec2 = client("ec2")
    ec2.terminate_instances(InstanceIds=[record["id"]])
    for _ in range(15):
        state = ec2.describe_instances(InstanceIds=[record["id"]])[
            "Reservations"][0]["Instances"][0]["State"]["Name"]
        if state == "terminated":
            break
        time.sleep(1)
    print("instancia", record["id"], "->", state)
    if record.get("security_group_id"):  # El SG solo se borra sin instancias en uso.
        ec2.delete_security_group(GroupId=record["security_group_id"])
        print("grupo de seguridad", record["security_group_id"], "eliminado")


def delete_bucket(record: dict) -> None:
    """Vacía y borra el bucket inventariado."""
    s3 = client("s3")
    objects = s3.list_objects_v2(Bucket=record["id"]).get("Contents", [])
    if objects:  # delete_bucket exige un bucket vacío.
        s3.delete_objects(Bucket=record["id"], Delete={
            "Objects": [{"Key": row["Key"]} for row in objects]})
    s3.delete_bucket(Bucket=record["id"])
    print("bucket", record["id"], "eliminado")


def delete_queues(prefix: str) -> None:
    """Borra la cola de trabajo y la DLQ del prefijo del grupo."""
    sqs = client("sqs")
    for name in (f"{prefix}-events", f"{prefix}-dlq"):
        url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
        sqs.delete_queue(QueueUrl=url)
        print("cola", name, "eliminada")


def main() -> int:
    """Ejecuta la limpieza; informa cada paso y no oculta fallos."""
    load_dotenv()
    parser = argparse.ArgumentParser(description="Limpieza del laboratorio Fénix")
    parser.add_argument("--colas", action="store_true", help="también borra las colas SQS")
    args = parser.parse_args()
    inventory = load(INVENTORY)
    try:
        if inventory.get("node-01", {}).get("id"):
            terminate_node(inventory["node-01"])
        if inventory.get("bucket-01", {}).get("id"):
            delete_bucket(inventory["bucket-01"])
        if args.colas:
            delete_queues(os.environ["QUEUE_PREFIX"])
    except (ClientError, BotoCoreError, KeyError) as error:
        print("Limpieza incompleta:", type(error).__name__)
        return 1
    print(f"Listo. Recién ahora se puede borrar {INVENTORY} (ya no quedan recursos).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
