from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from pipesor_nlp_log_processor.infrastructure.health_api import RuntimeHealth, create_health_app
from pipesor_nlp_log_processor.infrastructure.platform_identity import NLP_ID, PemSigningKey, WorkloadTokenProvider, public_jwk


TENANT = "24c96be0-50f3-4064-b5e4-643088141ae8"


def key_file(tmp_path: Path) -> Path:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "nlp.pem"
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    path.chmod(0o600)
    return path


def test_workload_claims_signature_and_cache(tmp_path: Path) -> None:
    now = [1_800_000_000.0]
    key = PemSigningKey(key_file(tmp_path))
    tokens = WorkloadTokenProvider(TENANT, key, lambda: now[0])
    first = tokens.get_token()
    claims = jwt.decode(first, key.public_key, algorithms=["RS256"], audience="urn:pipesor:api:correlation",
                        options={"verify_iat": False, "verify_nbf": False, "verify_exp": False})
    assert claims == {
        **{name: NLP_ID for name in ("sub", "oid", "azp")},
        "iss": "urn:pipesor:workload:nlp", "aud": "urn:pipesor:api:correlation",
        "tid": TENANT, "roles": ["Nlp.ProcessedEvidence.Send"], "idtyp": "app", "ver": "2.0",
        "iat": 1_800_000_000, "nbf": 1_800_000_000, "exp": 1_800_000_300, "jti": claims["jti"],
    }
    assert "scp" not in claims
    now[0] += 239
    assert tokens.get_token() == first
    now[0] += 2
    assert tokens.get_token() != first


def test_jwks_and_readiness_expose_only_public_material(tmp_path: Path) -> None:
    key = PemSigningKey(key_file(tmp_path))
    health = RuntimeHealth()
    client = TestClient(create_health_app(health, key))
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 503
    health.receiver(True)
    response = client.get("/.well-known/jwks.json")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=300"
    assert response.json() == {"keys": [public_jwk(key)]}
    assert "d" not in response.json()["keys"][0]
    assert client.get("/health/ready").status_code == 200
    health.authorization(False)
    assert client.get("/health/ready").status_code == 503

