from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


NLP_ID = "7c3b75a2-74cf-59a0-babf-1c0415d48762"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _integer(value: int) -> str:
    return _b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))


class SigningKey(Protocol):
    @property
    def public_key(self) -> rsa.RSAPublicKey: ...
    def sign(self, signing_input: bytes) -> bytes: ...


def public_jwk(key: SigningKey) -> dict[str, str]:
    numbers = key.public_key.public_numbers()
    material = {"e": _integer(numbers.e), "kty": "RSA", "n": _integer(numbers.n)}
    thumbprint = _b64(hashlib.sha256(json.dumps(material, separators=(",", ":"), sort_keys=True).encode()).digest())
    return {**material, "alg": "RS256", "kid": thumbprint, "use": "sig"}


class PemSigningKey:
    def __init__(self, path: Path) -> None:
        mode = stat.S_IMODE(path.stat().st_mode)
        if os.name != "nt" and mode not in {0o400, 0o600}:
            raise ValueError("PIPESOR_SIGNING_KEY_FILE must have mode 0400 or 0600")
        encoded = path.read_bytes()
        if not encoded.startswith(b"-----BEGIN PRIVATE KEY-----"):
            raise ValueError("PIPESOR_SIGNING_KEY_FILE must contain PKCS#8 PEM")
        key = serialization.load_pem_private_key(encoded, password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
            raise ValueError("an RSA key of at least 2048 bits is required")
        self._key = key

    @property
    def public_key(self) -> rsa.RSAPublicKey:
        return self._key.public_key()

    def sign(self, signing_input: bytes) -> bytes:
        return self._key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())


class KeyVaultSigningKey:
    def __init__(self, key_id: str, credential: object) -> None:
        from azure.keyvault.keys import KeyClient
        from azure.keyvault.keys.crypto import CryptographyClient, SignatureAlgorithm

        parts = key_id.split("/keys/")
        if len(parts) != 2 or not parts[0].startswith("https://"):
            raise ValueError("PIPESOR_SIGNING_KEY_ID must identify a Key Vault key version")
        name_version = parts[1].split("/")
        if len(name_version) != 2 or not all(name_version):
            raise ValueError("PIPESOR_SIGNING_KEY_ID must pin a key version")
        key = KeyClient(parts[0], credential).get_key(name_version[0], name_version[1])
        if key.key is None or key.key.n is None or key.key.e is None:
            raise ValueError("Key Vault key must expose an RSA public key")
        numbers = rsa.RSAPublicNumbers(int.from_bytes(key.key.e, "big"), int.from_bytes(key.key.n, "big"))
        self._public = numbers.public_key()
        if self._public.key_size < 2048:
            raise ValueError("an RSA key of at least 2048 bits is required")
        self._client = CryptographyClient(key.id, credential)
        self._algorithm = SignatureAlgorithm.rs256

    @property
    def public_key(self) -> rsa.RSAPublicKey:
        return self._public

    def sign(self, signing_input: bytes) -> bytes:
        digest = hashlib.sha256(signing_input).digest()
        return self._client.sign(self._algorithm, digest).signature


@dataclass(frozen=True, slots=True)
class CachedToken:
    value: str
    expires_at: int


class WorkloadTokenProvider:
    def __init__(self, tenant_id: str, key: SigningKey, clock: Callable[[], float] = time.time) -> None:
        self._tenant_id = tenant_id
        self._key = key
        self._clock = clock
        self._cached: CachedToken | None = None

    def get_token(self) -> str:
        now = int(self._clock())
        if self._cached is not None and self._cached.expires_at - 60 > now:
            return self._cached.value
        header = {"alg": "RS256", "kid": public_jwk(self._key)["kid"], "typ": "JWT"}
        claims = {
            "iss": "urn:pipesor:workload:nlp",
            "aud": "urn:pipesor:api:correlation",
            "sub": NLP_ID,
            "oid": NLP_ID,
            "azp": NLP_ID,
            "tid": self._tenant_id,
            "roles": ["Nlp.ProcessedEvidence.Send"],
            "idtyp": "app",
            "ver": "2.0",
            "iat": now,
            "nbf": now,
            "exp": now + 300,
            "jti": str(uuid.uuid4()),
        }
        signing_input = ".".join(
            _b64(json.dumps(part, separators=(",", ":"), sort_keys=True).encode())
            for part in (header, claims)
        ).encode("ascii")
        value = signing_input.decode("ascii") + "." + _b64(self._key.sign(signing_input))
        self._cached = CachedToken(value, now + 300)
        return value

