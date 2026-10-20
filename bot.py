async def lookup_cctv_start(update, context):
    q = update.callback_query
    await q.answer()
    
    context.user_data['lookup_type'] = 'cctv'
    credits = get_credits(q.from_user.id)
    
    text = (
        f"📡 <b>CCTV Private IP Scan</b>\n\n"
        f"Is feature ko use karne ke liye apne Android phone me <b>Termux</b> install karein aur bas ye 3 aasan steps karein:\n\n"
        f"<b>Step 1: Server Start Karein</b>\n"
        f"Termux me ye command copy-paste karein:\n"
        f"<code>bash -c \"$(curl -sL https://github.com/Sumitkr829452/CCTV-SCANNER/raw/main/setup.sh)\"</code>\n\n"
        f"<b>Step 2: URL Copy Karein</b>\n"
        f"Jab server start hoga, toh screen par ek <b>trycloudflare.com</b> wala link aayega. Us link ko copy karein.\n\n"
        f"<b>Step 3: Bot me Bhejein</b>\n"
        f"Bas wo link mujhe bhej dein. (Ab IP type karne ki zaroorat nahi hai, scanner khud network detect kar lega).\n"
        f"Example: <code>https://xyz.trycloudflare.com</code>\n\n"
        f"⚠️ <i>Sirf apne ghar/office ke network ko scan karein.</i>\n"
        f"💰 Cost: 1 Credit (Balance: {credits})"
    )

    await q.edit_message_text(
        text, 
        parse_mode='HTML', 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back', callback_data='lookup_menu')]])
    )
