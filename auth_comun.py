"""
auth_comun.py — Autenticación por sesión (cookie firmada) contra Supabase
=========================================================================
Login de usuarios para todo el hub. Los usuarios se guardan en Supabase
(tabla 'usuarios': username, password_hash, activo) y se crean directamente
en la BD con el script crear_usuario.py.

- La cookie de sesión va firmada (itsdangerous) con AUTH_SECRET, así el
  middleware del hub puede validarla sin estado en servidor.
- La contraseña se guarda con hash (werkzeug.security).

Variables de entorno:
    AUTH_SECRET            secreto para firmar la cookie (¡ponlo en producción!)
    SUPABASE_URL / SUPABASE_KEY
    SUPABASE_USUARIOS_TABLE  (opcional, por defecto 'usuarios')
    AUTH_MAX_AGE           (opcional) segundos de validez de la sesión (def. 7 días)
"""
from __future__ import annotations

import os

import requests
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash

COOKIE = "sesion_hub"
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
TABLA = os.environ.get("SUPABASE_USUARIOS_TABLE", "usuarios").strip() or "usuarios"
MAX_AGE = int(os.environ.get("AUTH_MAX_AGE", str(7 * 24 * 3600)))


def _secret() -> str:
    # AUTH_SECRET preferente; si no, cae a algo estable ya presente (no ideal,
    # pero evita romper en local). En producción define AUTH_SECRET.
    return (os.environ.get("AUTH_SECRET")
            or os.environ.get("APP_PASSWORD")
            or os.environ.get("SUPABASE_KEY")
            or "cambia-esto-en-produccion")


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret(), salt="sesion-hub")


def activo() -> bool:
    """True si hay respaldo de usuarios (Supabase configurado)."""
    return bool(SUPABASE_URL and SUPABASE_KEY)


def _headers() -> dict:
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


def crear_cookie(username: str) -> str:
    return _serializer().dumps({"u": username})


def validar_cookie(token: str | None) -> str | None:
    """Devuelve el username si la cookie es válida y no ha caducado; si no, None."""
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=MAX_AGE)
        return data.get("u")
    except (BadSignature, SignatureExpired, Exception):  # noqa: BLE001
        return None


def verificar_credenciales(username: str, password: str) -> bool:
    """Comprueba usuario+contraseña contra la tabla de Supabase."""
    if not activo() or not username or not password:
        return False
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{TABLA}",
            params={"select": "password_hash,activo", "username": f"eq.{username}", "limit": "1"},
            headers=_headers(), timeout=8,
        )
        if r.status_code >= 300:
            return False
        filas = r.json()
        if not filas:
            return False
        u = filas[0]
        if not u.get("activo", True):
            return False
        return check_password_hash(u.get("password_hash") or "", password)
    except Exception:  # noqa: BLE001
        return False
