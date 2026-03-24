"""Tests for NIP-04 encryption utilities."""

import pytest
import re
import secp256k1
from shared.crypto import nip04_encrypt, nip04_decrypt, gen_keys


class TestNIP04:
    def test_encrypt_decrypt_roundtrip(self):
        """Encrypted content can be decrypted back to original.

        Alice encrypts to Bob. Bob (receiver) decrypts using:
        - receiver_priv: Bob's private key
        - sender_ephemeral_pub: Alice's ephemeral public key (from ciphertext)
        """
        alice_priv, alice_pub = gen_keys()
        bob_priv, bob_pub = gen_keys()

        # Alice encrypts
        ciphertext = nip04_encrypt("Hello, World!", alice_priv, bob_pub)

        # Bob decrypts using his privkey and the ephemeral pubkey extracted from ciphertext
        import base64
        combined = base64.b64decode(ciphertext)
        ephem_pub = combined[:32].hex()  # Alice's ephemeral pubkey is in ciphertext

        decrypted = nip04_decrypt(ciphertext, bob_priv, ephem_pub)
        assert decrypted == "Hello, World!"

    def test_encrypt_produces_base64(self):
        """Encryption produces Base64-encoded ciphertext."""
        _, bob_pub = gen_keys()
        alice_priv = "a" * 64
        ciphertext = nip04_encrypt("test", alice_priv, bob_pub)
        assert re.match(r'^[A-Za-z0-9+/]+=*$', ciphertext)

    def test_decrypt_wrong_key_fails(self):
        """Decryption with wrong key raises ValueError."""
        alice_priv, alice_pub = gen_keys()
        bob_priv, bob_pub = gen_keys()
        charlie_priv, charlie_pub = gen_keys()

        ciphertext = nip04_encrypt("secret message", alice_priv, bob_pub)

        # Charlie (wrong person) tries to decrypt
        import base64
        combined = base64.b64decode(ciphertext)
        ephem_pub = combined[:32].hex()

        with pytest.raises(ValueError):
            nip04_decrypt(ciphertext, charlie_priv, ephem_pub)
