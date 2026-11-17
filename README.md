# Plantillas del proyecto Fenix

Leer Guia.md o la consigna PDF antes de ejecutar. Este es un punto de partida didáctico: los TODO y NotImplementedError son tareas del equipo, no una aplicación terminada.

## Inicio

Crear y activar un entorno virtual Python 3.11 o superior. Desde esta carpeta ejecutar `python -m pip install -e .`, copiar `.env.example` a `.env`, completar variables y usar la instalación LocalStack validada por la cátedra. El compose de referencia usa EC2 mock, sin VM real. Python corre en el host.

`python -m pytest -m base -q` verifica el apoyo ya implementado. `python -m pytest -m pendiente -q` falla inicialmente hasta completar contratos. No confundir esas marcas con aprobación del TP.

La guía contiene los comandos exactos, matriz de pruebas, roles y requisitos del README definitivo que debe escribir el equipo. Reemplazar este README inicial por su documentación completa.

## Integrante 4: logging estructurado

El módulo `fenix.utils.logging_json` configura un logger JSON asíncrono para cada proceso. Escribe archivos JSONL en `logs/`, incluye marca de tiempo UTC y campos de contexto permitidos, y rota los archivos al alcanzar 2 MiB. Conserva hasta tres históricos comprimidos en gzip. La cola en memoria desacopla la escritura en disco; no reemplaza a SQS.

La CLI inicializa un logger por comando y detiene su listener al salir. Para probar la rotación y comprobar que el histórico gzip contiene líneas JSON válidas:

```bash
PYTHONPATH=src python3 -m pytest tests/test_logging_f16.py -q
```
