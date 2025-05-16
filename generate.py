import json
from solana.keypair import Keypair
# Generate a new Solana keypair
keypair = Keypair()
public_key = str(keypair.public_key)
secret_key = list(keypair.secret_key)  # Convert to list for JSON serialization

# Save to keypair.json
with open("keypair.json", "w") as f:
    json.dump(secret_key, f)

print(f"Public Key: {public_key}")
print(f"Secret Key saved to keypair.json")
print("Fund the keypair on devnet with: solana airdrop 1", public_key, "--url https://api.devnet.solana.com")
