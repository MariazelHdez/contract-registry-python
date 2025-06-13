# contract-registry-python

# Libraries
pip install pyppeteer
pip install pyppeteer-stealth

## Uso del script `d2.py`

Para ejecutar la descarga de contratos sin abrir el navegador puede utilizarse
el modo *headless*:

```bash
python d2.py --headless
```

Si se omite la opción `--headless`, el navegador se abrirá de manera normal.
Desde la versión más reciente, el script incluye parámetros que evitan que
Chrome se pause al quedar en segundo plano. Para evitar interferencias con otras
tareas se recomienda utilizar el modo *headless* siempre que sea posible.

## Extraer detalles de contratos con `contract_details.py`

El script `contract_details.py` permite obtener la información detallada de
cada contrato. También puede ejecutarse en modo *headless* para que la
automatización corra en segundo plano sin abrir una ventana de Chrome:

```bash
python contract_details.py --headless
```

Si se omite la opción `--headless` se abrirá el navegador de manera normal.
