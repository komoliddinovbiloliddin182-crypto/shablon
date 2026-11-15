import asyncio
import base64
import hashlib
import logging
import os
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ConfigDict

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("botbuilder")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "botbuilder.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

secret = os.getenv("TOKEN_ENCRYPTION_SECRET")
if not secret:
    # Local development only. On Render, render.yaml generates a persistent env secret.
    local_key_file = BASE_DIR / ".local_encryption_secret"
    if os.getenv("RENDER"):
        raise RuntimeError("TOKEN_ENCRYPTION_SECRET Render Environment'da sozlanmagan.")
    if local_key_file.exists():
        secret = local_key_file.read_text(encoding="utf-8").strip()
    else:
        import secrets
        secret = secrets.token_urlsafe(48)
        local_key_file.write_text(secret, encoding="utf-8")
        try:
            local_key_file.chmod(0o600)
        except OSError:
            pass

fernet_key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
cipher = Fernet(fernet_key)

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.execute("""
CREATE TABLE IF NOT EXISTS managed_bots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    token_ciphertext TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    template TEXT NOT NULL,
    created_at TEXT NOT NULL,
    trial_ends_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
)
""")
db.commit()
db_lock = asyncio.Lock()
running_bots: dict[str, tuple[asyncio.Task, Bot]] = {}
rate_log: dict[str, deque] = defaultdict(deque)
VALID_TEMPLATES = {"support", "shop", "quiz", "sub"}
TEMPLATE_LABELS = {
    "support": "Yordam markazi",
    "shop": "Onlayn do'kon",
    "quiz": "Quiz / Test",
    "sub": "Obuna boti",
}

def encrypt_token(token: str) -> str:
    return cipher.encrypt(token.encode("utf-8")).decode("ascii")

def decrypt_token(value: str) -> str:
    return cipher.decrypt(value.encode("ascii")).decode("utf-8")

def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def is_valid_trial(row) -> bool:
    try:
        return bool(row["is_active"]) and datetime.utcnow() < datetime.fromisoformat(row["trial_ends_at"])
    except (ValueError, TypeError):
        return False

def menu_for(template: str):
    kb = ReplyKeyboardBuilder()
    if template == "support":
        kb.button(text="💬 Savol yuborish")
        kb.button(text="ℹ️ Yordam")
    elif template == "shop":
        kb.button(text="🛍️ Katalog")
        kb.button(text="📦 Buyurtma berish")
        kb.button(text="☎️ Aloqa")
    elif template == "quiz":
        kb.button(text="🧠 Testni boshlash")
        kb.button(text="ℹ️ Qoidalar")
    else:
        kb.button(text="📢 Obuna haqida")
        kb.button(text="☎️ Admin bilan aloqa")
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)

def build_dispatcher(token: str, template: str):
    dp = Dispatcher()

    async def check_trial(message: types.Message) -> bool:
        async with db_lock:
            row = db.execute(
                "SELECT is_active, trial_ends_at FROM managed_bots WHERE token_hash=?",
                (token_digest(token),)
            ).fetchone()
            valid = bool(row) and is_valid_trial(row)
            if row and not valid and row["is_active"]:
                db.execute("UPDATE managed_bots SET is_active=0 WHERE token_hash=?", (token_digest(token),))
                db.commit()
        if not valid:
            await message.answer("⏳ Ushbu botning sinov muddati tugagan yoki bot vaqtincha to‘xtatilgan.")
        return valid

    @dp.message(CommandStart())
    async def start(message: types.Message):
        if not await check_trial(message):
            return
        greetings = {
            "support": "Assalomu alaykum! 👋\nYordam markaziga xush kelibsiz.\nSavolingizni yuboring — bot uni qabul qiladi.",
            "shop": "Assalomu alaykum! 🛍️\nDo‘kon botiga xush kelibsiz!\nKatalog yoki buyurtma bo‘limini tanlang.",
            "quiz": "Salom! 🧠\nBilimingizni sinab ko‘rishga tayyormisiz? Testni boshlang!",
            "sub": "Assalomu alaykum! 🔐\nObuna botiga xush kelibsiz. Obuna tartibi haqida ma’lumot oling."
        }
        await message.answer(greetings[template], reply_markup=menu_for(template))

    @dp.message(Command("help"))
    async def help_cmd(message: types.Message):
        if await check_trial(message):
            await message.answer(
                "Mavjud buyruqlar:\n/start — bosh menyu\n/help — yordam\n"
                "Xabaringizni yuboring, bot qabul qiladi.",
                reply_markup=menu_for(template)
            )

    @dp.message(F.text == "ℹ️ Yordam")
    @dp.message(F.text == "ℹ️ Qoidalar")
    async def info(message: types.Message):
        if await check_trial(message):
            await message.answer("Savolingiz yoki xabaringizni shu chatga yozib yuboring. ✅")

    @dp.message(F.text == "🛍️ Katalog")
    async def catalog(message: types.Message):
        if await check_trial(message):
            await message.answer(
                "🛍️ Mahsulotlar katalogi\n\n"
                "Katalog hali sozlanmagan. Bot egasi mahsulotlar va narxlarni admin paneldan qo‘shishi kerak."
            )

    @dp.message(F.text == "📦 Buyurtma berish")
    async def order(message: types.Message):
        if await check_trial(message):
            await message.answer("📦 Mahsulot nomi, miqdori va aloqa ma’lumotini bitta xabarda yuboring.")

    @dp.message(F.text == "🧠 Testni boshlash")
    async def quiz(message: types.Message):
        if await check_trial(message):
            await message.answer("🧠 Savol: O‘zbekiston poytaxti qaysi shahar?\n\nA) Samarqand\nB) Toshkent\nC) Buxoro")

    @dp.message(F.text == "📢 Obuna haqida")
    async def subscription(message: types.Message):
        if await check_trial(message):
            await message.answer(
                "📢 Kanalga obuna bo‘lish tartibi bot egasi tomonidan sozlanishi kerak. "
                "Hozircha bu bo‘lim ma’lumot berish rejimida ishlaydi."
            )

    @dp.message(F.text.in_({"☎️ Aloqa", "☎️ Admin bilan aloqa"}))
    async def contact(message: types.Message):
        if await check_trial(message):
            await message.answer("Aloqa ma’lumotlari hali sozlanmagan. Iltimos, keyinroq urinib ko‘ring.")

    @dp.message()
    async def fallback(message: types.Message):
        if not await check_trial(message):
            return
        if template == "quiz":
            answer = (message.text or "").strip().lower()
            if answer in {"b", "b)", "toshkent"}:
                await message.answer("🎉 To‘g‘ri javob! Toshkent — O‘zbekiston poytaxti.")
            else:
                await message.answer("Javob qabul qilindi. To‘g‘ri javob: B) Toshkent.")
        elif template == "support":
            await message.answer("✅ Xabaringiz qabul qilindi. Operator bilan aloqa funksiyasi egasi sozlagandan keyin ishlaydi.")
        elif template == "shop":
            await message.answer("✅ So‘rovingiz qabul qilindi. Buyurtma bo‘limidan foydalaning.")
        else:
            await message.answer("Xabaringiz qabul qilindi. Obuna bo‘limidagi ko‘rsatmalarni ko‘ring.")

    return dp

async def run_bot(token: str, template: str):
    existing = running_bots.get(token)
    if existing and not existing[0].done():
        return
    bot = Bot(token=token)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        dp = build_dispatcher(token, template)
        async def poll():
            try:
                await dp.start_polling(bot, handle_signals=False, close_bot_session=False)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Telegram polling to‘xtadi: %s", token_digest(token)[:10])
            finally:
                await bot.session.close()
        task = asyncio.create_task(poll(), name=f"telegram-{token_digest(token)[:8]}")
        running_bots[token] = (task, bot)
        log.info("Telegram bot polling boshlandi: %s", token_digest(token)[:10])
    except Exception:
        await bot.session.close()
        log.exception("Botni ishga tushirishda xato")

async def stop_all_bots():
    tasks = []
    for task, bot in list(running_bots.values()):
        task.cancel()
        tasks.append(task)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    for _, bot in list(running_bots.values()):
        try:
            await bot.session.close()
        except Exception:
            pass
    running_bots.clear()

def allow_create(ip: str) -> bool:
    now = time.time()
    q = rate_log[ip]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= 5:
        return False
    q.append(now)
    return True

class CreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    bot_token: str = Field(min_length=30, max_length=100)
    template: str = Field(min_length=3, max_length=20)

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with db_lock:
        rows = db.execute("SELECT * FROM managed_bots WHERE is_active=1").fetchall()
        active_tokens = []
        for row in rows:
            if is_valid_trial(row):
                try:
                    active_tokens.append((decrypt_token(row["token_ciphertext"]), row["template"]))
                except (InvalidToken, ValueError):
                    log.error("Bot tokenini ochib bo‘lmadi; bot avtomatik ishga tushirilmadi.")
                    db.execute("UPDATE managed_bots SET is_active=0 WHERE id=?", (row["id"],))
            else:
                db.execute("UPDATE managed_bots SET is_active=0 WHERE id=?", (row["id"],))
        db.commit()
    for token, template in active_tokens:
        await run_bot(token, template)
    yield
    await stop_all_bots()
    db.close()

app = FastAPI(title="BotBuilder", version="1.0.0", lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def home():
    return (BASE_DIR / "index.html").read_text(encoding="utf-8")

@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "service": "BotBuilder",
        "running_bots": sum(not task.done() for task, _ in running_bots.values())
    }

@app.get("/api/bots")
async def list_bots():
    # Minimal public listing: no tokens and no exact trial dates are exposed.
    async with db_lock:
        rows = db.execute(
            "SELECT username, template, trial_ends_at, is_active FROM managed_bots ORDER BY id DESC LIMIT 100"
        ).fetchall()
        result = []
        for row in rows:
            active = is_valid_trial(row)
            result.append({
                "username": row["username"],
                "template": TEMPLATE_LABELS.get(row["template"], row["template"]),
                "trial_ends_at": datetime.fromisoformat(row["trial_ends_at"]).strftime("%Y-%m-%d"),
                "is_active": active
            })
    return result

@app.post("/api/create-bot")
async def create_bot(req: CreateRequest, request: Request):
    if req.template not in VALID_TEMPLATES:
        return JSONResponse({"ok": False, "error": "Bot shabloni noto‘g‘ri."}, status_code=400)
    ip = request.client.host if request.client else "unknown"
    if not allow_create(ip):
        return JSONResponse(
            {"ok": False, "error": "Juda ko‘p urinish. Bir soatdan keyin qayta urinib ko‘ring."},
            status_code=429
        )

    token = req.bot_token.strip()
    test_bot = Bot(token=token)
    try:
        me = await test_bot.get_me()
        if not me.username:
            return JSONResponse({"ok": False, "error": "Bot username aniqlanmadi."}, status_code=400)
    except Exception:
        return JSONResponse(
            {"ok": False, "error": "Token noto‘g‘ri yoki Telegram bilan aloqa yo‘q."},
            status_code=400
        )
    finally:
        await test_bot.session.close()

    digest = token_digest(token)
    now = datetime.utcnow()
    trial_end = now + timedelta(days=3)
    async with db_lock:
        exists = db.execute("SELECT id FROM managed_bots WHERE token_hash=?", (digest,)).fetchone()
        if exists:
            return JSONResponse({"ok": False, "error": "Bu bot avval platformaga qo‘shilgan."}, status_code=409)
        db.execute(
            """INSERT INTO managed_bots
               (username, token_ciphertext, token_hash, template, created_at, trial_ends_at, is_active)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (
                me.username,
                encrypt_token(token),
                digest,
                req.template,
                now.isoformat(),
                trial_end.isoformat()
            )
        )
        db.commit()

    await run_bot(token, req.template)
    return {"ok": True, "username": me.username, "trial_ends_at": trial_end.isoformat()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
