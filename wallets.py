"""
wallets.py — Multi-chain wallet generation for Solatran
Supports: Solana, Ethereum (+ EVM chains), Tron
All private keys are AES-256 encrypted before storage.
"""

import os
import json
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv

load_dotenv()

# This key must be 32 bytes, stored in .env as hex
# Generate with: python -c "import secrets; print(secrets.token_hex(32))"
ENCRYPTION_KEY = bytes.fromhex(os.getenv("WALLET_ENCRYPTION_KEY", "0" * 64))

SUPPORTED_CHAINS = ["solana", "ethereum", "tron"]

# Token → chain mapping (which chains support which tokens)
TOKEN_CHAINS = {
    "SOL":  ["solana"],
    "ETH":  ["ethereum"],
    "USDT": ["ethereum", "tron", "solana"],   # ERC-20, TRC-20, SPL
    "USDC": ["ethereum", "solana"],
    "BNB":  ["ethereum"],                      # BEP-20 uses same address as ETH
    "MATIC": ["ethereum"],
}

# Token contract addresses (mainnet)
TOKEN_CONTRACTS = {
    "ethereum": {
        "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "BNB":  "0xB8c77482e45F1F44dE1745F52C74426C631bDD52",
    },
    "tron": {
        "USDT": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
        "USDC": "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8",
    },
    "solana": {
        "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
        "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    }
}


# ─── Encryption helpers ───────────────────────────────────────────────────────

def encrypt_key(private_key_bytes: bytes) -> str:
    """Encrypt a private key with AES-256-GCM. Returns base64 string."""
    aesgcm = AESGCM(ENCRYPTION_KEY)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, private_key_bytes, None)
    return base64.b64encode(nonce + ciphertext).decode()


def decrypt_key(encrypted: str) -> bytes:
    """Decrypt an AES-256-GCM encrypted private key."""
    data = base64.b64decode(encrypted)
    nonce, ciphertext = data[:12], data[12:]
    aesgcm = AESGCM(ENCRYPTION_KEY)
    return aesgcm.decrypt(nonce, ciphertext, None)


# ─── Solana ───────────────────────────────────────────────────────────────────

def generate_solana_wallet() -> dict:
    """Generate a new Solana keypair."""
    from solders.keypair import Keypair
    kp = Keypair()
    private_bytes = bytes(kp)
    return {
        "chain": "solana",
        "address": str(kp.pubkey()),
        "encrypted_key": encrypt_key(private_bytes),
    }


def get_solana_keypair(encrypted_key: str):
    """Reconstruct a Solana Keypair from encrypted storage."""
    from solders.keypair import Keypair
    return Keypair.from_bytes(decrypt_key(encrypted_key))


# ─── Ethereum (covers ETH, USDT ERC-20, USDC, BNB, Polygon, Base, etc.) ──────

def generate_ethereum_wallet() -> dict:
    """Generate a new Ethereum wallet. Works for all EVM chains."""
    from eth_account import Account
    Account.enable_unaudited_hdwallet_features()
    acct = Account.create()
    return {
        "chain": "ethereum",
        "address": acct.address,
        "encrypted_key": encrypt_key(acct.key),
    }


def get_ethereum_account(encrypted_key: str):
    """Reconstruct an Ethereum Account from encrypted storage."""
    from eth_account import Account
    return Account.from_key(decrypt_key(encrypted_key))


# ─── Tron (covers USDT TRC-20) ────────────────────────────────────────────────

def generate_tron_wallet() -> dict:
    """Generate a new Tron wallet."""
    from tronpy.keys import PrivateKey
    priv = PrivateKey.random()
    return {
        "chain": "tron",
        "address": priv.public_key.to_base58check_address(),
        "encrypted_key": encrypt_key(bytes(priv)),
    }


def get_tron_key(encrypted_key: str):
    """Reconstruct a Tron PrivateKey from encrypted storage."""
    from tronpy.keys import PrivateKey
    return PrivateKey(decrypt_key(encrypted_key))


# ─── Unified generator ────────────────────────────────────────────────────────

GENERATORS = {
    "solana":   generate_solana_wallet,
    "ethereum": generate_ethereum_wallet,
    "tron":     generate_tron_wallet,
}


def generate_all_wallets() -> list[dict]:
    """Generate one wallet per supported chain for a new user."""
    wallets = []
    for chain in SUPPORTED_CHAINS:
        wallet = GENERATORS[chain]()
        wallets.append(wallet)
        print(f"  ✅ {chain}: {wallet['address']}")
    return wallets


def get_token_chain(token: str) -> list[str]:
    """Return which chains support a given token."""
    return TOKEN_CHAINS.get(token.upper(), [])


def get_contract(chain: str, token: str) -> str | None:
    """Return the contract address for a token on a given chain, or None for native."""
    return TOKEN_CONTRACTS.get(chain, {}).get(token.upper())


if __name__ == "__main__":
    print("Generating test wallets...")
    wallets = generate_all_wallets()
    print("\nAll wallets generated successfully.")
    print("Supported tokens:", list(TOKEN_CHAINS.keys()))
