"""
AnimeVerse Upload Bot — Caption Auto-Detect Mode
=================================================
Install:  pip install pyTelegramBotAPI firebase-admin flask
Run:      python bot.py

Flow:
  1. /setup anime-id S1   → anime aur season set karo (ek baar)
  2. Saari files ek saath forward karo (kisi bhi order mein)
  3. Bot caption se Episode + Quality auto-detect karega
  4. Same episode ki files group → Storage → Firebase save
  5. /done → sab complete hone pe confirm karo
"""

import re
import os
import json
import time
import threading
import telebot
import firebase_admin
from firebase_admin import credentials, db
from flask import Flask

# ══════════════════════════════════════════════════════
#   SETTINGS — Sirf yahan apna data daalo
# ══════════════════════════════════════════════════════

BOT_TOKEN       = os.environ.get("BOT_TOKEN", "")
BOT_USERNAME    = "D0file_Bot"         # @ke bina
ALLOWED_USER    = 7373324949

STORAGE_CHANNEL = -1003963251495
FIREBASE_URL    = "https://animeverse-9eada-default-rtdb.firebaseio.com/"

# Kitni qualities per episode? (3 = 480p+720p+1080p)
# Jab yeh count pura ho → auto save
QUALITIES_PER_EP = 3

# ══════════════════════════════════════════════════════
#   FIREBASE INIT
# ══════════════════════════════════════════════════════

firebase_key_env = os.environ.get("FIREBASE_KEY")
if firebase_key_env:
    firebase_key = json.loads(firebase_key_env)
    cred = credentials.Certificate(firebase_key)
else:
    cred = credentials.Certificate("key.json")

firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_URL})

bot = telebot.TeleBot(BOT_TOKEN)

_cid = str(STORAGE_CHANNEL).replace("-100", "")
# Delivery link format: https://t.me/BOT_USERNAME?start=MSG_ID

# ══════════════════════════════════════════════════════
#   STATE
# ══════════════════════════════════════════════════════

session = {
    "anime_id" : None,
    "season"   : None,
    "done_eps" : 0,
}

# ep_buffer[ep_num] = [ {chat_id, msg_id, size, quality}, ... ]
ep_buffer = {}

def reset_all():
    session.update({"anime_id": None, "season": None, "done_eps": 0})
    ep_buffer.clear()

# ══════════════════════════════════════════════════════
#   FLASK WEB VIEW
# ══════════════════════════════════════════════════════

web = Flask(__name__)

@web.route("/")
def home():
    anime  = session.get("anime_id") or "—"
    season = session.get("season")   or "—"
    done   = session.get("done_eps", 0)
    buf    = len(ep_buffer)

    buf_rows = ""
    for ep_num in sorted(ep_buffer.keys()):
        count = len(ep_buffer[ep_num])
        buf_rows += f"<li>E{str(ep_num).zfill(2)}: {count}/{QUALITIES_PER_EP} files</li>"

    return f"""<!DOCTYPE html>
<html>
<head>
  <title>AnimeVerse Bot</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="refresh" content="10">
  <style>
    *{{margin:0;padding:0;box-sizing:border-box}}
    body{{background:#0a0a0f;color:#eee;font-family:monospace;padding:20px;min-height:100vh}}
    h1{{color:#00ff88;font-size:1.5em;margin-bottom:4px}}
    .badge{{display:inline-block;background:#00ff8820;color:#00ff88;
            border:1px solid #00ff8840;padding:3px 12px;border-radius:20px;
            font-size:11px;margin-bottom:24px}}
    .card{{background:#111318;border:1px solid #1e1e2e;border-radius:14px;
           padding:18px;margin-bottom:14px}}
    .card h3{{color:#555;font-size:10px;letter-spacing:2px;
              text-transform:uppercase;margin-bottom:12px}}
    .row{{display:flex;justify-content:space-between;align-items:center;
          padding:8px 0;border-bottom:1px solid #1a1a2e}}
    .row:last-child{{border-bottom:none}}
    .lbl{{color:#666;font-size:12px}}
    .val{{color:#fff;font-size:13px;font-weight:bold}}
    .green{{color:#00ff88}}
    .yellow{{color:#ffd700}}
    ul{{list-style:none}}
    ul li{{color:#aaa;font-size:12px;padding:6px 0;
           border-bottom:1px solid #1a1a2e}}
    ul li:last-child{{border-bottom:none}}
    .empty{{color:#333;font-size:12px;text-align:center;padding:12px}}
    footer{{color:#2a2a3a;font-size:10px;text-align:center;margin-top:24px}}
  </style>
</head>
<body>
  <h1>🤖 AnimeVerse Bot</h1>
  <div class="badge">● Running</div>
  <div class="card">
    <h3>Session</h3>
    <div class="row">
      <span class="lbl">📺 Anime</span>
      <span class="val">{anime}</span>
    </div>
    <div class="row">
      <span class="lbl">🎬 Season</span>
      <span class="val">{season}</span>
    </div>
    <div class="row">
      <span class="lbl">✅ Saved</span>
      <span class="val green">{done} episodes</span>
    </div>
    <div class="row">
      <span class="lbl">⏳ Buffer</span>
      <span class="val yellow">{buf} episodes</span>
    </div>
  </div>
  <div class="card">
    <h3>Buffer Details</h3>
    {"<ul>" + buf_rows + "</ul>" if buf_rows else '<div class="empty">Buffer khaali hai ✅</div>'}
  </div>
  <footer>@{BOT_USERNAME} &nbsp;·&nbsp; Auto refresh 10s</footer>
</body>
</html>"""

@web.route("/health")
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    web.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ══════════════════════════════════════════════════════
#   CAPTION PARSER
# ══════════════════════════════════════════════════════

def parse_caption(text: str):
    """
    Caption/filename se episode number aur quality nikaalega.

    Episode detect priority:
      1. EPISODE/EP keyword ke baad number  → "EPISODE - 07", "EP04"
      2. E + number                         → "E09", "E40"
      3. Standalone 1-2 digit number        → "07", "11", "40"

    Quality detect:
      480p / 720p / 1080p → direct use
      Nahi mila → size se fallback (caller mein)

    Returns: (ep_num: int or None, quality: str or None)
    """
    if not text:
        return None, None

    t = text.upper()

    # --- Episode detect ---
    ep_num = None

    # Priority 1: EPISODE/EP keyword ke baad number
    ep_match = re.search(r'\bEP(?:ISODE)?\s*[-:→►\s]*\s*(\d{1,3})\b', t)
    if ep_match:
        ep_num = int(ep_match.group(1))

    # Priority 2: E + number (E09, E40)
    if not ep_num:
        e_match = re.search(r'\bE(\d{1,3})\b', t)
        if e_match:
            ep_num = int(e_match.group(1))

    # Priority 3: standalone 1-2 digit number (season number remove karke)
    if not ep_num:
        cleaned = re.sub(r'\bS\d{1,2}\b', '', t)
        nums = re.findall(r'\b(\d{1,2})\b', cleaned)
        if nums:
            ep_num = int(nums[0])

    # --- Quality detect ---
    quality = None
    q_match = re.search(r'\b(1080P|720P|480P)\b', t)
    if q_match:
        quality = q_match.group(1).replace("P", "p")

    return ep_num, quality

# ══════════════════════════════════════════════════════
#   FIREBASE
# ══════════════════════════════════════════════════════

def save_to_firebase(anime_id, season, ep_num, quality_dict):
    ep_key = f"E{ep_num}"
    if not quality_dict:
        print(f"  ⚠️ Empty dict — skip Firebase for {ep_key}")
        return ep_key
    db.reference(f"anime_links/{anime_id}/{season}/{ep_key}").update(quality_dict)
    print(f"  ✅ Firebase: anime_links/{anime_id}/{season}/{ep_key}")
    return ep_key

# ══════════════════════════════════════════════════════
#   STORAGE FORWARD
# ══════════════════════════════════════════════════════

def forward_to_storage(from_chat_id, msg_id, new_caption):
    try:
        sent = bot.copy_message(
            chat_id      = STORAGE_CHANNEL,
            from_chat_id = from_chat_id,
            message_id   = msg_id,
            caption      = new_caption,
        )
        # Link format: https://t.me/BotUsername?start=MSG_ID
        return f"https://t.me/{BOT_USERNAME}?start={sent.message_id}"
    except Exception as e:
        print(f"  ❌ Forward error: {e}")
        return None

# ══════════════════════════════════════════════════════
#   PROCESS EPISODE
# ══════════════════════════════════════════════════════

def process_ep(chat_id, ep_num, files):
    anime_id = session["anime_id"]
    season   = session["season"]
    ep_key   = f"E{ep_num}"

    # Size ke hisaab se sort — chota=480p, beech=720p, bada=1080p
    sorted_files = sorted(files, key=lambda x: x["size"])
    quality_map = {0: "480p", 1: "720p", 2: "1080p"}
    for i, f in enumerate(sorted_files):
        f["quality"] = quality_map.get(i, f"part{i+1}")

    quality_dict = {}
    for f in files:
        quality = f["quality"]
        size_mb = round(f["size"] / (1024 * 1024), 1)

        caption = (
            f"🎌 {anime_id}\n"
            f"📺 {season} | {ep_key} | {quality} | {size_mb}MB\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )

        link = forward_to_storage(f["chat_id"], f["msg_id"], caption)
        if link:
            quality_dict[quality] = link

    saved_key = save_to_firebase(anime_id, season, ep_num, quality_dict)
    session["done_eps"] += 1

    if quality_dict:
        q_lines = "\n".join([f"  • {q}: ✅" for q in quality_dict])
        bot.send_message(chat_id, f"""
✅ *{saved_key} Saved!*
{q_lines}
🔗 `anime_links/{anime_id}/{season}/{saved_key}`
""", parse_mode="Markdown")
    else:
        bot.send_message(chat_id, f"""
❌ *{saved_key} Failed!*
Bot ko storage channel ka *Admin* banao!
""", parse_mode="Markdown")

# ══════════════════════════════════════════════════════
#   COMMANDS
# ══════════════════════════════════════════════════════

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    args = msg.text.split()

    # User ne ?start=MSG_ID se open kiya → file deliver karo
    if len(args) > 1:
        try:
            msg_id = int(args[1])
            bot.copy_message(
                chat_id      = msg.chat.id,
                from_chat_id = STORAGE_CHANNEL,
                message_id   = msg_id,
            )
        except Exception as e:
            print(f"Delivery error: {e}")
            bot.reply_to(msg, "❌ File nahi mili. Link expire ho gaya ya galat hai.")
        return

    # Admin ka /start — help dikhao
    if msg.from_user.id == ALLOWED_USER:
        bot.reply_to(msg, """
🎌 *AnimeVerse Upload Bot v2*
━━━━━━━━━━━━━━━━━━━━━━━━━

*Step 1:* `/setup anime-id S1`
*Step 2:* Saari files forward karo ek saath
*Step 3:* `/done` jab sab bhej do

━━━━━━━━━━━━━━━━━━━━━━━━━
*Other commands:*
📋 `/status` — buffer dekho
🔍 `/check anime-id S1 5`
🗑 `/delete anime-id S1 5`
🔄 `/reset`
""", parse_mode="Markdown")


@bot.message_handler(commands=["help"])
def cmd_help(msg):
    if msg.from_user.id != ALLOWED_USER:
        return
    bot.reply_to(msg, "📌 Commands: /setup /status /done /reset /check /delete")


@bot.message_handler(commands=["setup"])
def cmd_setup(msg):
    if msg.from_user.id != ALLOWED_USER:
        return
    try:
        parts    = msg.text.split()
        anime_id = parts[1]
        season   = parts[2].upper()
        reset_all()
        session["anime_id"] = anime_id
        session["season"]   = season
        bot.reply_to(msg, f"""
✅ *Setup Done!*
📺 Anime: `{anime_id}`
🎬 Season: `{season}`

Ab saari files ek saath forward karo! 🚀
Bot caption dekh ke khud group karega.
""", parse_mode="Markdown")
    except:
        bot.reply_to(msg, "❌ Format: `/setup anime-id S1`", parse_mode="Markdown")


@bot.message_handler(commands=["done"])
def cmd_done(msg):
    if msg.from_user.id != ALLOWED_USER:
        return
    pending = list(ep_buffer.keys())
    if pending:
        bot.reply_to(msg, f"⚙️ *{len(pending)} pending episodes process ho rahe hain...*",
                     parse_mode="Markdown")
        for ep_num in sorted(pending):
            files = ep_buffer.pop(ep_num)
            process_ep(msg.chat.id, ep_num, files)

    total = session["done_eps"]
    bot.send_message(msg.chat.id, f"""
🏁 *Sab Complete!*
✅ *{total} episodes* Firebase mein save!
📺 `{session['anime_id']}` | `{session['season']}`

Naya season ke liye `/setup` karo.
""", parse_mode="Markdown")


@bot.message_handler(commands=["status"])
def cmd_status(msg):
    if msg.from_user.id != ALLOWED_USER:
        return
    if not session["anime_id"]:
        bot.reply_to(msg, "ℹ️ Koi session nahi.\n`/setup anime-id S1` se shuru karo.")
        return
    lines = [
        f"📋 *Status:*\n━━━━━━━━━━━━━━━━━━━━",
        f"📺 `{session['anime_id']}` | `{session['season']}`",
        f"✅ Saved: `{session['done_eps']} episodes`",
        f"⏳ Buffer: `{len(ep_buffer)} episodes`\n"
    ]
    for ep_num in sorted(ep_buffer.keys()):
        files = ep_buffer[ep_num]
        quals = [f["quality"] for f in files]
        lines.append(f"  E{ep_num}: {', '.join(quals)} ({len(files)}/{QUALITIES_PER_EP})")
    bot.reply_to(msg, "\n".join(lines), parse_mode="Markdown")


@bot.message_handler(commands=["reset"])
def cmd_reset(msg):
    if msg.from_user.id != ALLOWED_USER:
        return
    reset_all()
    bot.reply_to(msg, "🔄 *Reset done!* `/setup anime-id S1` se shuru karo.", parse_mode="Markdown")


@bot.message_handler(commands=["check"])
def cmd_check(msg):
    if msg.from_user.id != ALLOWED_USER:
        return
    try:
        parts    = msg.text.split()
        anime_id = parts[1]
        season   = parts[2].upper()
        ep_num   = str(parts[3])
        data = db.reference(f"anime_links/{anime_id}/{season}/E{ep_num}").get()
        if data:
            lines = [f"📊 *{anime_id} | {season} | E{ep_num}*\n━━━━━━━━━━━━━━━━"]
            for q, link in data.items():
                lines.append(f"• {q}: `{str(link)[:55]}`")
            bot.reply_to(msg, "\n".join(lines), parse_mode="Markdown")
        else:
            bot.reply_to(msg, f"❌ E{ep_num} Firebase mein nahi mila")
    except:
        bot.reply_to(msg, "❌ Format: `/check anime-id S1 5`", parse_mode="Markdown")


@bot.message_handler(commands=["delete"])
def cmd_delete(msg):
    if msg.from_user.id != ALLOWED_USER:
        return
    try:
        parts    = msg.text.split()
        anime_id = parts[1]
        season   = parts[2].upper()
        ep_num   = str(parts[3]).zfill(2)
        db.reference(f"anime_links/{anime_id}/{season}/E{ep_num}").delete()
        bot.reply_to(msg, f"🗑 *Deleted:* `E{ep_num}`\nPath: `{anime_id}/{season}/E{ep_num}`",
                     parse_mode="Markdown")
    except:
        bot.reply_to(msg, "❌ Format: `/delete anime-id S1 5`", parse_mode="Markdown")

# ══════════════════════════════════════════════════════
#   FILE HANDLER
# ══════════════════════════════════════════════════════

@bot.message_handler(content_types=["document", "video"])
def handle_file(msg):
    if msg.from_user.id != ALLOWED_USER:
        return

    if not session["anime_id"]:
        bot.reply_to(msg, "❌ Pehle `/setup anime-id S1` karo!", parse_mode="Markdown")
        return

    file_obj  = msg.document or msg.video
    file_size = file_obj.file_size or 0
    file_name = getattr(file_obj, "file_name", None) or "video"
    caption   = msg.caption or ""

    # Sirf episode number detect karo (quality size se assign hogi)
    ep_num, _ = parse_caption(caption)
    if not ep_num:
        ep_num, _ = parse_caption(file_name)

    if not ep_num:
        bot.reply_to(msg, f"⚠️ *Episode detect nahi hua!*\nCaption: `{caption[:80]}`\nCaption mein `Episode - 04` ya `E04` hona chahiye.", parse_mode="Markdown")
        return

    quality = "pending"  # Size se assign hogi process_ep mein

    # Buffer mein add
    if ep_num not in ep_buffer:
        ep_buffer[ep_num] = []

    ep_key = f"E{ep_num}"

    # Duplicate file check — same size ki file dobara aayi?
    existing_sizes = [f["size"] for f in ep_buffer[ep_num]]
    if file_size in existing_sizes:
        bot.reply_to(msg, f"⚠️ *{ep_key} — Same file dobara aai! Skip kar raha hoon.*", parse_mode="Markdown")
        return

    ep_buffer[ep_num].append({
        "chat_id": msg.chat.id,
        "msg_id" : msg.message_id,
        "size"   : file_size,
        "quality": quality,
        "name"   : file_name,
    })

    count   = len(ep_buffer[ep_num])
    size_mb = round(file_size / (1024 * 1024), 1)

    if count >= QUALITIES_PER_EP:
        bot.reply_to(msg, f"""
📥 `{quality}` | `{size_mb}MB`
⚙️ *{ep_key} complete! Save ho raha hai...*
""", parse_mode="Markdown")
        files = ep_buffer.pop(ep_num)
        process_ep(msg.chat.id, ep_num, files)
    else:
        remaining = QUALITIES_PER_EP - count
        bot.reply_to(msg, f"""
📥 `{quality}` | `{size_mb}MB`
📦 *{ep_key}:* {count}/{QUALITIES_PER_EP} | aur *{remaining}* chahiye
""", parse_mode="Markdown")

# ══════════════════════════════════════════════════════
#   RUN — Flask + Bot
# ══════════════════════════════════════════════════════

print("=" * 50)
print("  🤖 AnimeVerse Bot v2 — Caption Mode")
print(f"  📦 Storage: t.me/c/{_cid}/")
print("  Ctrl+C se band karo")
print("=" * 50)

flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()
print("  Web server started")

while True:
    try:
        print("  Bot polling shuru...")
        bot.polling(
            none_stop=True,
            interval=1,
            timeout=60,
            long_polling_timeout=60
        )
    except Exception as e:
        print(f"  Crash: {e}")
        print("  10 sec mein restart...")
        time.sleep(10)
    
