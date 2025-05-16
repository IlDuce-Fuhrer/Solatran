import json
from solana.keypair import Keypair
from solana.rpc.api import Client

# Load keypair
with open("C:/Users/USER/Documents/Solatran/keypair.json", "r") as f:
    secret_key = json.load(f)
keypair = Keypair.from_secret_key(bytes(secret_key))

# Initialize Solana client
solana_client = Client("https://api.devnet.solana.com")

# Get balance
balance = solana_client.get_balance(keypair.public_key)

# Extract balance value (handle GetBalanceResp object)
balance_value = balance.value  # Access the 'value' attribute
print(f"Public Key: {keypair.public_key}")
print(f"Balance: {balance_value / 1_000_000_000} SOL")
