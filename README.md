# Solatran

A multi-currency crypto transfer service powered by Twitter. Send SOL, ETH, USDT, USDC and more to anyone on Twitter — just by mentioning a bot in a tweet. No wallet apps, no browser extensions, no complicated addresses. Just tweet.

---

## How it works

```
@Solatran send 10 USDT to @friend
```

That's it. Solatran sees the tweet, checks both accounts, moves the funds instantly, and replies with a confirmation — all in under a second. No blockchain fees for transfers between users.

---

## Commands

| Tweet | What it does |
|---|---|
| `@Solatran send 10 USDT to @someone` | Send any supported token to a registered user |
| `@Solatran balance` | Get your current balances (sent via DM) |
| `@Solatran deposit ETH` | Get your ETH deposit address (sent via DM) |
| `@Solatran withdraw 0.1 ETH 0xABC...` | Withdraw funds to an external wallet |

---

## Supported currencies

| Token | Networks |
|---|---|
| SOL | Solana |
| ETH | Ethereum |
| USDT | Ethereum (ERC-20), Tron (TRC-20), Solana (SPL) |
| USDC | Ethereum, Solana |
| BNB | BNB Chain |

---

## Getting started

### 1. Register

Visit **solatran.xyz** and connect your Twitter account. Solatran automatically generates a deposit wallet for you on every supported chain. No seed phrases to manage — Solatran is a custodial service.

### 2. Deposit

Tweet `@Solatran deposit SOL` (or any token) and the bot will DM you your deposit address. Send funds to that address and your Solatran balance is credited automatically.

### 3. Send

Tweet the send command and funds move instantly between registered users at zero cost. Transfers between Solatran users never touch the blockchain — they're instant internal balance updates.

### 4. Withdraw

When you're ready to move funds to your own external wallet, tweet the withdraw command. A 0.5% fee applies on withdrawals. On-chain confirmation times vary by network.

---

## Architecture

Solatran is built on three layers:

**Twitter bot (`main.py`)** — Polls Twitter mentions every 60 seconds, parses commands, and routes them to the transfer engine.

**Transfer engine (`transfer.py`)** — Validates users and balances, executes internal transfers as database updates, and handles on-chain withdrawals.

**Fetch.ai agent (`sol.py`)** — Intelligent middleware that handles fraud detection, spending limits, and multi-step confirmations. Also exposes Solatran as an agent on the ASI:One network.

**PostgreSQL database** — Stores users, wallets, balances, and a full transaction audit trail.

```
Tweet → main.py → sol.py agent → transfer.py → PostgreSQL
                                      ↓
                               Solana / Ethereum / Tron
                               (withdrawals and deposits only)
```

---

## Project structure

```
Solatran/
├── main.py          # Twitter bot — polls mentions, parses commands
├── sol.py           # Fetch.ai agent — validation and intelligence layer
├── transfer.py      # Transfer engine — internal transfers and withdrawals
├── models.py        # SQLAlchemy database models
├── wallets.py       # Multi-chain wallet generation and key encryption
├── keygen.py        # Bot keypair generation utility
├── keypair.json     # Bot's Solana keypair (never commit this)
├── .env             # API keys and secrets (never commit this)
├── requirements.txt # Python dependencies
├── Procfile         # Deployment config for Railway / Fly.io
└── README.md
```

---

## Environment variables

Create a `.env` file in the project root:

```env
# Twitter API
TWITTER_CLIENT_ID=
TWITTER_CLIENT_SECRET=
TWITTER_REDIRECT_URL=http://127.0.0.1:5000/callback

# Database
DATABASE_URL=postgresql://user:password@localhost:5432/solatran

# Wallet encryption (generate with: python -c "import secrets; print(secrets.token_hex(32))")
WALLET_ENCRYPTION_KEY=

# Solana
SOLANA_RPC=https://api.mainnet-beta.solana.com
KEYPAIR_PATH=keypair.json

# Ethereum
ETHEREUM_RPC=https://mainnet.infura.io/v3/YOUR_INFURA_KEY

# Tron
TRON_RPC=https://api.trongrid.io

# Fetch.ai agent
AGENT_SEED=your-secret-agent-seed-phrase
```

---

## Installation

```bash
# Clone the repo
git clone https://github.com/yourname/solatran.git
cd solatran

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Fill in your keys in .env

# Initialize the database
python models.py

# Run the bot locally
python main.py
```

---

## Dependencies

```
solana==0.33.0
solders==0.21.0
web3==6.x
tronpy
eth-account
flask==3.0.3
sqlalchemy
psycopg2-binary
requests==2.32.3
python-dotenv==1.0.1
cryptography
uagents
gunicorn==23.0.0
```

---

## Security

- Private keys are encrypted with AES-256-GCM before database storage — never stored in plaintext
- The `.env` file and `keypair.json` are in `.gitignore` and must never be committed
- Per-transaction limits and daily spending caps are enforced by the transfer engine
- Every transaction is logged with its source tweet ID — duplicate tweet processing is blocked at the database level
- Withdrawals require the user to be the authenticated owner of the sending account

---

## Deployment

Solatran is configured for **Railway** out of the box via the included `Procfile`. 

```bash
# Install Railway CLI
npm i -g @railway/cli

# Login and deploy
railway login
railway init
railway up
```

Set all `.env` variables in the Railway dashboard under **Variables** before deploying.

---

## Roadmap

- [x] Solana wallet generation and transfer
- [x] Multi-chain wallet generation (ETH, Tron)
- [x] Internal transfer engine (off-chain)
- [x] Database models and audit trail
- [ ] Registration web app (Twitter OAuth + wallet linking)
- [ ] Deposit watcher (on-chain balance monitoring)
- [ ] Rewritten Twitter bot with full command support
- [ ] Fetch.ai agent integration
- [ ] SPL token withdrawals (USDT, USDC on Solana)
- [ ] Telegram interface
- [ ] Web dashboard for balance and history

---

## License

MIT
