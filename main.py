import os
import io
import tempfile
import asyncio
import sqlite3
from datetime import datetime, timezone
from typing import Optional, List, Tuple

import discord
from discord import option
from discord.ext import commands
import gdown

# -------------------- CONFIG --------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
ADMIN_USER_IDS = {
    # ใส่ Discord User ID ของแอดมิน เช่น
    # 123456789012345678,
}

INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.members = False
INTENTS.message_content = False

DB_PATH = os.getenv("DB_PATH", "shopbot.db")

# -------------------- DB LAYER --------------------
def db_init():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id INTEGER UNIQUE NOT NULL,
            balance_cents INTEGER NOT NULL DEFAULT 0
        );
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            drive_id TEXT NOT NULL,
            price_cents INTEGER NOT NULL
        );
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            drive_id TEXT NOT NULL,
            price_cents INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
    """)
    conn.commit()
    conn.close()

def db_conn():
    return sqlite3.connect(DB_PATH)

def get_or_create_user(discord_id: int) -> Tuple[int, int]:
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT id, balance_cents FROM users WHERE discord_id=?", (discord_id,))
    row = c.fetchone()
    if row:
        conn.close()
        return row[0], row[1]
    c.execute("INSERT INTO users (discord_id, balance_cents) VALUES (?, 0)", (discord_id,))
    conn.commit()
    user_id = c.lastrowid
    conn.close()
    return user_id, 0

def add_funds(discord_id: int, amount_cents: int) -> int:
    user_id, _ = get_or_create_user(discord_id)
    conn = db_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET balance_cents = balance_cents + ? WHERE id=?", (amount_cents, user_id))
    conn.commit()
    c.execute("SELECT balance_cents FROM users WHERE id=?", (user_id,))
    balance = c.fetchone()[0]
    conn.close()
    return balance

def get_balance(discord_id: int) -> int:
    _, bal = get_or_create_user(discord_id)
    return bal

def set_item(name: str, drive_id: str, price_cents: int):
    conn = db_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO catalog (name, drive_id, price_cents)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET drive_id=excluded.drive_id, price_cents=excluded.price_cents;
    """, (name, drive_id, price_cents))
    conn.commit()
    conn.close()

def remove_item(name: str) -> bool:
    conn = db_conn()
    c = conn.cursor()
    c.execute("DELETE FROM catalog WHERE name=?", (name,))
    changed = c.rowcount
    conn.commit()
    conn.close()
    return changed > 0

def list_items() -> List[Tuple[str, str, int]]:
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT name, drive_id, price_cents FROM catalog ORDER BY name ASC")
    rows = c.fetchall()
    conn.close()
    return rows

def start_purchase(discord_id: int, item_name: str) -> Tuple[bool, str, Optional[str], Optional[int], Optional[int]]:
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT id, balance_cents FROM users WHERE discord_id=?", (discord_id,))
    u = c.fetchone()
    if not u:
        conn2 = db_conn()
        conn2.execute("INSERT INTO users (discord_id, balance_cents) VALUES (?, 0)", (discord_id,))
        conn2.commit()
        conn2.close()
        user_id = db_conn().execute("SELECT id FROM users WHERE discord_id=?", (discord_id,)).fetchone()[0]
        balance_cents = 0
    else:
        user_id, balance_cents = u

    c.execute("SELECT drive_id, price_cents FROM catalog WHERE name=?", (item_name,))
    item = c.fetchone()
    if not item:
        conn.close()
        return False, f"ไม่พบรายการ `{item_name}`", None, None, None

    drive_id, price_cents = item
    if balance_cents < price_cents:
        conn.close()
        needed = price_cents - balance_cents
        return False, f"ยอดเงินไม่พอ ต้องการเพิ่มอีก {needed/100:.2f} บาท", None, price_cents, balance_cents

    new_balance = balance_cents - price_cents
    c.execute("UPDATE users SET balance_cents=? WHERE id=?", (new_balance, user_id))
    c.execute("""
        INSERT INTO purchases (user_id, item_name, drive_id, price_cents, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, item_name, drive_id, price_cents, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return True, "ชำระเงินสำเร็จ", drive_id, price_cents, new_balance

def get_history(discord_id: int) -> List[Tuple[str, int, str]]:
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE discord_id=?", (discord_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return []
    user_id = row[0]
    c.execute("""
        SELECT item_name, price_cents, created_at
        FROM purchases
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 50
    """, (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

# -------------------- UTIL --------------------
def is_admin(user: discord.abc.User) -> bool:
    return user.id in ADMIN_USER_IDS

def cents_fmt(v: int) -> str:
    return f"{v/100:.2f} บาท"

def drive_id_from_link(link_or_id: str) -> str:
    if "drive.google.com" not in link_or_id:
        return link_or_id.strip()
    parts = link_or_id.split("/")
    if "file" in parts and "d" in parts:
        try:
            i = parts.index("d")
            return parts[i+1]
        except Exception:
            pass
    if "id=" in link_or_id:
        return link_or_id.split("id=")[-1].split("&")[0]
    return link_or_id.strip()

async def download_drive_to_temp_mp4(drive_id: str) -> str:
    tmpdir = tempfile.mkdtemp(prefix="shopbot_")
    out_path = os.path.join(tmpdir, f"{drive_id}.mp4")
    url = f"https://drive.google.com/uc?id={drive_id}"
    gdown.download(url, out_path, quiet=True)
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError("ดาวน์โหลดไฟล์ไม่สำเร็จ (เช็คการแชร์ไฟล์/สิทธิ์/ขนาดไฟล์)")
    return out_path

# -------------------- BOT --------------------
bot = discord.Bot(intents=INTENTS)

@bot.event
async def on_ready():
    db_init()
    try:
        await bot.sync_commands()
    except Exception:
        pass
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")

@bot.slash_command(description="เช็คยอดเงินของคุณ")
async def balance(ctx: discord.ApplicationContext):
    bal = get_balance(ctx.author.id)
    await ctx.respond(f"ยอดเงินคงเหลือของคุณ: **{cents_fmt(bal)}**", ephemeral=True)

@bot.slash_command(description="ดูประวัติการซื้อของคุณ (ล่าสุด 50 รายการ)")
async def history(ctx: discord.ApplicationContext):
    rows = get_history(ctx.author.id)
    if not rows:
        await ctx.respond("ยังไม่มีประวัติการซื้อ", ephemeral=True)
        return
    lines = []
    for name, price, at in rows:
        timestr = datetime.fromisoformat(at).astimezone().strftime("%Y-%m-%d %H:%M")
        lines.append(f"• {timestr} — {name} — {cents_fmt(price)}")
    msg = "ประวัติการซื้อของคุณ:\n" + "\n".join(lines)
    if len(msg) > 1900:
        msg = msg[:1900] + "\n..."
    await ctx.respond(msg, ephemeral=True)

@bot.slash_command(description="[ADMIN] เติมเงินให้ผู้ใช้ (หน่วยบาท)")
@option("user", description="เลือกสมาชิก", required=True)
@option("amount_baht", description="จำนวนเงิน (บาท)", required=True)
async def add_funds_cmd(ctx: discord.ApplicationContext, user: discord.User, amount_baht: float):
    if not is_admin(ctx.author):
        await ctx.respond("คำสั่งนี้สำหรับแอดมินเท่านั้น", ephemeral=True)
        return
    cents = int(round(amount_baht * 100))
    newbal = add_funds(user.id, cents)
    await ctx.respond(f"เติมเงินให้ {user.mention} จำนวน {amount_baht:.2f} บาท เรียบร้อย\nยอดใหม่: **{cents_fmt(newbal)}**", ephemeral=True)

@bot.slash_command(description="[ADMIN] เพิ่ม/แก้ไขรายการ (ชื่อ, ราคา(บาท), Google Drive link/ID)")
@option("name", description="ชื่อรายการ", required=True)
@option("price_baht", description="ราคา (บาท)", required=True)
@option("drive_link_or_id", description="ลิงก์หรือ file id จาก Google Drive", required=True)
async def set_item_cmd(ctx: discord.ApplicationContext, name: str, price_baht: float, drive_link_or_id: str):
    if not is_admin(ctx.author):
        await ctx.respond("คำสั่งนี้สำหรับแอดมินเท่านั้น", ephemeral=True)
        return
    cents = int(round(price_baht * 100))
    did = drive_id_from_link(drive_link_or_id)
    set_item(name.strip(), did, cents)
    await ctx.respond(f"บันทึกรายการ **{name}** ราคา {price_baht:.2f} บาท (drive id: `{did}`) เรียบร้อย", ephemeral=True)

@bot.slash_command(description="[ADMIN] ลบรายการออกจากแคตตาล็อก")
@option("name", description="ชื่อรายการ", required=True)
async def remove_item_cmd(ctx: discord.ApplicationContext, name: str):
    if not is_admin(ctx.author):
        await ctx.respond("คำสั่งนี้สำหรับแอดมินเท่านั้น", ephemeral=True)
        return
    ok = remove_item(name.strip())
    if ok:
        await ctx.respond(f"ลบรายการ **{name}** เรียบร้อย", ephemeral=True)
    else:
        await ctx.respond(f"ไม่พบรายการ **{name}**", ephemeral=True)

@bot.slash_command(description="ดูรายการทั้งหมดในร้าน")
async def list_items_cmd(ctx: discord.ApplicationContext):
    items = list_items()
    if not items:
        await ctx.respond("ยังไม่มีรายการในร้าน", ephemeral=True)
        return
    lines = [f"• {name} — {cents_fmt(price)}" for (name, _, price) in items]
    await ctx.respond("รายการทั้งหมด:\n" + "\n".join(lines), ephemeral=True)

class ShopSelect(discord.ui.Select):
    def __init__(self):
        items = list_items()
        options = []
        for name, _, price in items[:25]:
            label = name[:100]
            desc = f"ราคา {cents_fmt(price)}"
            options.append(discord.SelectOption(label=label, description=desc, value=name))
        if not options:
            options = [discord.SelectOption(label="ยังไม่มีสินค้า", description="ให้แอดมินเพิ่มด้วย /set_item", value="__none__", default=True)]
        super().__init__(placeholder="เลือกรายการที่ต้องการซื้อ...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        if choice == "__none__":
            await interaction.response.send_message("ยังไม่มีสินค้าให้ซื้อ", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, msg, drive_id, price_cents, new_balance = start_purchase(interaction.user.id, choice)
        if not ok:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
            return

        try:
            path = await download_drive_to_temp_mp4(drive_id)
        except Exception as e:
            conn = db_conn()
            c = conn.cursor()
            c.execute("UPDATE users SET balance_cents = balance_cents + ? WHERE discord_id=?", (price_cents, interaction.user.id))
            conn.commit()
            conn.close()
            await interaction.followup.send(f"⚠️ ดาวน์โหลดไฟล์ไม่สำเร็จ: {e}\nได้ทำการคืนเงินแล้ว", ephemeral=True)
            return

        size = os.path.getsize(path)
        if size > 8 * 1024 * 1024:
            await interaction.followup.send(
                "⚠️ ไฟล์มีขนาดใหญ่กว่า 8MB อาจส่งไม่สำเร็จบนเซิร์ฟเวอร์ที่ไม่มี Nitro/Boost\n"
                "ให้แอดมินบีบอัด/ลดขนาดไฟล์ก่อน หรือส่งผ่านลิงก์แทน",
                ephemeral=True
            )

        try:
            await interaction.followup.send(
                content=f"✅ ชำระเงินสำเร็จ: **{choice}** — {cents_fmt(price_cents)}\nยอดเงินคงเหลือ: **{cents_fmt(new_balance)}**",
                ephemeral=True
            )
            await interaction.followup.send(
                content="นี่คือไฟล์ของคุณ:",
                file=discord.File(path, filename=f"{choice}.mp4"),
                ephemeral=True
            )
        except discord.HTTPException as e:
            await interaction.followup.send(f"⚠️ อัปโหลดไฟล์ล้มเหลว: {e}", ephemeral=True)

class ShopView(discord.ui.View):
    def __init__(self, timeout: Optional[float] = 180):
        super().__init__(timeout=timeout)
        self.add_item(ShopSelect())

@bot.slash_command(description="เปิดเมนูร้านค้า: เลือกรายการ ซื้อ และรับไฟล์ .mp4")
async def shop(ctx: discord.ApplicationContext):
    view = ShopView()
    await ctx.respond("โปรดเลือกรายการที่ต้องการซื้อจากเมนูด้านล่าง:", view=view, ephemeral=True)

# -------------------- RUN --------------------
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("กรุณาตั้งค่า ENV ชื่อ DISCORD_TOKEN")
    db_init()
    bot.run(DISCORD_TOKEN)
