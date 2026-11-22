"""HTTPS for devices on the local network.

When "Allow devices on my network" is on, the host opens a second listener for other devices, using
HTTPS with a certificate generated on this computer. No certificate authority vouches for it, so
devices check its SHA-256 fingerprint instead: the phone app pins it at pairing (scanned from the QR
code, or compared on screen), and browsers show their "not private" warning once.
The computer's own browser keeps using plain HTTP on 127.0.0.1, which never leaves the machine.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import os
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

CERT_NAME, KEY_NAME = "lan-cert.pem", "lan-key.pem"
VALID_DAYS = 3650
RENEW_BEFORE_DAYS = 30


def fingerprint(cert_pem: bytes) -> str:
    cert = x509.load_pem_x509_certificate(cert_pem)
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def short_fingerprint(fp: str) -> str:
    """What people compare by eye: the first 8 hex digits, as two groups."""
    return f"{fp[:4]} {fp[4:8]}".upper()


def _sans(names: set[str]) -> list[x509.GeneralName]:
    out: list[x509.GeneralName] = [x509.DNSName("localhost")]
    for n in sorted(names):
        n = n.strip("[]")
        try:
            out.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            if n and all(part.isalnum() or "-" in part for part in n.split(".")) and n != "localhost":
                out.append(x509.DNSName(n))
    return out


def ensure_certificate(folder: Path, names: set[str] | None = None) -> tuple[Path, Path, str]:
    """Return (certfile, keyfile, sha256 fingerprint), creating or renewing the certificate if needed.
    The certificate is reused across restarts and IP changes, so paired devices stay paired."""
    folder.mkdir(parents=True, exist_ok=True)
    cert_p, key_p = folder / CERT_NAME, folder / KEY_NAME
    if cert_p.exists() and key_p.exists():
        try:
            pem = cert_p.read_bytes()
            cert = x509.load_pem_x509_certificate(pem)
            if cert.not_valid_after_utc - dt.datetime.now(dt.UTC) > dt.timedelta(days=RENEW_BEFORE_DAYS):
                return cert_p, key_p, fingerprint(pem)
        except (ValueError, OSError):
            pass  # unreadable: make a new one
    key = ec.generate_private_key(ec.SECP256R1())
    host = socket.gethostname() or "this computer"
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Open Market OS on {host}"[:64]),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Open Market OS (self-signed)")])
    now = dt.datetime.now(dt.UTC)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=VALID_DAYS))
            .add_extension(x509.SubjectAlternativeName(_sans(names or set())), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    key_bytes = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption())
    tmp = key_p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key_bytes)
    os.replace(tmp, key_p)
    pem = cert.public_bytes(serialization.Encoding.PEM)
    cert_p.write_bytes(pem)
    return cert_p, key_p, fingerprint(pem)


def lan_addresses() -> list[str]:
    """IPv4 addresses other devices can use (no loopback or link-local), most likely first."""
    import psutil

    found: list[str] = []
    try:
        for name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family != socket.AF_INET:
                    continue
                ip = ipaddress.ip_address(a.address)
                if ip.is_loopback or ip.is_link_local:
                    continue
                found.append(a.address)
    except OSError:
        pass
    return sorted(set(found), key=lambda x: (not ipaddress.ip_address(x).is_private, x))
