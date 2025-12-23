from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
import base64


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


# Generate EC key (P-256)
private_key = ec.generate_private_key(ec.SECP256R1())
public_key = private_key.public_key()

# Private key (raw)
private_numbers = private_key.private_numbers()
private_bytes = private_numbers.private_value.to_bytes(32, "big")

# Public key (raw uncompressed point)
public_numbers = public_key.public_numbers()
x = public_numbers.x.to_bytes(32, "big")
y = public_numbers.y.to_bytes(32, "big")
public_bytes = b"\x04" + x + y

print("VAPID_PUBLIC_KEY =", b64url(public_bytes))
print("VAPID_PRIVATE_KEY =", b64url(private_bytes))
