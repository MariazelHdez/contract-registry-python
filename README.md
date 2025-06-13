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
