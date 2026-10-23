"""Generate a signed Matrix server-key response from a Rezzy key file."""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_decrypt
from nacl.exceptions import CryptoError
from nacl.pwhash import argon2id
from nacl.signing import SigningKey

MAGIC = b"REZKEY\x00\x01"
HEADER_SIZE = 60
MAX_M_COST_KIB = 64 * 1024
MAX_T_COST = 3


def _passphrase(origin: str) -> bytes:
    suffix = re.sub(r"[.-]", "_", origin).upper()
    for name in (f"REZZY_KEY_PASSPHRASE_{suffix}", "REZZY_KEY_PASSPHRASE"):
        value = os.environ.get(name)
        if value:
            return value.encode()
    return getpass.getpass(f"Passphrase for {origin}: ").encode()


def _decrypt_key(path: Path, passphrase: bytes) -> tuple[str, SigningKey]:
    encrypted = path.read_bytes()
    if len(encrypted) < HEADER_SIZE + 16 or encrypted[:8] != MAGIC:
        raise ValueError("signing key must be a Rezzy passphrase-encrypted key file")

    m_cost = int.from_bytes(encrypted[8:12], "big")
    t_cost = int.from_bytes(encrypted[12:16], "big")
    lanes = int.from_bytes(encrypted[16:20], "big")
    if not 1 <= m_cost <= MAX_M_COST_KIB:
        raise ValueError("encrypted key file has unsupported memory cost")
    if not 1 <= t_cost <= MAX_T_COST or lanes != 1:
        raise ValueError("encrypted key file has unsupported KDF parameters")

    salt = encrypted[20:36]
    nonce = encrypted[36:60]
    key = argon2id.kdf(
        32,
        passphrase,
        salt,
        opslimit=t_cost,
        memlimit=m_cost * 1024,
    )
    try:
        plaintext = crypto_aead_xchacha20poly1305_ietf_decrypt(
            encrypted[HEADER_SIZE:], encrypted[:HEADER_SIZE], nonce, key
        )
    except CryptoError as error:
        raise ValueError("wrong passphrase or corrupted key file") from error

    fields = plaintext.decode("utf-8").split()
    if not fields:
        raise ValueError("decrypted signing key is empty")
    if fields[0].startswith("ed25519:"):
        key_id = fields[0]
        encoded_seed = fields[1] if len(fields) > 1 else ""
    elif fields[0] == "ed25519":
        if len(fields) < 3:
            raise ValueError("decrypted signing key is malformed")
        key_id = f"ed25519:{fields[1]}"
        encoded_seed = fields[2]
    else:
        raise ValueError("decrypted signing key must use ed25519")

    encoded_seed += "=" * (-len(encoded_seed) % 4)
    seed = base64.b64decode(encoded_seed, altchars=b"-_", validate=True)
    if len(seed) != 32:
        raise ValueError("Ed25519 signing seed must decode to 32 bytes")
    return key_id, SigningKey(seed)


def _old_keys(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("old-key file must contain a JSON object")
    for key_id, entry in value.items():
        if (
            not isinstance(key_id, str)
            or not key_id
            or not isinstance(entry, dict)
            or not isinstance(entry.get("key"), str)
            or not isinstance(entry.get("expired_ts"), int)
            or isinstance(entry.get("expired_ts"), bool)
        ):
            raise ValueError(
                'each old key must map to {"key": base64, "expired_ts": integer}'
            )
        encoded_key = entry["key"]
        padded_key = encoded_key + "=" * (-len(encoded_key) % 4)
        key_bytes = base64.b64decode(padded_key, altchars=b"-_", validate=True)
        if len(key_bytes) != 32:
            raise ValueError(f"old key {key_id} must decode to 32 bytes")
        entry["key"] = base64.b64encode(key_bytes).decode("ascii").rstrip("=")
    return value


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def generate(
    origin: str,
    key_path: Path,
    output_path: Path,
    lifetime_hours: int,
    old_keys_path: Path | None,
) -> None:
    if not origin:
        raise ValueError("server name must not be empty")
    output_file = output_path.resolve()
    input_files = [key_path.resolve()]
    if old_keys_path is not None:
        input_files.append(old_keys_path.resolve())
    if output_file in input_files:
        raise ValueError("output must not overwrite an input key file")
    passphrase = _passphrase(origin)
    key_id, signing_key = _decrypt_key(key_path, passphrase)
    response: dict[str, Any] = {
        "server_name": origin,
        "valid_until_ts": int(time.time() * 1000) + lifetime_hours * 60 * 60 * 1000,
        "verify_keys": {
            key_id: {
                "key": base64.b64encode(signing_key.verify_key.encode())
                .decode("ascii")
                .rstrip("=")
            }
        },
        "old_verify_keys": _old_keys(old_keys_path),
    }
    signature = signing_key.sign(_canonical_json(response)).signature
    response["signatures"] = {
        origin: {key_id: base64.b64encode(signature).decode("ascii").rstrip("=")}
    }
    _atomic_write(output_path, _canonical_json(response) + b"\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a Matrix /_matrix/key/v2/server response from a "
            "Rezzy-encrypted Ed25519 signing key."
        )
    )
    parser.add_argument("--origin", required=True, help="Matrix server name")
    parser.add_argument("--signing-key", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--old-keys",
        type=Path,
        help="Optional JSON map of old key IDs to key and expired_ts fields",
    )
    parser.add_argument(
        "--lifetime-hours",
        type=int,
        default=24,
        help="Response validity horizon (1 to 168 hours; default: 24)",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.lifetime_hours <= 168:
        parser.error("--lifetime-hours must be between 1 and 168")
    try:
        generate(
            args.origin,
            args.signing_key,
            args.output,
            args.lifetime_hours,
            args.old_keys,
        )
    except (OSError, ValueError, CryptoError) as error:
        print(f"key response generation failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
