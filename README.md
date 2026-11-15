# BotBuilder — Render deployment

BotBuilder is a FastAPI website that validates Telegram bot tokens and runs the connected bots with aiogram polling.

## Included

- Render Blueprint (`render.yaml`)
- Persistent SQLite disk for a single Render service instance
- Encrypted Telegram bot tokens at rest (Fernet; key derived from `TOKEN_ENCRYPTION_SECRET`)
- Token hash for duplicate detection
- Startup restoration for active trial bots
- Health endpoint: `/api/health`
- Basic per-IP create rate limit
- Responsive Uzbek dashboard

## Important limitations before a public commercial launch

This is a deployable beta, not yet a full multi-user SaaS product.

- The public bot list is informational; there is no user account/login or private per-user dashboard yet.
- The 3-day trial is enforced, but subscriptions, Click/Payme payments, invoices, refunds, and paid-plan renewals are not implemented.
- Shop catalog management, operator forwarding for support bots, and real Telegram channel membership verification require owner configuration and are not implemented as complete workflows.
- The app currently uses one web service instance with SQLite on a persistent disk. Do not scale this service to multiple instances. For larger public usage, migrate to managed PostgreSQL and separate web and background-worker services.
- Anyone can submit a bot token to the public endpoint. The app encrypts stored tokens and applies a simple per-IP rate limit, but a real public launch should add user accounts, ownership verification, abuse controls, and a private dashboard.
- Never commit bot tokens, encryption secrets, `.env`, or the database file to GitHub.

## Deploy to Render

1. Create a GitHub repository for this project.
2. Upload these files to the repository root: `main.py`, `index.html`, `requirements.txt`, `render.yaml`, `.gitignore`, `README.md`.
3. In Render, choose **New → Blueprint** and connect the GitHub repository.
4. Review the Blueprint. It creates a paid Starter Web Service and a 1 GB persistent disk. Check the current price in your Render dashboard before confirming.
5. Deploy. Render generates `TOKEN_ENCRYPTION_SECRET` automatically through `render.yaml`.
6. Open the service URL ending in `onrender.com`.
7. Check `https://YOUR-SERVICE.onrender.com/api/health`.

## Local test on Windows

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
py main.py
```

Open `http://127.0.0.1:8000`.

## Existing database warning

This version uses a new `managed_bots` table and a new database path by default. If you have old bots in `web_builder.db`, they will not be migrated automatically. Back up the old database and migrate/verify records before switching a live service.

## Operations

- Keep exactly one Render instance while using SQLite + a persistent disk.
- Monitor Render logs after deployment.
- If the encryption secret is changed after bots have been stored, the old tokens cannot be decrypted. Do not rotate it without a planned token re-entry/migration.
- A bot token is a password. If it is exposed, revoke it with BotFather and issue a new token.
