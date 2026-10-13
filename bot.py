import os
import re
import sqlite3
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

TOKEN = os.environ['BOT_TOKEN']
ADMIN_ID = int(os.environ['ADMIN_ID'])

RENDER_EXTERNAL_URL = os.getenv('RENDER_EXTERNAL_URL')
PORT = int(os.getenv('PORT', 10000))
WEBHOOK_PATH = f"/webhook/{TOKEN}"

DB_PATH = os.getenv('DB_PATH', 'bot.db')

DEVELOPER_NAME = "DEVELOPED BY SUMIT KUMAR"


# ==================== HTML STRIPPER ====================
def strip_html(html):
    """HTML tags, scripts, styles — sab remove karo"""
    html = re.sub(r'<script.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<[^>]+>', '', html)
    html = html.replace('&nbsp;', ' ')
    html = html.replace('&amp;', '&')
    html = html.replace('&lt;', '<')
    html = html.replace('&gt;', '>')
    html = html.replace('&quot;', '"')
    html = html.replace('&#39;', "'")
    html = re.sub(r'\n\s*\n', '\n', html)
    html = re.sub(r'[ \t]+', ' ', html)
    return html.strip()


# ==================== DATABASE LAYER ====================
def db_conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def db_init():
    c = db_conn()
    cur = c.cursor()
    cur.executescript('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        language_code TEXT,
        is_premium INTEGER DEFAULT 0,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        msg_count INTEGER DEFAULT 0
    );
    ''')
    c.commit()
    cur.close()
    c.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def register_user(u):
    c = db_conn()
    cur = c.cursor()
    cur.execute('SELECT user_id FROM users WHERE user_id=?', (u.id,))
    exists = cur.fetchone()

    if exists:
        cur.execute('''
            UPDATE users
            SET username=?, first_name=?, last_name=?,
                language_code=?, is_premium=?, last_seen=?, msg_count=msg_count+1
            WHERE user_id=?
        ''', (
            u.username or '', u.first_name or '', u.last_name or '',
            u.language_code or '',
            1 if getattr(u, 'is_premium', False) else 0,
            now_iso(), u.id
        ))
        is_new = False
    else:
        cur.execute('''
            INSERT INTO users(user_id, username, first_name, last_name,
                              language_code, is_premium, first_seen, last_seen, msg_count)
            VALUES(?,?,?,?,?,?,?,?,1)
        ''', (
            u.id, u.username or '', u.first_name or '', u.last_name or '',
            u.language_code or '',
            1 if getattr(u, 'is_premium', False) else 0,
            now_iso(), now_iso()
        ))
        is_new = True

    c.commit()
    cur.close()
    c.close()
    return is_new


def get_user(uid):
    c = db_conn()
    cur = c.cursor()
    cur.execute('SELECT * FROM users WHERE user_id=?', (uid,))
    r = cur.fetchone()
    cur.close()
    c.close()
    return r


def all_users(limit=50):
    c = db_conn()
    cur = c.cursor()
    cur.execute('SELECT * FROM users ORDER BY first_seen DESC LIMIT ?', (limit,))
    rows = cur.fetchall()
    cur.close()
    c.close()
    return rows


def user_count():
    c = db_conn()
    cur = c.cursor()
    cur.execute('SELECT COUNT(*) AS n FROM users')
    r = cur.fetchone()
    cur.close()
    c.close()
    return r['n'] if r else 0


# ==================== KEYBOARDS ====================
def kb(uid):
    x = [
        [InlineKeyboardButton('🔍 Lookup Services', callback_data='lookup_menu')],
        [InlineKeyboardButton('💥 SMS Bomber', callback_data='bomber')],
        [InlineKeyboardButton('📊 Status', callback_data='status')]
    ]
    if uid == ADMIN_ID:
        x.append([InlineKeyboardButton('👑 Admin Panel', callback_data='admin')])
    return InlineKeyboardMarkup(x)


def admin_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('👥 All Users', callback_data='users')],
        [InlineKeyboardButton('📊 User Stats', callback_data='stats')]
    ])


def lookup_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📱 Number Lookup', callback_data='lookup_num')],
        [InlineKeyboardButton('🚗 Vahan Lookup', callback_data='lookup_vahan')],
        [InlineKeyboardButton('🏦 IFSC Lookup', callback_data='lookup_ifsc')],
        [InlineKeyboardButton('🆔 Aadhaar Lookup', callback_data='lookup_aadhaar')],
        [InlineKeyboardButton('⬅️ Back', callback_data='home')]
    ])


# ==================== WATERMARK ====================
def clean_response(text):
    lines = text.split('\n')
    clean_lines = []
    skip_keywords = [
        'developer', 'priyanshu', 'gupta', 'channel',
        't.me', 'priyanshuexploits', '@priyanshu',
        '👨‍💻', '📢', '❤️'
    ]
    for line in lines:
        low = line.lower().strip()
        if any(kw in low for kw in skip_keywords):
            continue
        clean_lines.append(line)
    text = '\n'.join(clean_lines).strip()
    while '\n\n\n' in text:
        text = text.replace('\n\n\n', '\n\n')
    return text


def add_watermark(text):
    return text + f"\n\n━━━━━━━━━━━━━━━━━━━━\n👨‍💻 {DEVELOPER_NAME}\n━━━━━━━━━━━━━━━━━━━━"


# ==================== HOME ====================
async def home(update, context):
    u = update.effective_user
    is_new = register_user(u)

    if is_new:
        try:
            admin_msg = (
                f"🆕 *NEW USER*\n\n"
                f"👤 Name: {u.first_name or ''} {u.last_name or ''}\n"
                f"📛 Username: @{u.username}\n"
                f"🆔 User ID: `{u.id}`\n"
                f"🌐 Language: {u.language_code or 'unknown'}\n"
                f"⏰ Time: {datetime.now().strftime('%d %b %Y, %I:%M %p')}"
            )
            await context.bot.send_message(ADMIN_ID, admin_msg, parse_mode='Markdown')
        except Exception:
            pass

    text = (
        f"👋 Hi {u.first_name or 'User'}!\n\n"
        f"Welcome to the OSINT Bot.\n\n"
        f"Use the buttons below to access Lookup Services and SMS Bomber.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👨‍💻 {DEVELOPER_NAME}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb(u.id))
    else:
        await update.message.reply_text(text, reply_markup=kb(u.id))


async def start(update, context):
    await home(update, context)


async def status(update, context):
    await home(update, context)


# ==================== LOOKUP MENU ====================
async def lookup_menu(update, context):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        '🔍 *Lookup Services*\n\nChoose a service below:',
        reply_markup=lookup_kb(),
        parse_mode='Markdown'
    )


async def lookup_num_start(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data['lookup_type'] = 'num'
    await q.edit_message_text(
        '📱 *Number Lookup*\n\nSend the mobile number (10 digits):',
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='lookup_menu')]])
    )


async def lookup_vahan_start(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data['lookup_type'] = 'vahan'
    await q.edit_message_text(
        '🚗 *Vahan Lookup*\n\nSend the vehicle RC number (e.g., DL01AB1234):',
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='lookup_menu')]])
    )


async def lookup_ifsc_start(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data['lookup_type'] = 'ifsc'
    await q.edit_message_text(
        '🏦 *IFSC Lookup*\n\nSend the IFSC code (e.g., SBIN0001234):',
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='lookup_menu')]])
    )


async def lookup_aadhaar_start(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data['lookup_type'] = 'aadhaar'
    await q.edit_message_text(
        '🆔 *Aadhaar Lookup*\n\nSend the 12-digit Aadhaar number:',
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='lookup_menu')]])
    )


# ==================== BOMBER ====================
async def bomber_start(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data['bomber'] = True
    await q.edit_message_text(
        '💥 *SMS Bomber*\n\nSend the 10-digit mobile number to bomb:',
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='home')]])
    )


# ==================== TEXT MESSAGE (main handler) ====================
async def text_msg(update, context):
    # register user
    register_user(update.effective_user)

    # ---- BOMBER ----
    if context.user_data.get('bomber'):
        context.user_data['bomber'] = False
        number = update.message.text.strip()
        if not number.isdigit() or len(number) != 10:
            await update.message.reply_text(
                '❌ Invalid number. Send 10-digit mobile number.',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='home')]])
            )
            return

        processing = await update.message.reply_text(
            f'💥 *Bombing started...*\n\n📱 Number: `{number}`\n⏳ Please wait...',
            parse_mode='Markdown'
        )

        url = f'https://rohithost.myvipsite.fun/Bomber/?number={number}'
        try:
            timeout = aiohttp.ClientTimeout(total=45)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.get(url) as r:
                    raw = await r.text()
                    clean = strip_html(raw)
                    clean = clean_response(clean)
                    clean = add_watermark(clean)

                    # agar HTML ke baad kuch meaningful nahi bacha
                    if len(clean.strip()) < 100:
                        final = (
                            f'✅ *Bombing initiated*\n\n'
                            f'📱 Number: `{number}`\n'
                            f'💥 Status: Running\n'
                            f'⏱️ Duration: ~10 minutes\n\n'
                            f'_Server is sending SMS in background._'
                        )
                    else:
                        final = clean[:4000]

                    await processing.edit_text(
                        final,
                        parse_mode='Markdown',
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='home')]])
                    )
        except asyncio.TimeoutError:
            await processing.edit_text(
                f'⏱️ *Bombing in progress*\n\n'
                f'📱 Number: `{number}`\n'
                f'💥 Server is sending SMS...\n'
                f'_(Timeout — server may still be running)_',
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='home')]])
            )
        except Exception as e:
            await processing.edit_text(
                f'❌ Error: {e}',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='home')]])
            )
        return

    # ---- LOOKUP ----
    lookup_type = context.user_data.get('lookup_type')
    if lookup_type:
        context.user_data['lookup_type'] = None
        v = update.message.text.strip()
        urls = {
            'num': f'https://thanksfor100user.vercel.app/num?number={v}',
            'vahan': f'https://priyanshu-pied-xi.vercel.app/api/vahan?rc={v}&format=card',
            'ifsc': f'https://priyanshuexploits.vercel.app/api/ifsc/{v}?format=text',
            'aadhaar': f'https://aadhar-xi.vercel.app/api/lookup?id={v}&format=card'
        }
        try:
            timeout = aiohttp.ClientTimeout(total=25)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.get(urls[lookup_type]) as r:
                    text = await r.text()
                    text = clean_response(text)
                    text = add_watermark(text)
                    await update.message.reply_text(
                        text[:4000],
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='home')]])
                    )
        except Exception as e:
            await update.message.reply_text(
                f'❌ Error: {e}',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='home')]])
            )
        return

    # ---- kuch aur bheja to home ----
    await update.message.reply_text(
        '👇 Use the buttons below:',
        reply_markup=kb(update.effective_user.id)
    )


# ==================== ADMIN ====================
async def admin(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        return
    await q.edit_message_text(
        f'👑 *Admin Panel*\n\nTotal Users: *{user_count()}*',
        reply_markup=admin_kb(),
        parse_mode='Markdown'
    )


async def users_list(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        return

    rows = all_users(50)
    if not rows:
        await q.edit_message_text('👥 No users yet.', reply_markup=admin_kb())
        return

    await q.edit_message_text(
        f'👥 *Last 50 Users* (Total: {user_count()})',
        parse_mode='Markdown',
        reply_markup=admin_kb()
    )

    for r in rows:
        uname = f"@{r['username']}" if r['username'] else "(no username)"
        text = (
            f"👤 *{r['first_name'] or ''} {r['last_name'] or ''}*\n"
            f"📛 {uname}\n"
            f"🆔 `{r['user_id']}`\n"
            f"🌐 {r['language_code'] or 'unknown'}\n"
            f"💎 Premium: {'✅' if r['is_premium'] else '❌'}\n"
            f"📩 Messages: {r['msg_count']}\n"
            f"🕐 First seen: {r['first_seen'][:19]}\n"
            f"🕐 Last seen: {r['last_seen'][:19]}"
        )
        try:
            await context.bot.send_message(ADMIN_ID, text, parse_mode='Markdown')
        except Exception:
            pass


async def stats(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        return
    await q.edit_message_text(
        f'📊 *Bot Statistics*\n\n👥 Total Users: *{user_count()}*',
        reply_markup=admin_kb(),
        parse_mode='Markdown'
    )


# ==================== CALLBACKS ====================
async def callbacks(update, context):
    d = update.callback_query.data
    if d == 'home':
        await home(update, context)
    elif d == 'status':
        await home(update, context)
    elif d == 'admin':
        await admin(update, context)
    elif d == 'users':
        await users_list(update, context)
    elif d == 'stats':
        await stats(update, context)
    elif d == 'lookup_menu':
        await lookup_menu(update, context)
    elif d == 'lookup_num':
        await lookup_num_start(update, context)
    elif d == 'lookup_vahan':
        await lookup_vahan_start(update, context)
    elif d == 'lookup_ifsc':
        await lookup_ifsc_start(update, context)
    elif d == 'lookup_aadhaar':
        await lookup_aadhaar_start(update, context)
    elif d == 'bomber':
        await bomber_start(update, context)


# ==================== HEALTH SERVER ====================
from http.server import BaseHTTPRequestHandler, HTTPServer


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')

    def log_message(self, format, *args):
        pass


# ==================== MAIN ====================
def main():
    db_init()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_msg))

    if RENDER_EXTERNAL_URL:
        print(f'Starting webhook on {RENDER_EXTERNAL_URL}{WEBHOOK_PATH}')
        app.run_webhook(
            listen='0.0.0.0',
            port=PORT,
            url_path=WEBHOOK_PATH,
            webhook_url=f'{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}',
            drop_pending_updates=True
        )
    else:
        print('RENDER_EXTERNAL_URL not set, running in polling mode')
        app.run_polling()


if __name__ == '__main__':
    main()
