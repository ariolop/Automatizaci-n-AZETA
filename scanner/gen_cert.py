"""
gen_cert.py — Genera un certificado autofirmado (cert.pem + key.pem) para servir
la app por HTTPS. Necesario porque la cámara del navegador solo funciona sobre
HTTPS o localhost. Se ejecuta solo una vez; el navegador del móvil pedirá aceptar
el aviso de "sitio no seguro" la primera vez (es normal en certificados propios).

Uso directo:  python gen_cert.py
"""
from __future__ import annotations

import datetime
import ipaddress
import socket
from pathlib import Path


def _ips_locales() -> list[str]:
    ips = {"127.0.0.1"}
    try:
        ips.add(socket.gethostbyname(socket.gethostname()))
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(ips)


def generar(cert_path: Path, key_path: Path) -> tuple[Path, Path]:
    """Crea cert.pem y key.pem autofirmados (válidos 10 años) usando cryptography."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "scanner-local")])

    san = [x509.DNSName("localhost")]
    for ip in _ips_locales():
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass

    ahora = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - datetime.timedelta(days=1))
        .not_valid_after(ahora + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


if __name__ == "__main__":
    base = Path(__file__).resolve().parent
    c, k = generar(base / "cert.pem", base / "key.pem")
    print("Certificado generado:")
    print(" ", c)
    print(" ", k)
    print("IPs locales incluidas:", ", ".join(_ips_locales()))
