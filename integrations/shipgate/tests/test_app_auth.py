import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from shipgate.app_auth import AppCredentials
from shipgate.github import GitHub


def private_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def test_installation_token_uses_an_app_jwt():
    pem = private_key()
    credentials = AppCredentials("12345", pem)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        header = request.headers["authorization"]
        seen["url"] = str(request.url)
        seen["token"] = header.removeprefix("Bearer ")
        return httpx.Response(201, json={"token": "ghs_install"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        token = credentials.token_for(99, client)
    claims = jwt.decode(seen["token"], options={"verify_signature": False})
    assert token == "ghs_install"
    assert claims["iss"] == "12345"
    assert seen["url"].endswith("/app/installations/99/access_tokens")
    github = GitHub(token)
    assert github.token == "ghs_install"
