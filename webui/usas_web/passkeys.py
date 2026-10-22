"""Passkeys (WebAuthn, with py_webauthn): another way for the UI's one user to sign in, or the only way.

Passkeys are added from the account page once signed in (with the current password), and kept in
passkeys.json in the data directory with "Require a passkey", which turns password sign-in off. Each
challenge is random, belongs to one session, is used once and expires after 5 minutes. A response counts
only if it comes from the UI's own origin (USAS_PUBLIC_URL, or http://localhost on any port), is for its
RP ID, and the authenticator verified the user (a PIN or biometrics); the signature counter must go up if
the authenticator keeps one.
"""

import json
import re
import secrets
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import (
    base64url_to_bytes,
    bytes_to_base64url,
    parse_authentication_credential_json,
    parse_client_data_json,
    parse_registration_credential_json,
)
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .auth import Session
from .config import Settings
from .store import write_private

CHALLENGE_SECONDS = 5 * 60
MAX_PASSKEYS = 20
MAX_NAME = 64
MAX_RESPONSE = 64 * 1024  # a response's JSON; real ones are a few kilobytes
RP_NAME = "USAS"
USER_NAME = "usas"
USER_DISPLAY_NAME = "USAS web UI"
# Without USAS_PUBLIC_URL: the UI on localhost, whatever port Docker publishes it on.
LOCAL_ORIGIN = re.compile(r"https?://localhost(?::[0-9]{1,5})?\Z")
TRANSPORTS = {t.value for t in AuthenticatorTransport}


class PasskeyError(Exception):
    """A passkey step that failed: message is for the user, reason (more detail) for the log."""

    def __init__(self, message: str, reason: str = ""):
        super().__init__(message)
        self.message = message
        self.reason = reason or message


@dataclass
class Challenge:
    purpose: str  # "register" or "login"
    value: bytes
    expires: float
    name: str = ""  # the new passkey's name, for "register"


def clean_name(name: str) -> str:
    """A passkey's name without control or format characters, with single spaces."""
    text = "".join(" " if unicodedata.category(c).startswith(("C", "Z")) else c for c in name)
    return " ".join(text.split())


def loggable(text: str, limit: int = 200) -> str:
    """Text from a request, safe to put in one log line."""
    return "".join(c if c.isprintable() else "?" for c in text)[:limit]


class Passkeys:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()

    # --- The file ----------------------------------------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        path = self.settings.passkeys_file
        if not path.exists():
            return {"user_id": "", "require": False, "credentials": []}
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict) or not isinstance(data.get("credentials"), list):
                raise ValueError("not a passkeys file")
            return data
        except (OSError, ValueError):
            # Fail closed: an unreadable file turns password sign-in off (as "Require a passkey" would)
            # and accepts no passkey. Deleting the file is the way back (docs/web-ui.md).
            return {"user_id": "", "require": True, "credentials": [], "broken": True}

    def _write(self, data: dict[str, Any]) -> None:
        data.pop("broken", None)
        write_private(self.settings.passkeys_file, json.dumps(data, indent=2) + "\n")

    def _user_id(self, data: dict[str, Any]) -> bytes:
        """The user handle passkeys are made for: random, made with the first passkey."""
        if not data.get("user_id"):
            data["user_id"] = bytes_to_base64url(secrets.token_bytes(32))
        return base64url_to_bytes(data["user_id"])

    # --- What the account page shows ---------------------------------------------------------------------

    def listing(self) -> list[dict[str, Any]]:
        return [{"id": c["id"], "name": c["name"], "created": c["created"], "last_used": c.get("last_used"),
                 "backed_up": bool(c.get("backed_up"))} for c in self._read()["credentials"]]

    def count(self) -> int:
        return len(self._read()["credentials"])

    def required(self) -> bool:
        return bool(self._read().get("require"))

    def broken(self) -> bool:
        return bool(self._read().get("broken"))

    def set_required(self, on: bool) -> None:
        with self._lock:
            data = self._read()
            if data.get("broken"):
                raise PasskeyError("The passkeys file can't be read. Delete passkeys.json from the data volume.")
            if on and not data["credentials"]:
                raise PasskeyError("Add a passkey first: with none, nobody could sign in.")
            data["require"] = on
            self._write(data)

    def remove(self, credential_id: str) -> str:
        """Removes a passkey; returns its name."""
        with self._lock:
            data = self._read()
            found = [c for c in data["credentials"] if c["id"] == credential_id]
            if not found:
                raise PasskeyError("That passkey isn't registered (any more).")
            if data.get("require") and len(data["credentials"]) == 1:
                raise PasskeyError("This is the only passkey and a passkey is required to sign in. Add another "
                                   "one first, or turn off “Require a passkey”.")
            data["credentials"] = [c for c in data["credentials"] if c["id"] != credential_id]
            self._write(data)
            return found[0]["name"]

    # --- Origins and challenges --------------------------------------------------------------------------

    def origin_ok(self, origin: str) -> bool:
        if self.settings.public_url:
            return origin == self.settings.public_url
        return bool(LOCAL_ORIGIN.match(origin))

    def _origin(self, client_data_json: bytes) -> str:
        """The origin the browser put in the response, if it is the UI's own; PasskeyError otherwise."""
        try:
            origin = parse_client_data_json(client_data_json).origin
        except Exception as exc:  # noqa: BLE001 (py_webauthn raises several kinds for a bad clientDataJSON)
            raise PasskeyError("The browser's answer couldn't be read.", f"bad clientDataJSON: {exc}") from None
        if not self.origin_ok(origin):
            expected = self.settings.public_url or "http://localhost:<port>"
            raise PasskeyError("The passkey was used on another site.",
                               f"origin {loggable(origin)!r} isn't {expected} (see USAS_PUBLIC_URL)")
        return origin

    @staticmethod
    def _new_challenge(session: Session, purpose: str, name: str = "") -> bytes:
        value = secrets.token_bytes(32)
        session.passkey_challenge = Challenge(purpose, value, time.time() + CHALLENGE_SECONDS, name)
        return value

    @staticmethod
    def _take_challenge(session: Session, purpose: str) -> Challenge:
        """The session's challenge for purpose, which can't be used again whatever happens next."""
        challenge, session.passkey_challenge = session.passkey_challenge, None
        if not isinstance(challenge, Challenge) or challenge.purpose != purpose:
            raise PasskeyError("Start again: this page didn't ask for a passkey.", "no challenge for this session")
        if time.time() > challenge.expires:
            raise PasskeyError("That took too long. Try again.", "the challenge expired")
        return challenge

    @staticmethod
    def _descriptors(data: dict[str, Any]) -> list[PublicKeyCredentialDescriptor]:
        return [PublicKeyCredentialDescriptor(
            id=base64url_to_bytes(c["id"]),
            transports=[AuthenticatorTransport(t) for t in c.get("transports", []) if t in TRANSPORTS])
            for c in data["credentials"]]

    # --- Adding a passkey ----------------------------------------------------------------------------------

    def registration_options(self, session: Session, name: str) -> str:
        """The options for navigator.credentials.create(), as JSON; name must be clean_name()d already."""
        with self._lock:
            data = self._read()
            if data.get("broken"):
                raise PasskeyError("The passkeys file can't be read. Delete passkeys.json from the data volume.")
            if len(data["credentials"]) >= MAX_PASSKEYS:
                raise PasskeyError(f"There are {MAX_PASSKEYS} passkeys already. Remove one first.")
            new_user = not data.get("user_id")
            user_id = self._user_id(data)
            if new_user:
                self._write(data)
        options = generate_registration_options(
            rp_id=self.settings.rp_id,
            rp_name=RP_NAME,
            user_id=user_id,
            user_name=USER_NAME,
            user_display_name=USER_DISPLAY_NAME,
            challenge=self._new_challenge(session, "register", name),
            timeout=CHALLENGE_SECONDS * 1000,
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=self._descriptors(data),
        )
        return options_to_json(options)

    def register(self, session: Session, response: str) -> dict[str, Any]:
        """Checks the browser's answer to registration_options() and saves the passkey."""
        challenge = self._take_challenge(session, "register")
        if len(response) > MAX_RESPONSE:
            raise PasskeyError("The browser's answer is too large.", "response too large")
        try:
            credential = parse_registration_credential_json(response)
        except WebAuthnException as exc:
            raise PasskeyError("The browser's answer couldn't be read.", f"bad response: {exc}") from None
        origin = self._origin(credential.response.client_data_json)
        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=challenge.value,
                expected_rp_id=self.settings.rp_id,
                expected_origin=origin,
                require_user_verification=True,
            )
        except Exception as exc:  # noqa: BLE001 (any failure to verify is a failed sign-in, not a crash)
            raise PasskeyError("The passkey couldn't be verified.", loggable(str(exc))) from None
        record = {
            "id": bytes_to_base64url(verified.credential_id),
            "public_key": bytes_to_base64url(verified.credential_public_key),
            "sign_count": verified.sign_count,
            "transports": [t.value for t in credential.response.transports or []],
            "name": challenge.name,
            "created": int(time.time()),
            "last_used": None,
            "backed_up": verified.credential_backed_up,
        }
        with self._lock:
            data = self._read()
            if any(c["id"] == record["id"] for c in data["credentials"]):
                raise PasskeyError("That passkey is registered already.", "credential ID registered already")
            if len(data["credentials"]) >= MAX_PASSKEYS:
                raise PasskeyError(f"There are {MAX_PASSKEYS} passkeys already. Remove one first.")
            data["credentials"].append(record)
            self._write(data)
        return record

    # --- Signing in --------------------------------------------------------------------------------------

    def authentication_options(self, session: Session) -> str:
        """The options for navigator.credentials.get(), as JSON. They list the registered passkeys, so
        security keys that don't store passkeys themselves work too."""
        data = self._read()
        if not data["credentials"]:
            raise PasskeyError("No passkey is registered. Sign in with the password.", "no passkeys")
        options = generate_authentication_options(
            rp_id=self.settings.rp_id,
            challenge=self._new_challenge(session, "login"),
            timeout=CHALLENGE_SECONDS * 1000,
            allow_credentials=self._descriptors(data),
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        return options_to_json(options)

    def authenticate(self, session: Session, response: str) -> dict[str, Any]:
        """Checks the browser's answer to authentication_options(); returns the passkey it used."""
        challenge = self._take_challenge(session, "login")
        if len(response) > MAX_RESPONSE:
            raise PasskeyError("The browser's answer is too large.", "response too large")
        try:
            credential = parse_authentication_credential_json(response)
        except WebAuthnException as exc:
            raise PasskeyError("The browser's answer couldn't be read.", f"bad response: {exc}") from None
        origin = self._origin(credential.response.client_data_json)
        credential_id = bytes_to_base64url(credential.raw_id)
        with self._lock:
            data = self._read()
            found = next((c for c in data["credentials"] if c["id"] == credential_id), None)
            if found is None:
                raise PasskeyError("That passkey isn't registered here.", "unknown credential ID")
            handle = credential.response.user_handle
            if handle is not None and data.get("user_id") and handle != base64url_to_bytes(data["user_id"]):
                raise PasskeyError("That passkey isn't registered here.", "user handle doesn't match")
            try:
                verified = verify_authentication_response(
                    credential=credential,
                    expected_challenge=challenge.value,
                    expected_rp_id=self.settings.rp_id,
                    expected_origin=origin,
                    credential_public_key=base64url_to_bytes(found["public_key"]),
                    credential_current_sign_count=int(found.get("sign_count", 0)),
                    require_user_verification=True,
                )
            except Exception as exc:  # noqa: BLE001 (any failure to verify is a failed sign-in, not a crash)
                raise PasskeyError("The passkey couldn't be verified.", loggable(str(exc))) from None
            found["sign_count"] = verified.new_sign_count
            found["last_used"] = int(time.time())
            found["backed_up"] = verified.credential_backed_up
            self._write(data)
        return found

