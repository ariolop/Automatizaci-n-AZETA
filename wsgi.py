"""Punto de entrada WSGI para gunicorn (Render).

    gunicorn wsgi:application --bind 0.0.0.0:$PORT
"""
from hub import application  # noqa: F401
