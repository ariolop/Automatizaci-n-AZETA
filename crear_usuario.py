"""
crear_usuario.py — Alta/actualización de un usuario del hub en Supabase
=======================================================================
Guarda el usuario con la contraseña HASHEADA (nunca en claro).

    python crear_usuario.py <usuario> <contraseña>
    python crear_usuario.py alejandro "MiClaveSegura"

Lee SUPABASE_URL / SUPABASE_KEY de los .env (raíz / scanner / cspapeleria).
Antes ejecuta 'supabase_usuarios_schema.sql' en Supabase.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parent


def _cargar_env(ruta: Path) -> None:
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, v = linea.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def main():
    if len(sys.argv) != 3:
        print("Uso: python crear_usuario.py <usuario> <contraseña>")
        sys.exit(1)
    username, password = sys.argv[1], sys.argv[2]

    for f in (".env", "scanner/.env", "cspapeleria/.env"):
        _cargar_env(ROOT / f)
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_KEY", "").strip()
    tabla = os.environ.get("SUPABASE_USUARIOS_TABLE", "usuarios")
    if not url or not key:
        print("ERROR: faltan SUPABASE_URL / SUPABASE_KEY en los .env.")
        sys.exit(1)

    fila = {"username": username, "password_hash": generate_password_hash(password), "activo": True}
    r = requests.post(
        f"{url}/rest/v1/{tabla}",
        params={"on_conflict": "username"},
        json=fila,
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"},
        timeout=15,
    )
    if r.status_code < 300:
        print(f"✓ Usuario '{username}' creado/actualizado.")
    else:
        print(f"ERROR {r.status_code}: {r.text[:300]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
