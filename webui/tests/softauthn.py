"""A software authenticator for the tests: makes and uses an ES256 passkey the way a browser and an
authenticator would, and returns the JSON the app sends (PublicKeyCredential.toJSON()). Each response can be
spoiled on purpose: another origin or RP ID, no user verification, an old signature counter."""

import hashlib
import json
import secrets
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

UP, UV, BE, BS, AT = 0x01, 0x04, 0x08, 0x10, 0x40


class SoftAuthenticator:
    def __init__(self, origin: str = "http://localhost", rp_id: str = "localhost", backed_up: bool = False):
        self.origin = origin
        self.rp_id = rp_id
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(16)
        self.sign_count = 0
        self.flags = BE | BS if backed_up else 0
        self.user_handle: bytes | None = None

    def _client_data(self, kind: str, challenge: str, origin: str | None) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin or self.origin,
                           "crossOrigin": False}).encode()

    def _cose_key(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"),
                            -3: numbers.y.to_bytes(32, "big")})

    def create(self, options: dict, origin: str | None = None, rp_id: str | None = None, uv: bool = True,
               kind: str = "webauthn.create") -> str:
        """The answer to navigator.credentials.create() with the server's options."""
        self.user_handle = base64url_to_bytes(options["user"]["id"])
        rp_hash = hashlib.sha256((rp_id or self.rp_id).encode()).digest()
        flags = UP | AT | (UV if uv else 0) | self.flags
        attested = (b"\0" * 16 + struct.pack(">H", len(self.credential_id)) + self.credential_id
                    + self._cose_key())
        auth_data = rp_hash + bytes([flags]) + struct.pack(">I", self.sign_count) + attested
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        cid = bytes_to_base64url(self.credential_id)
        return json.dumps({
            "id": cid, "rawId": cid, "type": "public-key", "authenticatorAttachment": "platform",
            "response": {
                "clientDataJSON": bytes_to_base64url(self._client_data(kind, options["challenge"], origin)),
                "attestationObject": bytes_to_base64url(attestation),
                "transports": ["internal", "hybrid", "carrier-pigeon"],
            },
            "clientExtensionResults": {},
        })

    def get(self, options: dict, origin: str | None = None, rp_id: str | None = None, uv: bool = True,
            count: int | None = None, user_handle: bytes | None = None) -> str:
        """The answer to navigator.credentials.get(); count sets the signature counter (otherwise it goes up)."""
        self.sign_count = self.sign_count + 1 if count is None else count
        rp_hash = hashlib.sha256((rp_id or self.rp_id).encode()).digest()
        flags = UP | (UV if uv else 0) | self.flags
        auth_data = rp_hash + bytes([flags]) + struct.pack(">I", self.sign_count)
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        signature = self.key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        cid = bytes_to_base64url(self.credential_id)
        handle = user_handle if user_handle is not None else self.user_handle
        return json.dumps({
            "id": cid, "rawId": cid, "type": "public-key", "authenticatorAttachment": "platform",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
                "userHandle": bytes_to_base64url(handle) if handle else None,
            },
            "clientExtensionResults": {},
        })
