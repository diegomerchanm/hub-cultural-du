"""
wsgi.py — Hub Cultural DU
Punto de entrada para servidores WSGI de producción (gunicorn, usado por
Render u otro hosting). No se puede hacer `from 5_visualize_dashboard
import app` directamente porque los nombres de módulo en Python no pueden
empezar con un dígito — se usa importlib para sortear esa limitación sin
renombrar el script original (que además siguen usando localmente con
`python 5_visualize_dashboard.py`).

Uso en producción:
    gunicorn wsgi:server
"""
import importlib

_dashboard = importlib.import_module("5_visualize_dashboard")

# Dash expone el Flask app subyacente en `app.server` — eso es lo que
# entiende un servidor WSGI como gunicorn.
server = _dashboard.app.server
