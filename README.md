# 🔥 Proyecto Fénix — Sistema de Autorreparación de Infraestructura

**TP Final — Programación para Automatización II (PPA2)**

Sistema de autorreparación de infraestructura cloud emulada que detecta desviaciones en recursos EC2 y S3, las corrige de forma convergente y notifica el resultado. Toda la operación se ejecuta contra **LocalStack** (emulador local de AWS) sin credenciales ni recursos reales.

---

## Arquitectura

```
┌──────────────┐    HMAC-SHA256     ┌──────────────────┐    SQS     ┌──────────────┐
│  CLI send    │ ──────────────────►│  Receptor HTTP   │ ─────────► │  Cola SQS    │
│  (firma +    │   POST /webhook    │  (FastAPI/Uvicorn)│  publish   │  events      │
│   evento)    │                    │  verify → validate│           │              │
└──────────────┘                    │  → enqueue (202) │           └──────┬───────┘
                                    └──────────────────┘                  │ receive
                                                                         ▼
┌──────────────┐    Telegram/       ┌──────────────────┐          ┌──────────────┐
│  Canal de    │ ◄──────────────────│  Remediación     │ ◄────────│   Worker     │
│  Notificación│    console         │  (repair + notify)│  handler │  (1 msg/vez) │
└──────────────┘                    │  ledger durable  │          │  ACK/retry   │
                                    └──────────────────┘          └──────────────┘
                                           │                             │
                                           ▼                             │ backoff
                                    ┌──────────────┐              ┌──────────────┐
                                    │ EC2 / S3     │              │   DLQ        │
                                    │ (LocalStack) │              │  (maxReceive │
                                    └──────────────┘              │   Count = 3) │
                                                                  └──────────────┘
```

**Flujo principal:**

1. `CLI send` firma un evento JSON con HMAC-SHA256 y lo envía vía HTTP al receptor.
2. El **receptor FastAPI** verifica la firma, valida el evento contra el inventario y lo encola en SQS (respuesta `202 Accepted`).
3. El **worker** consume mensajes de la cola uno a uno con long polling (10 s).
4. El **handler de remediación** revalida el evento, repara el recurso (arranca EC2 o restaura tags de S3) y notifica por Telegram/consola, registrando cada etapa en un ledger durable (`data/events.json`).
5. Solo tras el éxito durable del handler se ejecuta el ACK (delete del mensaje). Ante fallas, se aplica **backoff exponencial** vía `change_message_visibility`. Tras 3 recepciones fallidas, SQS aísla el mensaje en la **DLQ**.

---

## Requisitos Previos

| Requisito               | Versión mínima                                             |
| ----------------------- | ---------------------------------------------------------- |
| Python                  | 3.11+                                                      |
| Docker + Docker Compose | Para ejecutar LocalStack                                   |
| LocalStack              | Imagen `localstack/localstack:4.14.0` (definida en `.env`) |

### Dependencias Python

Declaradas en [`requirements.txt`](requirements.txt):

```
boto3
fastapi
uvicorn
requests
python-dotenv
pytest
httpx
```

---

## Instalación y Despliegue

### 1. Clonar y crear entorno virtual

```bash
git clone <URL_DEL_REPOSITORIO>
cd tp-final
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Linux/macOS
source .venv/bin/activate
```

### 2. Instalar el proyecto en modo editable

```bash
python -m pip install -e .
```

### 3. Configurar variables de entorno

Crear el archivo `.env` en la raíz del proyecto (está ignorado por `.gitignore`):

```env
AWS_ENDPOINT_URL=http://127.0.0.1:4566
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
AWS_DEFAULT_REGION=us-east-1
GROUP_ID=grupo01
QUEUE_PREFIX=fenix-grupo01
PROJECT=fenix
WEBHOOK_SECRET=<secreto_de_al_menos_16_caracteres>
NOTIFY_PROVIDER=telegram
TELEGRAM_BOT_TOKEN=<token_del_bot>
TELEGRAM_CHAT_ID=<chat_id>
LOCALSTACK_AUTH_TOKEN=<token_de_localstack>
LOCALSTACK_IMAGE=localstack/localstack:4.14.0
```

> **Nota:** `NOTIFY_PROVIDER=console` imprime las notificaciones en terminal en lugar de enviarlas por Telegram.

### 4. Levantar LocalStack

```bash
docker compose up -d
```

Esto inicia LocalStack con los servicios `sqs`, `s3` y `ec2` en modo mock (`EC2_VM_MANAGER=mock`) en `http://localhost:4566`. Python se ejecuta en el host, no dentro del contenedor.

---

## Uso de la CLI

Todos los comandos se ejecutan desde la raíz del proyecto con el entorno virtual activado.

### Inicializar colas SQS

```bash
python -m fenix.cli init-queues
```

Crea la Dead Letter Queue (`{QUEUE_PREFIX}-dlq`) y la cola principal (`{QUEUE_PREFIX}-events`) con `RedrivePolicy` (`maxReceiveCount=3`). Es **idempotente**: si las colas ya existen, retorna las mismas URLs.

### Provisionar infraestructura de laboratorio

```bash
python -m fenix.cli setup-demo
```

Crea o reutiliza los recursos mock en LocalStack:

- **`node-01`**: Security Group + instancia EC2 (`t3.micro`) con `ClientToken` estable.
- **`bucket-01`**: Bucket S3 en `us-east-1`.

Ambos recursos reciben las etiquetas obligatorias `ManagedBy=ppa2`, `Project=fenix`, `GroupId=<grupo>`, `Owner=<grupo>`. Persiste el inventario en `data/inventory.json` mediante reemplazo atómico.

### Inyectar una falla controlada

```bash
python -m fenix.cli fault node-01     # Detiene la instancia EC2
python -m fenix.cli fault bucket-01   # Elimina la etiqueta Owner del bucket
```

### Enviar un evento firmado al receptor

```bash
# Primero, iniciar el receptor en otra terminal:
uvicorn fenix.services.receiver:app --host 127.0.0.1 --port 8000

# Enviar el evento:
python -m fenix.cli send examples/node-event.json
python -m fenix.cli send examples/bucket-event.json
```

El comando firma el JSON con HMAC-SHA256 (`WEBHOOK_SECRET`) y lo envía como `POST /webhook`. El receptor valida la firma, verifica el contrato del evento contra el inventario, y encola en SQS respondiendo `202 Accepted`.

### Ejecutar el worker

```bash
python -m fenix.cli worker           # Bucle continuo con long polling
python -m fenix.cli worker --once    # Una sola consulta (útil para pruebas)
```

El worker consume mensajes de SQS, ejecuta la remediación (arrancar instancia o restaurar tags), notifica el resultado y hace ACK. Los logs se escriben en `logs/fenix-worker-<PID>.jsonl`.

### Monitoreo de logs en tiempo real

```powershell
# PowerShell
Get-Content logs\fenix-worker-*.jsonl -Wait -Tail 10

# Linux/macOS
tail -f logs/fenix-worker-*.jsonl
```

---

## Eventos de Ejemplo

### `examples/node-event.json`

```json
{
  "schema_version": 1,
  "event_id": "evt-node-001",
  "event_type": "instance.stopped",
  "resource_id": "node-01",
  "occurred_at": "2026-10-22T18:00:00Z",
  "simulate_failures": 0
}
```

### `examples/bucket-event.json`

```json
{
  "schema_version": 1,
  "event_id": "evt-bucket-001",
  "event_type": "bucket.tags_missing",
  "resource_id": "bucket-01",
  "occurred_at": "2026-10-22T18:00:00Z",
  "simulate_failures": 0
}
```

> El campo `simulate_failures` (0–5) permite forzar fallas de laboratorio para probar el mecanismo de reintentos.

---

## Ejecución de Tests

```bash
# Tests del apoyo ya implementado (no requieren LocalStack)
python -m pytest -m base -q

# Tests de contratos completados por el equipo
python -m pytest -m pendiente -q

# Tests del integrante 1 (validación y eventos)
python -m pytest -m i1 -q

# Tests de integración (requieren LocalStack corriendo)
python -m pytest -m integracion -q

# Todos los tests
python -m pytest -q
```

---

## Estructura del Proyecto

```
tp-final/
├── compose.yaml                    # Docker Compose para LocalStack
├── pyproject.toml                  # Configuración del paquete Python
├── requirements.txt                # Dependencias
├── .env                            # Variables de entorno (ignorado por Git)
├── examples/
│   ├── node-event.json             # Evento de ejemplo: instancia detenida
│   └── bucket-event.json           # Evento de ejemplo: tags faltantes
├── data/
│   ├── inventory.json              # Inventario de recursos (generado por setup-demo)
│   └── events.json                 # Ledger durable de eventos procesados
├── logs/                           # Logs JSONL rotativos por proceso
├── src/fenix/
│   ├── cli.py                      # Punto de entrada CLI (argparse)
│   ├── config/
│   │   └── settings.py             # Fábrica de clientes Boto3 → localhost:4566
│   ├── services/
│   │   ├── cloud_support.py        # Recetas Boto3 (create_bucket, create_instance, etc.)
│   │   ├── demo.py                 # Provisión de infraestructura mock e inventario
│   │   ├── events.py               # Validación de esquema cerrado y fingerprint
│   │   ├── notifications.py        # Notificaciones (Telegram / console)
│   │   ├── receiver.py             # Receptor FastAPI (webhook firmado → SQS)
│   │   ├── remediation.py          # Reparación convergente + ledger durable
│   │   ├── sqs.py                  # Mensajería SQS (colas, publish, receive, retry)
│   │   └── worker.py               # Bucle de consumo: receive → handler → ACK/retry
│   └── utils/
│       ├── crypto.py               # HMAC-SHA256 (firma y verificación)
│       ├── errors.py               # Jerarquía de excepciones del proyecto
│       ├── logging_json.py         # Logger JSON asíncrono con rotación gzip
│       ├── state.py                # Persistencia atómica (load/save con os.replace)
│       └── validation.py           # Validadores puros (identifier, ttl, utc)
├── tests/
│   ├── test_base.py                # Tests del apoyo entregado
│   ├── test_contracts.py           # Contratos de validación
│   ├── test_fenix.py               # Tests generales
│   ├── test_i1_events.py           # Tests de eventos (I1)
│   ├── test_i1_validation.py       # Tests de validación (I1)
│   ├── test_integracion.py         # Tests de integración con LocalStack
│   ├── test_logging_f16.py         # Tests de logging y rotación
│   ├── test_notifications.py       # Tests de notificaciones
│   ├── test_remediation.py         # Tests de remediación
│   ├── test_scenarios.py           # Escenarios de integración (I6)
│   ├── test_webhook.py             # Tests del receptor HTTP
│   └── test_worker.py              # Tests del worker
├── docs/
│   └── I1_entradas_y_excepciones.md
├── scripts/
│   └── evidencia_i1.py
├── Evidencias_i2/                  # Capturas de evidencia del integrante 2
└── Evidencias_i4/                  # Capturas de evidencia del integrante 4
```

---

## Módulos por Integrante

### Integrante 2: Mensajería SQS e inventario cloud

El módulo `fenix.services.sqs` gestiona las colas estándar SQS contra LocalStack: `ensure_queues` crea la DLQ con retención de 14 días, la cola principal con `VisibilityTimeout=45` y retención de 4 días, y vincula la `RedrivePolicy` (`maxReceiveCount=3`) mediante `set_queue_attributes` para garantizar idempotencia. `retry_later` aplica backoff exponencial (`min(30, 2^count)`) exclusivamente con `change_message_visibility` sobre el `ReceiptHandle` vigente, sin re-publicar el mensaje.

El módulo `fenix.services.demo` provisiona la infraestructura mock: descubre VPC e imagen del emulador, crea o reutiliza Security Group, instancia EC2 (con `ClientToken` estable) y bucket S3, aplica las cuatro etiquetas obligatorias (`ManagedBy=ppa2`, `Project=fenix`, `GroupId`, `Owner`) y persiste atómicamente el inventario en `data/inventory.json`. Tres funciones auxiliares privadas (`_find_instance_by_tags`, `_find_sg_by_name`, `_bucket_exists`) garantizan idempotencia sin duplicar recursos.

### Integrante 4: Logging estructurado

El módulo `fenix.utils.logging_json` configura un logger JSON asíncrono para cada proceso. Escribe archivos JSONL en `logs/`, incluye marca de tiempo UTC y campos de contexto permitidos, y rota los archivos al alcanzar 2 MiB. Conserva hasta tres históricos comprimidos en gzip. La cola en memoria desacopla la escritura en disco; no reemplaza a SQS.

La CLI inicializa un logger por comando y detiene su listener al salir. Para probar la rotación y comprobar que el histórico gzip contiene líneas JSON válidas:

```bash
python -m pytest tests/test_logging_f16.py -q
```

---

## Matriz de Pruebas y Evidencias de Validación

La siguiente tabla documenta las pruebas de validación ejecutadas contra LocalStack y sus correspondientes evidencias.

| #         | Caso de Prueba                                           | Comandos Ejecutados                                                                                                                                                                              | Descripción Técnica                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | Evidencia                                                                                                    |
| --------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| **F01**   | **Idempotencia en la Inicialización e Inventario**       | Ejecución consecutiva repetida de `python -m fenix.cli init-queues` y `python -m fenix.cli setup-demo`                                                                                           | Se validó que el sistema cumple con la propiedad de idempotencia. Ante múltiples ejecuciones consecutivas, el script no duplica infraestructura ni sobrescribe identidades: conserva exactas las mismas URLs de colas SQS (`fenix-grupo01-events` y `fenix-grupo01-dlq`), el mismo `InstanceId` en EC2 (`i-2805dc15ac3d81245`), el mismo `SecurityGroupId` (`sg-2bce32e9525845b58`) y el mismo bucket S3 (`fenix-grupo01-demo`). Además, genera consistentemente el manifiesto de inventario en `data/inventory.json`.                                                    | [`evidencias-F01idempotencia_init_setup.png`](Evidencias_i2/evidencias-F01idempotencia_init_setup.png)       |
| **F10-A** | **Reintentos con Backoff Exponencial en el Worker**      | Worker corriendo con `python -u -m fenix.cli worker` e inyección de evento con `simulate_failures: 5` y `event_id` nuevo (`evt-node-003`). Monitoreo con `Get-Content logs\fenix-worker-*.jsonl` | Se comprobó que el worker detecta eventos no procesados y aplica una estrategia de backoff exponencial ante fallas persistentes simuladas. En los logs estructurados JSONL se evidencian las tres instancias incrementales de postergación de visibilidad (`"reintento_programado en 2 s"`, `"reintento_programado en 4 s"`, `"reintento_programado en 8 s"`) asociadas a los contadores `attempt: 1`, `attempt: 2` y `attempt: 3`. Tras alcanzar la falla simulada 3 de 5, el worker detiene los reintentos en la cola principal registrando el estado `"sin_mensajes"`. | [`evidencias-F10_worker_reintentos_backoff.png`](Evidencias_i2/evidencias-F10_worker_reintentos_backoff.png) |
| **F10-B** | **Aislamiento y Consulta en la Dead Letter Queue (DLQ)** | Consulta mediante Boto3 a la DLQ: `sqs.receive_message(QueueUrl='.../fenix-grupo01-dlq')`                                                                                                        | Se verificó que SQS aisló automáticamente el mensaje en la DLQ (`fenix-grupo01-dlq`) después de superar la cantidad máxima de intentos (`maxReceiveCount = 3`) en la cola principal. La lectura demuestra que el payload original se conserva íntegro para posterior análisis/replay (con `ApproximateReceiveCount >= 3`) sin haber sido eliminado de forma destructiva por el worker.                                                                                                                                                                                    | [`evidencias-F10_lectura_dlq_aws_cli.png`](Evidencias_i2/evidencias-F10_lectura_dlq_aws_cli.png)             |

---

## Restricciones Técnicas del TP

| Restricción                                     | Implementación                                                                          |
| ----------------------------------------------- | --------------------------------------------------------------------------------------- |
| Sin `except: pass` ni `BaseException`           | Solo capturas específicas de `ClientError`, `BotoCoreError`, `ProjectError` y subclases |
| Sin `return`/`break`/`continue` en `finally`    | Verificado en todos los módulos                                                         |
| Endpoint exclusivo `localhost:4566`             | Validado en `config/settings.py` con `urlparse`                                         |
| Sin credenciales reales de AWS                  | Variables ficticias (`test`/`test`) para LocalStack                                     |
| Type hints en parámetros y retornos             | Todas las funciones públicas anotadas                                                   |
| Docstrings con Args/Returns/Raises/Side Effects | Formato consistente en todo el proyecto                                                 |
| Persistencia atómica                            | `state.save` usa `os.replace` en el mismo filesystem                                    |
| Nunca `send_message` en `retry_later`           | Solo `change_message_visibility` para no reiniciar `ApproximateReceiveCount`            |
| ACK solo tras éxito durable                     | `acknowledge` se llama únicamente después de que el handler persista todas las etapas   |
