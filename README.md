# Plantillas del proyecto Fenix

Leer Guia.md o la consigna PDF antes de ejecutar. Este es un punto de partida didáctico: los TODO y NotImplementedError son tareas del equipo, no una aplicación terminada.

## Inicio

Crear y activar un entorno virtual Python 3.11 o superior. Desde esta carpeta ejecutar `python -m pip install -e .`, copiar `.env.example` a `.env`, completar variables y usar la instalación LocalStack validada por la cátedra. El compose de referencia usa EC2 mock, sin VM real. Python corre en el host.

`python -m pytest -m base -q` verifica el apoyo ya implementado. `python -m pytest -m pendiente -q` falla inicialmente hasta completar contratos. No confundir esas marcas con aprobación del TP.

La guía contiene los comandos exactos, matriz de pruebas, roles y requisitos del README definitivo que debe escribir el equipo. Reemplazar este README inicial por su documentación completa.
