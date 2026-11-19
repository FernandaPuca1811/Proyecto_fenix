"""Evidencia individual del integrante 1: un evento correcto y tres rechazos.

Reproduce el orden que sigue el receptor (validar y solo entonces publicar) con un
publicador simulado que cuenta llamadas. Demuestra que un evento rechazado no llega
a la etapa de publicación. No usa red ni Docker.

Limitación honesta: esto prueba la validación de I1, no el receptor HTTP. La prueba
completa (HTTP 422 y ningún mensaje nuevo en SQS) se registra cuando I3 termine el
receptor y se verifica contra LocalStack.

Uso (desde la raíz del proyecto, con el entorno activado):
    python scripts/evidencia_i1.py [--inventory data/inventory.json]
"""
import argparse  # Permite elegir el inventario real del grupo.
import json  # Cada resultado se imprime como una línea JSON.
import sys  # Código de salida según se cumpla o no lo esperado.
from pathlib import Path

from fenix.services.events import validate
from fenix.utils.errors import ValidationError

ROOT = Path(__file__).resolve().parents[1]
# Inventario de muestra con la forma que deja setup-demo (IDs ficticios).
SAMPLE_INVENTORY = {
    "node-01": {"type": "ec2", "id": "i-0123456789abcdef0", "security_group_id": "sg-0123456789abcdef0",
                "expected_tags": {"ManagedBy": "ppa2", "Project": "fenix", "GroupId": "grupo01", "Owner": "grupo01"}},
    "bucket-01": {"type": "s3", "id": "fenix-grupo01-demo",
                  "expected_tags": {"ManagedBy": "ppa2", "Project": "fenix", "GroupId": "grupo01", "Owner": "grupo01"}},
}


def process(event, inventory, publish) -> None:
    """Valida y, solo si el evento es correcto, publica.

    Args:
        event: Valor ya decodificado de JSON (puede no ser un objeto).
        inventory: Mapa alias -> recurso.
        publish: Callable que representa send_message a SQS.
    Raises:
        ValidationError: Si el evento no cumple el contrato; publish no se invoca.
    """
    publish(validate(event, inventory))  # validate lanza antes de que se llame a publish.


def main() -> int:
    """Ejecuta los cuatro casos e imprime una línea JSON por caso.

    Returns:
        0 si hubo una publicación y tres rechazos; 1 en cualquier otro caso.
    """
    parser = argparse.ArgumentParser(description="Evidencia de I1")
    parser.add_argument("--inventory", help="Ruta a data/inventory.json (por defecto, muestra ficticia)")
    args = parser.parse_args()
    if args.inventory:
        inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
        source = "archivo"
    else:
        inventory, source = SAMPLE_INVENTORY, "muestra"
    good = json.loads((ROOT / "examples" / "node-event.json").read_text(encoding="utf-8"))
    cases = [  # (nombre, evento)
        ("evento-correcto", good),
        ("rechazo-alias-ajeno", {**good, "event_id": "evt-ajeno-001", "resource_id": "node-99"}),
        ("rechazo-clave-extra", {**good, "event_id": "evt-extra-001", "instance_id": "i-0123456789abcdef0"}),
        ("rechazo-bool-como-entero", {**good, "event_id": "evt-bool-001", "simulate_failures": True}),
    ]
    published = []  # El publicador simulado solo anota lo que recibe.
    accepted = rejected = 0
    for name, event in cases:
        before = len(published)
        try:
            process(event, inventory, published.append)
            accepted += 1
            line = {"case": name, "result": "accepted", "error": None}
        except ValidationError as error:
            rejected += 1
            line = {"case": name, "result": "rejected", "error": str(error)}
        line["published"] = len(published) - before  # 1 solo para el evento correcto.
        print(json.dumps(line, ensure_ascii=False))
    ok = accepted == 1 and rejected == 3 and len(published) == 1
    print(json.dumps({"inventory": source, "accepted": accepted, "rejected": rejected,
                      "sqs_publications": len(published), "expected_met": ok}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
