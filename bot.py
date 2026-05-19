"""
AnimeVerse Upload Bot v3 — Caption Mode + Web View
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
#   SETTINGS
# ══════════════════════════════════════════════════════

BOT_TOKEN        = os.environ.get("BOT_TOKEN", "")
BOT_USERNAME     = "D0file_Bot"
ALLOWED_USER     = 7373324949
STORAGE_CHANNEL  = -1003963251495
FIREBASE_URL     = "https://animeverse-9eada-default-rtdb.firebaseio.com/"
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

# ══════════════════════════════════════════════════════
#   STATE
# ══════════════════════════════════════════════════════

session   = {"anime_id": None, "season": None, "done_eps": 0}
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
    if not text:
        return None, None
    t = text.upper()
    ep_num = None

    ep_match = re.search(r'\bEP(?:ISODE)?\s*[-:→►\s]*\s*(\d{1,3})\b', t)
    if ep_match:
        ep_num = int(ep_match.group(1))

    if not ep_num:
        e_match = re.search(r'\bE(\d{1,3})\b', t)
        if e_match:
            ep_num = int(e_match.group(1))

    if not ep_num:
        cleaned = re.sub(r'\bS\d{1,2}\b', '', t)
        nums = re.findall(r'\b(\d{1,2})\b', cleaned)
        if nums:
            ep_num = int(nums[0])

    return ep_num, None

# ══════════════════════════════════════════════════════
#   FIREBASE
# ══════════════════════════════════════════════════════

def save_to_firebase(anime_id, season, ep_num, quality_dict):
    ep_key = f"E{str(ep_num).zfill(2)}"
    if not quality_dict:
        print(f"  Empty dict — skip {ep_key}")
        return ep_key
    db.reference(f"anime_links/{anime_id}/{season}/{ep_key}").update(quality_dict)
    print(f"  Firebase saved: anime_links/{anime_id}/{season}/{ep_key}")
    return ep_key

# ══════════════════════════════════════════════════════
#   STORAGE FORWARD
# ══════════════════════════════════════════════════════

def forward_to_storage(from_chat_id, msg_id, new_caption):
    try:
        sent = bot.copy_message(
            chat_id=STORAGE_CHANNEL,
            from_chat_id=from_chat_id,
            message_id=msg_id,
            caption=new_caption,
        )
        return f"https://t.me/{BOT_USERNAME}?start={sent.message_id}"
    except Exception as e:
        print(f"  Forward error: {e}")
        return None

# ══════════════════════════════════════════════════════
#   PROCESS EPISODE
# ══════════════════════════════════════════════════════

def process_ep(chat_id, ep_num, files):
    anime_id = session["anime_id"]
    season   = session["season"]
    ep_key   = f"E{str(ep_num).zfill(2)}"

    sorted_files = sorted(files, key=lambda x: x["size"])
    quality_map  = {0: "480p", 1: "720p", 2: "1080p"}
    for i, f in enumerate(sorted_files):
        f["quality"] = quality_map.get(i, f"part{i+1}")

    quality_dict = {}
    for f in sorted_files:
        size_mb = round(f["size"] / (1024 * 1024), 1)
        cap = (
            f"Anime: {anime_id}\n"
            f"Season: {season} | {ep_key} | {f['quality']} | {size_mb}MB"
        )
        link = forward_to_storage(f["chat_id"], f["msg_id"], cap)
        if link:
            quality_dict[f["quality"]] = link

    saved_key = save_to_firebase(anime_id, season, ep_num, quality_dict)
    session["done_eps"] += 1

    if quality_dict:
        q_lines = "\n".join([f"  {q}: OK" for q in quality_dict])
        bot.send_message(chat_id,
            f"Saved: {saved_key}\n{q_lines}\nPath: anime_links/{anime_id}/{season}/{saved_key}")
    else:
        bot.send_message(chat_id,
            f"Failed: {saved_key}\nBot ko storage channel ka Admin banao!")

# ══════════════════════════════════════════════════════
#   COMMANDS
# ══════════════════════════════════════════════════════

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    args = msg.text.split()
    if len(args) > 1:
        try:
            msg_id = int(args[1])
            bot.copy_message(
                chat_id=msg.chat.id,
                from_chat_id=STORAGE_CHANNEL,
                message_id=msg_id,
            )
        except Exception as e:
            print(f"Delivery error: {e}")
            bot.reply_to(msg, "File nahi mili. Link expire ho gaya ya galat hai.")
        return

    if msg.from_user.id == ALLOWED_USER:
        bot.reply_to(msg,
            "AnimeVerse Bot v3\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Admin ID: {ALLOWED_USER}\n"
            f"Bot: @{BOT_USERNAME}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Commands:\n"
            "/setup anime-id S1 — Season shuru\n"
            "/done — Sab process karo\n"
            "/status — Buffer dekho\n"
            "/check anime-id S1 5 — Firebase check\n"
            "/delete anime-id S1 5 — Episode delete\n"
            "/reset — Sab clear"
        )
    else:
        bot.reply_to(msg, "AnimeVerse me aapka swagat hai!")


@bot.message_handler(commands=["help"])
def cmd_help(msg):
    if msg.from_user.id != ALLOWED_USER:
        return
    bot.reply_to(msg, "Commands: /setup /status /done /reset /check /delete")


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
        bot.reply_to(msg,
            f"Setup Done!\n"
            f"Anime: {anime_id}\n"
            f"Season: {season}\n\n"
            f"Ab saari files forward karo!\n"
            f"Jab ho jaaye: /done"
        )
    except:
        bot.reply_to(msg, "Format: /setup anime-id S1")


@bot.message_handler(commands=["done"])
def cmd_done(msg):
    if msg.from_user.id != ALLOWED_USER:
        return
    pending = list(ep_buffer.keys())
    if pending:
        bot.reply_to(msg, f"{len(pending)} pending episodes process ho rahe hain...")
        for ep_num in sorted(pending):
            files = ep_buffer.pop(ep_num)
            process_ep(msg.chat.id, ep_num, files)
    total = session["done_eps"]
    bot.send_message(msg.chat.id,
        f"Sab Complete!\n"
        f"{total} episodes Firebase mein save!\n"
        f"Anime: {session.get('anime_id','—')} | Season: {session.get('season','—')}\n\n"
        f"Naya season: /setup anime-id S2"
    )


@bot.message_handler(commands=["status"])
def cmd_status(msg):
    if msg.from_user.id != ALLOWED_USER:
        return
    if not session["anime_id"]:
        bot.reply_to(msg, "Koi session nahi. /setup anime-id S1 se shuru karo.")
        return
    lines = [
        f"Status:",
        f"Anime: {session['anime_id']} | Season: {session['season']}",
        f"Saved: {session['done_eps']} episodes",
        f"Buffer: {len(ep_buffer)} episodes",
    ]
    for ep_num in sorted(ep_buffer.keys()):
        files = ep_buffer[ep_num]
        lines.append(f"  E{str(ep_num).zfill(2)}: {len(files)}/{QUALITIES_PER_EP} files")
    bot.reply_to(msg, "\n".join(lines))


@bot.message_handler(commands=["reset"])
def cmd_reset(msg):
    if msg.from_user.id != ALLOWED_USER:
        return
    reset_all()
    bot.reply_to(msg, "Reset done!")


@bot.message_handler(commands=["check"])
def cmd_check(msg):
    if msg.from_user.id != ALLOWED_USER:
        return
    try:
        parts    = msg.text.split()
        anime_id = parts[1]
        season   = parts[2].upper()
        ep_num   = str(parts[3]).zfill(2)
        data = db.reference(f"anime_links/{anime_id}/{season}/E{ep_num}").get()
        if data:
            lines = [f"Firebase: {anime_id} | {season} | E{ep_num}"]
            for q, link in data.items():
                lines.append(f"{q}: {str(link)[:55]}")
            bot.reply_to(msg, "\n".join(lines))
        else:
            bot.reply_to(msg, f"E{ep_num} Firebase mein nahi mila")
    except:
        bot.reply_to(msg, "Format: /check anime-id S1 5")


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
        bot.reply_to(msg, f"Deleted: E{ep_num}\nPath: {anime_id}/{season}/E{ep_num}")
    except:
        bot.reply_to(msg, "Format: /delete anime-id S1 5")

# ══════════════════════════════════════════════════════
#   FILE HANDLER
# ══════════════════════════════════════════════════════

@bot.message_handler(content_types=["document", "video"])
def handle_file(msg):
    if msg.from_user.id != ALLOWED_USER:
        return
    if not session["anime_id"]:
        bot.reply_to(msg, "Pehle /setup anime-id S1 karo!")
        return

    file_obj  = msg.document or msg.video
    file_size = file_obj.file_size or 0
    file_name = getattr(file_obj, "file_name", None) or "video"
    caption   = msg.caption or ""

    ep_num, _ = parse_caption(caption)
    if not ep_num:
        ep_num, _ = parse_caption(file_name)

    if not ep_num:
        bot.reply_to(msg,
            f"Episode detect nahi hua!\n"
            f"Caption: {caption[:80]}\n"
            f"File: {file_name[:80]}\n\n"
            f"Caption mein Episode - 04 ya E04 hona chahiye."
        )
        return

    ep_key = f"E{str(ep_num).zfill(2)}"

    if ep_num not in ep_buffer:
        ep_buffer[ep_num] = []

    existing_sizes = [f["size"] for f in ep_buffer[ep_num]]
    if file_size in existing_sizes:
        bot.reply_to(msg, f"{ep_key} — Same file dobara aai! Skip.")
        return

    ep_buffer[ep_num].append({
        "chat_id": msg.chat.id,
        "msg_id" : msg.message_id,
        "size"   : file_size,
        "quality": "pending",
        "name"   : file_name,
    })

    count     = len(ep_buffer[ep_num])
    size_mb   = round(file_size / (1024 * 1024), 1)
    remaining = QUALITIES_PER_EP - count

    if count >= QUALITIES_PER_EP:
        bot.reply_to(msg,
            f"File: {file_name[:25]} | {size_mb}MB\n"
            f"{ep_key} complete! Save ho raha hai..."
        )
        files = ep_buffer.pop(ep_num)
        process_ep(msg.chat.id, ep_num, files)
    else:
        bot.reply_to(msg,
            f"File: {file_name[:25]} | {size_mb}MB\n"
            f"{ep_key}: {count}/{QUALITIES_PER_EP} | aur {remaining} chahiye"
        )

# ══════════════════════════════════════════════════════
#   RUN — Flask + Bot
# ══════════════════════════════════════════════════════

print("=" * 50)
print("  AnimeVerse Bot v3 Starting...")
print(f"  Storage: {STORAGE_CHANNEL}")
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
            interval=2,
            timeout=60,
            long_polling_timeout=60
        )
    except Exception as e:
        print(f"  Crash: {e}")
        print("  10 sec mein restart...")
        time.sleep(10)
