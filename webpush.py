"""Notificări Web Push (RFC 8030), criptate cu aes128gcm (RFC 8291) și semnate VAPID (RFC 8292).

Scrise direct cu `cryptography`, fără alte biblioteci: e tot ce trebuie ca un mesaj să ajungă
pe iPhone (iOS 16.4+, aplicația adăugată pe ecranul principal), Android sau calculator.
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RECORD_SIZE = 4096


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_decode(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _raw_public(key):
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def generate_vapid_keys():
    """(cheie_privată, cheie_publică) în base64url, pentru variabilele VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY."""
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    return b64u(private), b64u(_raw_public(key.public_key()))


def _private_key(private_b64):
    return ec.derive_private_key(int.from_bytes(b64u_decode(private_b64), "big"), ec.SECP256R1())


def _hkdf(salt, info, length, ikm):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt(payload, p256dh, auth, _sender_key=None, _salt=None):
    """Corpul cererii push: antet (sare, mărime înregistrare, cheia publică a serverului) + text criptat."""
    ua_public = b64u_decode(p256dh)
    auth_secret = b64u_decode(auth)
    sender = _sender_key or ec.generate_private_key(ec.SECP256R1())
    sender_public = _raw_public(sender.public_key())
    shared = sender.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = _hkdf(auth_secret, b"WebPush: info\x00" + ua_public + sender_public, 32, shared)
    salt = _salt or os.urandom(16)
    cek = _hkdf(salt, b"Content-Encoding: aes128gcm\x00", 16, ikm)
    nonce = _hkdf(salt, b"Content-Encoding: nonce\x00", 12, ikm)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)     # o singură înregistrare, ultima
    return salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(sender_public)]) + sender_public + ciphertext


def vapid_authorization(endpoint, private_b64, public_b64, subject):
    parts = urlsplit(endpoint)
    header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    claims = b64u(json.dumps({"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(time.time()) + 12 * 3600,
                              "sub": subject}).encode())
    signing_input = f"{header}.{claims}".encode()
    r, s = decode_dss_signature(_private_key(private_b64).sign(signing_input, ec.ECDSA(hashes.SHA256())))
    token = f"{header}.{claims}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return f"vapid t={token}, k={public_b64}"


def send(subscription, message, private_b64, public_b64, subject="mailto:noreply@cirforvest.local", ttl=24 * 3600):
    """Trimite `message` (dict, ajunge la service worker ca JSON). Întoarce codul HTTP:
    201 = trimis; 404/410 = abonamentul nu mai există (telefonul a renunțat) și trebuie șters."""
    body = encrypt(json.dumps(message, ensure_ascii=False).encode(), subscription["keys"]["p256dh"],
                   subscription["keys"]["auth"])
    req = urllib.request.Request(subscription["endpoint"], data=body, method="POST", headers={
        "TTL": str(ttl), "Urgency": "normal", "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "Authorization": vapid_authorization(subscription["endpoint"], private_b64, public_b64, subject),
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
