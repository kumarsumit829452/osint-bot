import os, sqlite3, asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

TOKEN = os.environ['BOT_TOKEN']
ADMIN_ID = int(os.environ['ADMIN_ID'])
QR_PATH = os.getenv('PAYMENT_QR_PATH', 'paytm_qr.png')
PRICE, DAYS = 100, 30

DATABASE_URL = os.getenv('DATABASE_URL')
DB = os.getenv('DB_PATH', 'bot.db')

RENDER_EXTERNAL_URL = os.getenv('RENDER_EXTERNAL_URL')
PORT = int(os.getenv('PORT', 10000))
WEBHOOK_PATH = f"/webhook/{TOKEN}"

# 🔥 Aapka naam
DEVELOPER_NAME = "DEVELOPED BY SUMIT KUMAR"


# ---------- DB LAYER ----------
class DBWrapper:
    def __init__(self):
        self.is_pg = bool(DATABASE_URL)

    def connect(self):
        if self.is_pg:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            url = DATABASE_URL
            if url.startswith('postgres://'):
                url = url.replace('postgres://', 'postgresql://', 1)
            conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
            return conn
        else:
            c = sqlite3.connect(DB)
            c.row_factory = sqlite3.Row
            return c

    def param(self, q):
        return q.replace('?', '%s') if self.is_pg else q

    def init(self):
        c = self.connect()
        cur = c.cursor()
        if self.is_pg:
            cur.execute('''
            CREATE TABLE IF NOT EXISTS users(
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                wallet INTEGER NOT NULL DEFAULT 0,
                premium_until TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS payments(
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount INTEGER NOT NULL,
                utr TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                verified_at TEXT,
                UNIQUE(user_id, utr)
            );
            CREATE TABLE IF NOT EXISTS transactions(
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                type TEXT NOT NULL,
                amount INTEGER NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL
            );
            ''')
        else:
            cur.executescript('''
            CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, wallet INTEGER NOT NULL DEFAULT 0, premium_until TEXT);
            CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,amount INTEGER NOT NULL,utr TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL,verified_at TEXT,UNIQUE(user_id,utr));
            CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,type TEXT NOT NULL,amount INTEGER NOT NULL,note TEXT,created_at TEXT NOT NULL);
            ''')
        c.commit()
        cur.close()
        c.close()


db = DBWrapper()


def conn():
    return db.connect()


def now():
    return datetime.now(timezone.utc)


def iso():
    return now().isoformat()


def ensure(u):
    c = conn()
    cur = c.cursor()
    q = '''INSERT INTO users(user_id, username, first_name, wallet, premium_until)
           VALUES(?,?,?,0,NULL)
           ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name'''
    cur.execute(db.param(q), (u.id, u.username or '', u.first_name or ''))
    c.commit()
    cur.close()
    c.close()


def ensure_by_id(uid):
    c = conn()
    cur = c.cursor()
    q = '''INSERT INTO users(user_id, username, first_name, wallet, premium_until)
           VALUES(?,?,?,0,NULL)
           ON CONFLICT(user_id) DO NOTHING'''
    cur.execute(db.param(q), (uid, '', ''))
    c.commit()
    cur.close()
    c.close()


def user(uid):
    c = conn()
    cur = c.cursor()
    cur.execute(db.param('SELECT * FROM users WHERE user_id=?'), (uid,))
    r = cur.fetchone()
    cur.close()
    c.close()
    return r


def active(r):
    try:
        if not r or not r['premium_until']:
            return False
        pu = r['premium_until']
        if isinstance(pu, str):
            return datetime.fromisoformat(pu) > now()
        if pu.tzinfo is None:
            pu = pu.replace(tzinfo=timezone.utc)
        return pu > now()
    except Exception:
        return False


def fmt_dt(val):
    if isinstance(val, str):
        dt = datetime.fromisoformat(val)
    else:
        dt = val
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime('%d %b %Y, %I:%M %p')


async def premium_check_silent(uid):
    ensure_by_id(uid)
    r = user(uid)
    return active(r)


# ---------- WATERMARK FILTER ----------
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


# ---------- KEYBOARDS ----------
def kb(uid):
    x = [
        [InlineKeyboardButton('💎 Premium ₹100 / 30 Days', callback_data='premium'),
         InlineKeyboardButton('💰 Wallet', callback_data='wallet')],
        [InlineKeyboardButton('💳 Pay ₹100 / Submit UTR', callback_data='pay'),
         InlineKeyboardButton('📊 Status', callback_data='status')],
        [InlineKeyboardButton('🔍 Lookup Services', callback_data='lookup_menu')]
    ]
    if uid == ADMIN_ID:
        x.append([InlineKeyboardButton('👑 Admin Panel', callback_data='admin')])
    return InlineKeyboardMarkup(x)


def admin_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('⏳ Pending Payments', callback_data='pending')],
        [InlineKeyboardButton('👥 User Stats', callback_data='users')]
    ])


# ---------- HANDLERS ----------
async def home(update, context):
    u = update.effective_user
    ensure(u)
    r = user(u.id)
    exp = '❌ Premium inactive'
    if active(r):
        exp = f"✅ Premium active\nExpiry: {fmt_dt(r['premium_until'])}"
    text = (
        f"👋 Hi {u.first_name or 'User'}!\n\n"
        f"💰 Wallet: ₹{r['wallet']}\n{exp}\n\n"
        f"Use the buttons below. Premium is ₹100 for 30 days.\n\n"
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


async def pay(update, context):
    q = update.callback_query
    await q.answer()
    text = ('💳 Premium Payment\n\nAmount: ₹100\nValidity: 30 days\n\n'
            'Scan the Paytm QR and pay exactly ₹100. Then submit your UTR.\n\n'
            '⚠️ UTR is only a claim. Admin verifies the real payment in Paytm Business before approval.')
    k = InlineKeyboardMarkup([
        [InlineKeyboardButton('🧾 Submit UTR', callback_data='utr')],
        [InlineKeyboardButton('⬅️ Back', callback_data='home')]
    ])
    p = Path(QR_PATH)
    if p.exists():
        with p.open('rb') as f:
            await q.message.reply_photo(f, caption=text, reply_markup=k)
    else:
        await q.message.reply_text(text + '\n\n⚠️ QR image not configured on server yet.', reply_markup=k)


async def utr_start(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data['utr'] = True
    await q.message.reply_text('🧾 Send your UTR / transaction reference now.\n\nDo not send OTP, password, card details or any secret.')


# ---------- LOOKUP MENU ----------
async def lookup_menu(update, context):
    q = update.callback_query
    await q.answer()
    k = InlineKeyboardMarkup([
        [InlineKeyboardButton('📱 Number Lookup', callback_data='lookup_num')],
        [InlineKeyboardButton('🚗 Vahan Lookup', callback_data='lookup_vahan')],
        [InlineKeyboardButton('🏦 IFSC Lookup', callback_data='lookup_ifsc')],
        [InlineKeyboardButton('⬅️ Back', callback_data='home')]
    ])
    await q.edit_message_text(
        '🔍 *Lookup Services*\n\nChoose a service below:\n\n'
        '⚠️ Premium required for all lookups.',
        reply_markup=k,
        parse_mode='Markdown'
    )


async def lookup_num_start(update, context):
    q = update.callback_query
    await q.answer()
    if not await premium_check_silent(q.from_user.id):
        await q.edit_message_text(
            '🔒 Premium required.\n\nUse /start → Pay ₹100 / Submit UTR.',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='home')]])
        )
        return
    context.user_data['lookup_type'] = 'num'
    await q.edit_message_text(
        '📱 *Number Lookup*\n\nSend the mobile number (10 digits):',
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='lookup_menu')]])
    )


async def lookup_vahan_start(update, context):
    q = update.callback_query
    await q.answer()
    if not await premium_check_silent(q.from_user.id):
        await q.edit_message_text(
            '🔒 Premium required.\n\nUse /start → Pay ₹100 / Submit UTR.',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='home')]])
        )
        return
    context.user_data['lookup_type'] = 'vahan'
    await q.edit_message_text(
        '🚗 *Vahan Lookup*\n\nSend the vehicle RC number (e.g., DL01AB1234):',
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='lookup_menu')]])
    )


async def lookup_ifsc_start(update, context):
    q = update.callback_query
    await q.answer()
    if not await premium_check_silent(q.from_user.id):
        await q.edit_message_text(
            '🔒 Premium required.\n\nUse /start → Pay ₹100 / Submit UTR.',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='home')]])
        )
        return
    context.user_data['lookup_type'] = 'ifsc'
    await q.edit_message_text(
        '🏦 *IFSC Lookup*\n\nSend the IFSC code (e.g., SBIN0001234):',
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='lookup_menu')]])
    )


# ---------- TEXT MESSAGE HANDLER ----------
async def text_msg(update, context):
    # ---- LOOKUP TYPE CHECK ----
    lookup_type = context.user_data.get('lookup_type')
    if lookup_type:
        context.user_data['lookup_type'] = None
        v = update.message.text.strip()
        urls = {
            'num': f'https://thanksfor100user.vercel.app/num?number={v}',
            'vahan': f'https://priyanshu-pied-xi.vercel.app/api/vahan?rc={v}&format=card',
            'ifsc': f'https://priyanshuexploits.vercel.app/api/ifsc/{v}?format=text'
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(urls[lookup_type], timeout=20) as r:
                    text = await r.text()
                    text = clean_response(text)
                    text = add_watermark(text)
                    await update.message.reply_text(
                        text[:4000],
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='home')]])
                    )
        except Exception as e:
            await update.message.reply_text(f'Error: {e}')
        return

    # ---- UTR CHECK ----
    if not context.user_data.get('utr'):
        return
    u = update.effective_user
    ensure(u)
    utr = update.message.text.strip()
    context.user_data['utr'] = False
    if not utr.isalnum() or not 6 <= len(utr) <= 40:
        await update.message.reply_text('❌ Invalid UTR format. Send the transaction reference only.')
        return
    c = conn()
    cur = c.cursor()
    try:
        q = "INSERT INTO payments(user_id,amount,utr,created_at) VALUES(?,?,?,?)"
        if db.is_pg:
            cur.execute(db.param(q) + ' RETURNING id', (u.id, PRICE, utr, iso()))
            pid = cur.fetchone()['id']
        else:
            cur.execute(q, (u.id, PRICE, utr, iso()))
            pid = cur.lastrowid
        c.commit()
    except Exception as e:
        c.rollback()
        cur.close()
        c.close()
        if 'unique' in str(e).lower() or 'duplicate' in str(e).lower():
            await update.message.reply_text('⚠️ This UTR was already submitted.')
        else:
            await update.message.reply_text('❌ Database error. Try again.')
        return
    cur.close()
    c.close()
    await update.message.reply_text(f'✅ Payment request submitted.\n\nID: #{pid}\nAmount: ₹100\nUTR: {utr}\n\n⏳ Admin will verify it in Paytm Business.')
    name = '@' + u.username if u.username else (u.first_name or 'User')
    k = InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ APPROVE', callback_data=f'approve:{pid}'),
         InlineKeyboardButton('❌ REJECT', callback_data=f'reject:{pid}')]
    ])
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f'💳 NEW PAYMENT\n\nID: #{pid}\nUser: {name}\nTelegram ID: {u.id}\nAmount: ₹100\nUTR: {utr}\n\nVerify in Paytm Business before approving.',
            reply_markup=k
        )
    except Exception:
        pass


# ---------- WALLET / PREMIUM ----------
async def wallet(update, context):
    q = update.callback_query
    await q.answer()
    ensure(q.from_user)
    r = user(q.from_user.id)
    k = InlineKeyboardMarkup([
        [InlineKeyboardButton('💳 Add ₹100', callback_data='pay')],
        [InlineKeyboardButton('💎 Buy Premium ₹100', callback_data='buy')],
        [InlineKeyboardButton('⬅️ Back', callback_data='home')]
    ])
    await q.edit_message_text(
        f"💰 Wallet Balance: ₹{r['wallet']}\n\nApproved payments are added here. Use the balance to buy Premium.",
        reply_markup=k
    )


async def buy(update, context):
    q = update.callback_query
    await q.answer()
    ensure(q.from_user)
    r = user(q.from_user.id)
    if r['wallet'] < PRICE:
        await q.edit_message_text(
            f"❌ Wallet: ₹{r['wallet']}\nPremium: ₹100\n\nAdd ₹100 first.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('💳 Add ₹100', callback_data='pay')],
                [InlineKeyboardButton('⬅️ Back', callback_data='home')]
            ])
        )
        return
    if active(r):
        base = r['premium_until']
        if isinstance(base, str):
            base = datetime.fromisoformat(base)
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        start_dt = base
    else:
        start_dt = now()
    expiry = start_dt + timedelta(days=DAYS)
    c = conn()
    cur = c.cursor()
    cur.execute(db.param('UPDATE users SET wallet=wallet-?, premium_until=? WHERE user_id=?'),
                (PRICE, expiry.isoformat(), q.from_user.id))
    cur.execute(db.param("INSERT INTO transactions(user_id,type,amount,note,created_at) VALUES(?,?,?,?,?)"),
                (q.from_user.id, 'premium_purchase', -PRICE, '30 days Premium', iso()))
    c.commit()
    cur.close()
    c.close()
    await q.edit_message_text(
        f"🎉 Premium activated!\n\n💎 30 days\n📅 Expiry: {fmt_dt(expiry)}\n💰 Wallet left: ₹{r['wallet']-PRICE}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n👨‍💻 {DEVELOPER_NAME}\n━━━━━━━━━━━━━━━━━━━━",
        reply_markup=kb(q.from_user.id)
    )


async def premium_gate(update):
    await update.message.reply_text('🔒 Premium required. Use /start → Pay ₹100 / Submit UTR.')


async def premium(update):
    ensure(update.effective_user)
    r = user(update.effective_user.id)
    if not active(r):
        await premium_gate(update)
        return False
    return True


# ---------- SLASH COMMANDS ----------
async def lookup_cmd(update, context, kind):
    if not await premium(update):
        return
    if not context.args:
        await update.message.reply_text(f'Usage: /{kind} <value>')
        return
    v = context.args[0]
    urls = {
        'num': f'https://thanksfor100user.vercel.app/num?number={v}',
        'vahan': f'https://priyanshu-pied-xi.vercel.app/api/vahan?rc={v}&format=card',
        'ifsc': f'https://priyanshuexploits.vercel.app/api/ifsc/{v}?format=text'
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(urls[kind], timeout=20) as r:
                text = await r.text()
                text = clean_response(text)
                text = add_watermark(text)
                await update.message.reply_text(text[:4000])
    except Exception as e:
        await update.message.reply_text(f'Error: {e}')


async def num(update, context):
    await lookup_cmd(update, context, 'num')


async def vahan(update, context):
    await lookup_cmd(update, context, 'vahan')


async def ifsc(update, context):
    await lookup_cmd(update, context, 'ifsc')


# ---------- ADMIN ----------
async def admin(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        return
    await q.edit_message_text('👑 Admin Panel', reply_markup=admin_kb())


async def pending(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        return
    c = conn()
    cur = c.cursor()
    cur.execute(db.param(
        "SELECT p.*, u.username, u.first_name FROM payments p "
        "LEFT JOIN users u ON u.user_id=p.user_id "
        "WHERE p.status='pending' ORDER BY p.id LIMIT 10"
    ))
    rows = cur.fetchall()
    cur.close()
    c.close()
    if not rows:
        await q.edit_message_text('⏳ No pending payments.', reply_markup=admin_kb())
        return
    await q.edit_message_text('⏳ Pending payments:')
    for r in rows:
        name = '@' + r['username'] if r['username'] else (r['first_name'] or 'User')
        k = InlineKeyboardMarkup([
            [InlineKeyboardButton('✅ APPROVE', callback_data=f"approve:{r['id']}"),
             InlineKeyboardButton('❌ REJECT', callback_data=f"reject:{r['id']}")]
        ])
        await q.message.reply_text(
            f"💳 #{r['id']}\nUser: {name}\nTelegram ID: {r['user_id']}\nAmount: ₹{r['amount']}\nUTR: {r['utr']}\nSubmitted: {r['created_at']}",
            reply_markup=k
        )


async def users(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        return
    c = conn()
    cur = c.cursor()
    cur.execute('SELECT COUNT(*) AS c FROM users')
    total = cur.fetchone()['c']
    cur.execute(db.param('SELECT COUNT(*) AS c FROM users WHERE premium_until > ?'), (iso(),))
    prem = cur.fetchone()['c']
    cur.execute("SELECT COUNT(*) AS c FROM payments WHERE status='pending'")
    pend = cur.fetchone()['c']
    cur.close()
    c.close()
    await q.edit_message_text(
        f'👥 Users: {total}\n💎 Active Premium: {prem}\n⏳ Pending: {pend}',
        reply_markup=admin_kb()
    )


async def decision(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        return
    action, pid = q.data.split(':')
    pid = int(pid)
    c = conn()
    cur = c.cursor()
    cur.execute(db.param('SELECT * FROM payments WHERE id=?'), (pid,))
    r = cur.fetchone()
    if not r:
        cur.close()
        c.close()
        await q.edit_message_text('❌ Payment not found.')
        return
    if r['status'] != 'pending':
        cur.close()
        c.close()
        await q.edit_message_text(f"ℹ️ Already {r['status']}.")
        return
    status = 'approved' if action == 'approve' else 'rejected'
    cur.execute(db.param('UPDATE payments SET status=?, verified_at=? WHERE id=?'),
                (status, iso(), pid))
    if status == 'approved':
        cur.execute(db.param('UPDATE users SET wallet=wallet+? WHERE user_id=?'),
                    (r['amount'], r['user_id']))
        cur.execute(db.param("INSERT INTO transactions(user_id,type,amount,note,created_at) VALUES(?,?,?,?,?)"),
                    (r['user_id'], 'wallet_credit', r['amount'], f'Payment #{pid}', iso()))
    c.commit()
    cur.close()
    c.close()
    await q.edit_message_text(f"{'✅' if status == 'approved' else '❌'} Payment #{pid} {status}.")
    try:
        msg = ('✅ Payment verified. ₹100 added to wallet. Tap Buy Premium.'
               if status == 'approved'
               else '❌ Payment request rejected. Contact admin if you think this is an error.')
        await context.bot.send_message(r['user_id'], msg, reply_markup=kb(r['user_id']))
    except Exception:
        pass


# ---------- CALLBACK ROUTER ----------
async def callbacks(update, context):
    d = update.callback_query.data
    if d == 'home':
        await home(update, context)
    elif d == 'pay':
        await pay(update, context)
    elif d == 'utr':
        await utr_start(update, context)
    elif d == 'wallet':
        await wallet(update, context)
    elif d == 'buy':
        await buy(update, context)
    elif d == 'premium':
        q = update.callback_query
        await q.answer()
        ensure(q.from_user)
        r = user(q.from_user.id)
        await q.edit_message_text(
            f'💎 Premium\n\n₹100 / 30 days\nWallet: ₹{r["wallet"]}',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('💎 Buy Premium ₹100', callback_data='buy')],
                [InlineKeyboardButton('💳 Add ₹100', callback_data='pay')],
                [InlineKeyboardButton('⬅️ Back', callback_data='home')]
            ])
        )
    elif d == 'status':
        await home(update, context)
    elif d == 'admin':
        await admin(update, context)
    elif d == 'pending':
        await pending(update, context)
    elif d == 'users':
        await users(update, context)
    elif d == 'lookup_menu':
        await lookup_menu(update, context)
    elif d == 'lookup_num':
        await lookup_num_start(update, context)
    elif d == 'lookup_vahan':
        await lookup_vahan_start(update, context)
    elif d == 'lookup_ifsc':
        await lookup_ifsc_start(update, context)
    elif d.startswith('approve:') or d.startswith('reject:'):
        await decision(update, context)


# ---------- HEALTH CHECK SERVER ----------
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')

    def log_message(self, format, *args):
        pass


def run_health_server():
    port = int(os.getenv('HEALTH_PORT', PORT))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    server.serve_forever()


# ---------- MAIN ----------
def main():
    db.init()
    app = Application.builder().token(TOKEN).build()

    # Slash commands
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('status', status))
    app.add_handler(CommandHandler('num', num))
    app.add_handler(CommandHandler('vahan', vahan))
    app.add_handler(CommandHandler('ifsc', ifsc))

    # Callback buttons
    app.add_handler(CallbackQueryHandler(callbacks))

    # Text messages
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
        print('RENDER_EXTERNAL_URL not set, running in polling mode (local only)')
        app.run_polling()


if __name__ == '__main__':
    main()
