# Integrante 1: Entradas y Excepciones

Módulo que decide si un dato externo es aceptable **antes** de que algo se publique en SQS o se toque un recurso.

| Archivo | Contenido |
| --- | --- |
| `src/fenix/utils/validation.py` | `identifier`, `ttl_seconds`, `utc_text` |
| `src/fenix/services/events.py` | `validate` (contrato del evento), `fingerprint` (huella para detectar conflictos) |
| `src/fenix/utils/errors.py` | Revisión: cuándo se usa cada excepción (tabla en el docstring del módulo) |
| `tests/test_i1_validation.py`, `tests/test_i1_events.py` | 182 pruebas, 100 % de cobertura de líneas y ramas |
| `scripts/evidencia_i1.py` | Evidencia: un evento correcto y tres rechazos sin publicación |

## Contrato de entrada

Entradas: un evento (valor ya decodificado de JSON) y el inventario (`alias -> recurso`).
Salida: una copia validada del evento o `ValidationError`.

| Campo | Regla |
| --- | --- |
| (evento) | Debe ser un objeto JSON. Claves exactas: ni faltantes ni extra |
| `schema_version` | El entero `1` (`type(x) is int`; `True` se rechaza) |
| `event_id`, `resource_id` | `[a-z0-9-]{3,32}`, sin normalizar |
| `event_type` | `instance.stopped` o `bucket.tags_missing` |
| `resource_id` | Debe ser una clave del inventario y su `type` debe ser compatible (`ec2` con `instance.stopped`, `s3` con `bucket.tags_missing`) |
| `occurred_at` | ISO 8601 extendido terminado en `Z` o `+00:00` |
| `simulate_failures` | Entero de 0 a 5; `bool` se rechaza |

## Decisiones de diseño

- **`fullmatch`, no `match`:** con `$`, `"abc\n"` pasaría el patrón.
- **No se normaliza** a minúsculas: `AJENO` y `ajeno` son identidades distintas.
- **`type(x) is int`, no `isinstance`:** en Python `bool` hereda de `int` y `True == 1`.
- **`ttl_seconds` solo acepta dígitos ASCII:** `int()` tolera `" 60 "`, `"+60"`, `"6_0"` y dígitos Unicode.
- **`utc_text` fija la forma antes de interpretar:** `fromisoformat` acepta variantes ambiguas (espacio en lugar de `T`, formato básico). Un offset como `-03:00` es válido pero no UTC, así que se rechaza.
- **`event_type` se comprueba con `isinstance(str)` antes del `in`:** una lista o dict como valor lanzaría `TypeError` (no hashable) en lugar de `ValidationError`.
- **Un `InstanceId` pasa `identifier` pero no el inventario:** `i-0123456789abcdef0` cumple el patrón; lo frena que no es una clave del inventario.
- **Los mensajes no copian valores del remitente** (ni nombres de claves extra): podrían contener secretos.
- **Falla cerrada:** sin inventario utilizable no se autoriza ningún recurso.
- **`fingerprint`:** SHA-256 del JSON canónico. Lo puede usar I5 para el conflicto F15 (mismo `event_id`, distinto contenido).

## Dónde se usa `validate`

1. Receptor HTTP (I3): `ValidationError` se traduce a **422**.
2. `remediation.handle` (I5): el worker valida de nuevo; un fallo deja el mensaje sin ACK y la `RedrivePolicy` lo envía a la DLQ.

## Ejecución

```bash
python -m pytest -m i1 -q                    # pruebas de I1
python -m pytest -m i1 -q --cov=fenix.utils.validation --cov=fenix.services.events --cov-branch --cov-report=term-missing
python -m pytest tests/test_contracts.py -q  # contratos originales de la plantilla
python scripts/evidencia_i1.py               # evidencia individual
```

## Fila para la matriz de pruebas del README

| ID | Fecha | Comando | Entrada anonimizada | Esperado | Obtenido | Evidencia | Responsable |
| --- | --- | --- | --- | --- | --- | --- | --- |
| F07 (parte validación) | _completar_ | `python -m pytest -m i1 -q` y `python scripts/evidencia_i1.py` | alias ajeno `node-99`; clave extra; `simulate_failures=true` | Rechazo de esquema; ningún cambio cloud ni publicación | 182 pruebas aprobadas; 3 rechazos, 1 publicación (la del evento correcto) | salida del script | I1 |

La parte HTTP de F07 (422 y ningún mensaje nuevo en SQS) se registra cuando I3 complete el receptor.

## Preparación de la defensa

- **¿Por qué una lista JSON no es un evento válido?** Es JSON sintácticamente válido, pero el contrato exige un objeto con campos con nombre. Por eso es 422 y no 400, que corresponde a JSON roto.
- **¿Por qué validar otra vez en el worker?** La validación HTTP solo protege esa puerta. Un mensaje puede inyectarse directo en SQS (F08), o ser antiguo, o el inventario puede haber cambiado.
- **¿Por qué `fullmatch`?** Con `match` y `$`, un salto de línea final pasa el patrón.
- **¿Por qué `type(x) is int`?** `True == 1` y `isinstance(True, int)` es verdadero.
- **¿Por qué `ValidationError` y no `ValueError`?** Hereda de `ProjectError`: la CLI la captura y cierra con código 1, y el receptor la traduce a 422 sin confundirla con un error de programación.
- **Cambios breves que pueden pedirte en vivo:** agregar un `event_type` (una línea en `EVENT_RESOURCE_TYPES`), cambiar el tope de `simulate_failures` (`MAX_SIMULATED_FAILURES`), aceptar otros offsets convirtiéndolos a UTC (quitar el chequeo de offset; `astimezone` ya normaliza).

## Declaración de ayuda externa e IA (completar con datos reales)

- **Qué se utilizó:** _herramienta y para qué_.
- **Qué se modificó:** _qué cambié o descarté yo_.
- **Cómo se verificó:** `pytest -m i1` (182 pruebas), cobertura de líneas y ramas del 100 %, y una prueba de mutación manual (18 reglas rotas a propósito, todas detectadas).
