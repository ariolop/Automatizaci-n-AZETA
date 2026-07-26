@echo off
REM Arranca el escáner de códigos de barras (AZETA + Liderpapel).
REM La primera vez instala dependencias y genera el certificado HTTPS.
cd /d "%~dp0"

where python >nul 2>nul || (echo No se encontro Python en el PATH. & pause & exit /b 1)

if not exist cert.pem (
  echo Instalando dependencias...
  python -m pip install -r requirements.txt
  echo Generando certificado HTTPS autofirmado...
  python gen_cert.py
)

echo.
echo ============================================================
echo   Buscando la IP de este PC para abrir en el movil...
echo ============================================================
ipconfig | findstr /i "IPv4"
echo.
echo   Abre en el movil (misma WiFi):  https://LA-IP-DE-ARRIBA:5002
echo   (acepta el aviso de "sitio no seguro" la primera vez)
echo ============================================================
echo.

python scanner_app.py
pause
