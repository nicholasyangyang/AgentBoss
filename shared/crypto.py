"""Nostr cryptographic primitives: Bech32, secp256k1, Schnorr."""

import base64
import hashlib
import secrets

import secp256k1
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

# ── Bech32 encoding/decoding ──────────────────────────────────────────

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_CHARSET_MAP = {c: i for i, c in enumerate(_CHARSET)}
_GENERATOR = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]


def _polymod(values: list[int]) -> int:
    chk = 1
    for val in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ val
        for i in range(5):
            chk ^= _GENERATOR[i] if (top >> i) & 1 else 0
    return chk


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _convert_bits(data: bytes, from_bits: int, to_bits: int, pad: bool = True) -> list[int]:
    acc = 0
    bits = 0
    result: list[int] = []
    max_val = (1 << to_bits) - 1
    for val in data:
        acc = (acc << from_bits) | val
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            result.append((acc >> bits) & max_val)
    if pad and bits:
        result.append((acc << (to_bits - bits)) & max_val)
    return result


def _bech32_encode(hrp: str, data: bytes) -> str:
    values = _convert_bits(data, 8, 5)
    polymod = _polymod(_hrp_expand(hrp) + values + [0] * 6) ^ 1
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_CHARSET[v] for v in values + checksum)


def _bech32_decode(bech: str) -> tuple[str, bytes]:
    bech = bech.lower()
    pos = bech.rfind("1")
    if pos < 1 or pos + 7 > len(bech):
        raise ValueError("invalid bech32 string")
    hrp = bech[:pos]
    try:
        data = [_CHARSET_MAP[c] for c in bech[pos + 1 :]]
    except KeyError:
        raise ValueError("invalid bech32 character")
    if _polymod(_hrp_expand(hrp) + data) != 1:
        raise ValueError("invalid bech32 checksum")
    return hrp, bytes(_convert_bits(data[:-6], 5, 8, pad=False))


def to_npub(hex_pubkey: str) -> str:
    return _bech32_encode("npub", bytes.fromhex(hex_pubkey))


def to_nsec(hex_privkey: str) -> str:
    return _bech32_encode("nsec", bytes.fromhex(hex_privkey))


def npub_to_hex(npub: str) -> str:
    hrp, data = _bech32_decode(npub)
    if hrp != "npub":
        raise ValueError("not an npub")
    return data.hex()


def nsec_to_hex(nsec: str) -> str:
    hrp, data = _bech32_decode(nsec)
    if hrp != "nsec":
        raise ValueError("not an nsec")
    return data.hex()


# ── secp256k1 key generation & signing ────────────────────────────────


def gen_keys() -> tuple[str, str]:
    priv_bytes = secrets.token_bytes(32)
    pub_bytes = secp256k1.PrivateKey(priv_bytes).pubkey.serialize()[1:]
    return priv_bytes.hex(), pub_bytes.hex()


def derive_pub(hex_privkey: str) -> str:
    return secp256k1.PrivateKey(bytes.fromhex(hex_privkey)).pubkey.serialize()[1:].hex()


def schnorr_sign(event_id_hex: str, hex_privkey: str) -> str:
    return secp256k1.PrivateKey(bytes.fromhex(hex_privkey)).schnorr_sign(
        bytes.fromhex(event_id_hex), None, raw=True
    ).hex()


# ── NIP-04 DM Encryption ──────────────────────────────────────────────

import os
from cryptography.hazmat.primitives.asymmetric.ec import SECP256K1, ECDH
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.backends import default_backend


def _aes_256_ctr_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    """AES-256-CTR encryption."""
    cipher = Cipher(
        algorithms.AES(key),
        modes.CTR(iv),
        backend=default_backend()
    )
    encryptor = cipher.encryptor()
    return encryptor.update(plaintext) + encryptor.finalize()


def _aes_256_ctr_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    """AES-256-CTR decryption."""
    cipher = Cipher(
        algorithms.AES(key),
        modes.CTR(iv),
        backend=default_backend()
    )
    decryptor = cipher.decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def _secp256k1_hex_to_crypt_privkey(hex_priv: str):
    """Convert secp256k1 hex private key to cryptography PrivateKey."""
    priv_int = int.from_bytes(bytes.fromhex(hex_priv), "big")
    return ec.derive_private_key(priv_int, SECP256K1(), default_backend())


def nip04_encrypt(plaintext: str, sender_priv_hex: str, recipient_pub_hex: str) -> str:
    """Encrypt plaintext for NIP-04 DM.

    Uses AES-256-CTR with ECDH-derived shared secret.
    Returns Base64-encoded "ephem_pubkey + iv + ciphertext".

    Args:
        plaintext: The message to encrypt
        sender_priv_hex: Sender's private key (64 hex chars)
        recipient_pub_hex: Recipient's public key X coordinate (64 hex chars)

    Returns:
        Base64-encoded "ephem_pubkey + iv + ciphertext" string
    """
    # Generate ephemeral keypair using cryptography (compatible with secp256k1)
    ephem_priv = ec.generate_private_key(SECP256K1(), default_backend())

    # Get recipient's public key from X coordinate (secp256k1 format)
    # secp256k1 pubkey is 33 bytes: 0x02/0x03 + 32 bytes X coordinate
    recipient_pub_x = bytes.fromhex(recipient_pub_hex)
    # For ECDH with recipient's public key, we need to reconstruct it
    # Using the X coordinate, we can derive Y from curve equation (secp256k1)
    # But simpler: use the fact that secp256k1.PrivateKey can work with raw bytes
    recipient_priv_int = int.from_bytes(recipient_pub_x, "big")
    # We don't have recipient's privkey - we only have their pubkey X coord
    # So we use ECDH with ephemeral priv and recipient pubkey object

    # For ECDH, we need to create a public key from X coordinate
    # secp256k1 compressed format: 0x02/0x03 + X coordinate
    # We need to determine if Y is even or odd - assume even (0x02)
    y_even = True
    prefix = bytes([0x02 if y_even else 0x03])
    # (prefix + recipient_pub_x would be the compressed pubkey, but we derive Y from curve eq)

    curve = SECP256K1()
    # For secp256k1, y^2 = x^3 + 7 (mod p)
    # We need to compute y from x
    p = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
    x = recipient_priv_int
    # y^2 = x^3 + 7 (mod p)
    y_squared = (pow(x, 3, p) + 7) % p
    y = pow(y_squared, (p + 1) // 4, p)  # square root mod p
    if y % 2 != 0:
        y = p - y

    # Create public numbers
    public_numbers = ec.EllipticCurvePublicNumbers(x, y, curve)
    recipient_pubkey = public_numbers.public_key(default_backend())

    # ECDH: shared secret = ephem_priv * recipient_pubkey
    shared_secret = ephem_priv.exchange(ECDH(), recipient_pubkey)

    # Derive encryption key: sha256(shared_secret)
    encryption_key = hashlib.sha256(shared_secret).digest()

    # Get ephemeral public key X coordinate (32 bytes) for ciphertext
    ephem_pub_x = ephem_priv.public_key().public_numbers().x.to_bytes(32, "big")

    # Random 16-byte IV
    iv = os.urandom(16)

    # Encrypt
    plaintext_bytes = plaintext.encode("utf-8")
    ciphertext = _aes_256_ctr_encrypt(encryption_key, iv, plaintext_bytes)

    # Combine: ephem_pub_x (32) + iv (16) + ciphertext
    combined = ephem_pub_x + iv + ciphertext

    # Return as Base64
    return base64.b64encode(combined).decode("ascii")


def nip04_decrypt(ciphertext_b64: str, receiver_priv_hex: str, sender_ephem_pub_x_hex: str) -> str:
    """Decrypt NIP-04 DM ciphertext.

    Args:
        ciphertext_b64: Base64-encoded "ephem_pubkey_x + iv + ciphertext"
        receiver_priv_hex: Receiver's private key (64 hex chars)
        sender_ephem_pub_x_hex: Sender's ephemeral public key X coordinate (64 hex chars)

    Returns:
        Decrypted plaintext string

    Raises:
        ValueError: If decryption fails (wrong key or corrupted ciphertext)
    """
    try:
        combined = base64.b64decode(ciphertext_b64)

        # Extract: ephem_pub_x (first 32 bytes), iv (next 16 bytes), ciphertext (rest)
        ephem_pub_x_bytes = combined[:32]
        iv = combined[32:48]
        ciphertext = combined[48:]

        # Receiver's private key
        receiver_priv = _secp256k1_hex_to_crypt_privkey(receiver_priv_hex)

        # Sender's ephemeral public key X coordinate
        sender_pub_x = int.from_bytes(ephem_pub_x_bytes, "big")
        # Compute Y coordinate
        p = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
        y_squared = (pow(sender_pub_x, 3, p) + 7) % p
        y = pow(y_squared, (p + 1) // 4, p)
        if y % 2 != 0:
            y = p - y

        curve = SECP256K1()
        public_numbers = ec.EllipticCurvePublicNumbers(sender_pub_x, y, curve)
        sender_pubkey = public_numbers.public_key(default_backend())

        # ECDH: shared secret = receiver_priv * sender_pubkey
        shared_secret = receiver_priv.exchange(ECDH(), sender_pubkey)

        # Same key derivation
        encryption_key = hashlib.sha256(shared_secret).digest()

        # Decrypt
        plaintext_bytes = _aes_256_ctr_decrypt(encryption_key, iv, ciphertext)
        return plaintext_bytes.decode("utf-8")
    except Exception as e:
        raise ValueError(f"Decryption failed: {e}")
