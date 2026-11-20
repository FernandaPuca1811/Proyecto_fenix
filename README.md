# 🔥 Proyecto Fénix — Sistema de Autorreparación de Infraestructura

**TP Final — Programación para Automatización II (PPA2)**

Link de defensa en YouTube: https://www.youtube.com/watch?v=ojaDcjFT7wA

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

## Distribución de roles

La consigna distribuye el trabajo entre seis integrantes; no existe un rol 7.
Esta sección resume los roles que forman parte de esta documentación.

| Rol | Responsabilidad principal |
| --- | --- |
| Integrante 1 - Entradas y Excepciones | Validar los eventos antes de enviarlos a SQS, rechazar esquemas, identificadores y recursos inválidos, y proporcionar errores controlados. |
| Integrante 3 - Webhooks y Reparación | Autenticar el webhook con HMAC, publicar los eventos confirmados y ejecutar las reparaciones convergentes de EC2 y S3. |
| Integrante 4 - Observabilidad y Comunicación | Revisar el logging JSON, completar el adaptador de notificaciones y correlacionar los pasos relevantes con trazas y evidencias. |
| Integrante 5 - Integración y Worker | Implementar el consumo de SQS y el procesamiento por etapas, manteniendo el estado local y evitando reparaciones o avisos repetidos. |

### Alcance resumido

- **Integrante 1:** valida entradas y recursos, usa mensajes de error seguros y registra una evidencia individual sin publicar datos inválidos.
- **Integrante 3:** verifica firma y antigüedad del timestamp, responde con los códigos HTTP definidos y demuestra las reparaciones mediante API.
- **Integrante 4:** produce logs JSON legibles, registra un histórico gzip y administra el canal de avisos sin ocultar errores controlados.
- **Integrante 5:** coordina receptor, cola, persistencia, reparación y ACK; también debe reanudar una ejecución incompleta sin repetir efectos confirmados.

## Integrante 4: logging estructurado

El módulo `fenix.utils.logging_json` configura un logger JSON asíncrono para cada proceso. Escribe archivos JSONL en `logs/`, incluye marca de tiempo UTC y campos de contexto permitidos, y rota los archivos al alcanzar 2 MiB. Conserva hasta tres históricos comprimidos en gzip. La cola en memoria desacopla la escritura en disco; no reemplaza a SQS.

La CLI inicializa un logger por comando y detiene su listener al salir. Para probar la rotación y comprobar que el histórico gzip contiene líneas JSON válidas:

```bash
python -m pytest tests/test_logging_f16.py -q
```

El adaptador de Telegram trata timeout, conexión, HTTP 429 y 5xx como transitorios.
Si la respuesta incluye `Retry-After`, espera ese tiempo hasta un máximo de 30 segundos;
después propaga el error para que el worker/SQS administre el siguiente intento. Un
`Retry-After` inválido se ignora y el error transitorio se conserva.

### Evidencias y matriz del Integrante 4

| ID | Fecha | Comando o procedimiento | Resultado |
| --- | --- | --- | --- |
| F14 | 12-11-2026 | Reintentar el aviso 429 y luego publicar la reparación exitosa; repetir el evento | El aviso fallido no vuelve a ejecutar la reparación y el reintento termina con un único aviso confirmado. Cobertura: `tests/test_remediation.py::test_f14_notice_429_then_success_without_new_repair`. |
| F16 | 20-11-2026 | `PYTHONPATH=src python3 -m pytest tests/test_logging_f16.py tests/test_notifications.py tests/test_base.py -q` | 26 pruebas aprobadas. Se verifican rotación gzip, JSONL válido, contexto permitido, reintentos Telegram y configuración controlada. |

La configuración real de Telegram deja el proveedor en `console` mientras no existan
`TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`. Con Telegram configurado, el texto recibido
por el canal confirma el resultado de la reparación y se limita a 1000 caracteres.

Captura real de comunicación: [Alerta Telegram — 12-11-2026](Evidencias_i4/Captura%20de%20pantalla%202026-11-12%20a%20la(s)%206.28.27%E2%80%AFp.%E2%80%AFm..png).
La captura muestra el aviso enviado por el bot Tita al chat después de restaurar
`node-01` al estado `running`.

Esta evidencia puede mostrarse junto con el video de defensa y la salida de las
pruebas F14/F16.
