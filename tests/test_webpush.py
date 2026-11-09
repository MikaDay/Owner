import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

import webpush
from webpush import b64u_decode


def test_rfc8291_appendix_a_vector():
    sender = ec.derive_private_key(int.from_bytes(b64u_decode("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"),
                                   ec.SECP256R1())
    body = webpush.encrypt(b"When I grow up, I want to be a watermelon",
                           "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                           "BTBZMqHH6r4Tts7J_aSIgg", _sender_key=sender, _salt=b64u_decode("DGv6ra1nlYgDCS1FRnbzlw"))
    assert webpush.b64u(body) == ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocIn"
                                  "mYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQe"
                                  "xSgSxsj_Qulcy4a-fN")


def test_vapid_token_is_signed_by_our_key():
    private, public = webpush.generate_vapid_keys()
    header = webpush.vapid_authorization("https://web.push.apple.com/abc", private, public, "mailto:x@y.z")
    token = header.split("t=")[1].split(",")[0]
    h, c, sig = token.split(".")
    claims = json.loads(b64u_decode(c))
    assert claims["aud"] == "https://web.push.apple.com" and claims["sub"] == "mailto:x@y.z"
    raw = b64u_decode(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64u_decode(public))
    key.verify(der, f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))      # aruncă excepție dacă e greșit
    assert header.endswith(f"k={public}")
