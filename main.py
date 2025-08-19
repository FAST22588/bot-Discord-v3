import os
import re
import json
import asyncio
import discord
from discord.ext import commands
import gspread

# -------------------- CONFIG --------------------
INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.members = True
INTENTS.message_content = False  # เราใช้ slash command + components

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
SHEETS_ID = os.getenv("15GgroJJD1yj2ipc33HJvvy7ajFzwXfOil6k96HtyLdc", "")

SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
CATALOG_SHEET_NAME = os.getenv("CATALOG_SHEET_NAME", "catalog")
WALLET_SHEET_NAME  = os.getenv("WALLET_SHEET_NAME", "wallet")
ORDERS_SHEET_NAME  = os.getenv("ORDERS_SHEET_NAME", "orders")

MAX_LINKS_PER_MESSAGE = 8
# ------------------------------------------------

# ---------- Google Sheets helper ----------
class Sheets:
    def __init__(self, creds_env: str, spreadsheet_id: str):
        try:
            if os.path.exists(creds_env):
                # ถ้าเป็น path ไปยังไฟล์
                self.gc = gspread.service_account(filename=creds_env)
            else:
                # ถ้าเป็น JSON string (จาก ENV)
                creds_dict = json.loads(creds_env)
                self.gc = gspread.service_account_from_dict(creds_dict)
        except Exception as e:
            raise RuntimeError(f"โหลด Google Service Account ไม่สำเร็จ: {e}")

        self.spreadsheet = self.gc.open_by_key(spreadsheet_id)

    def _ws(self, name: str):
        return self.spreadsheet.worksheet(name)

    # ----- Catalog -----
    def get_menu_headers(self):
        ws = self._ws(CATALOG_SHEET_NAME)
        headers = ws.row_values(1)
        headers = [h.strip() for h in headers if str(h).strip() != ""]
        return headers

    def get_links_by_column_header(self, header_name: str):
        ws = self._ws(CATALOG_SHEET_NAME)
        headers = ws.row_values(1)
        col_index = None
        for i, h in enumerate(headers, start=1):
            if str(h).strip().lower() == header_name.strip().lower():
                col_index = i
                break
        if not col_index:
            return []
        col_values = ws.col_values(col_index)[2:]
        links = [v.strip() for v in col_values if str(v).strip()]
        return links

    # ----- Wallet -----
    def get_balance(self, user_id: int) -> float:
        try:
            ws = self._ws(WALLET_SHEET_NAME)
        except gspread.WorksheetNotFound:
            return 0.0

        records = ws.get_all_values()
        for row in records[1:]:
            if len(row) >= 2 and str(row[0]).strip() == str(user_id):
                try:
                    return float(row[1])
                except:
                    return 0.0
        return 0.0

    # ----- Orders -----
    def get_orders(self, user_id: int):
        try:
            ws = self._ws(ORDERS_SHEET_NAME)
        except gspread.WorksheetNotFound:
            return []

        orders = []
        records = ws.get_all_values()
        for row in records[1:]:
            if len(row) >= 2 and str(row[0]).strip() == str(user_id):
                item = row[1].strip()
                ts = row[2].strip() if len(row) >= 3 and row[2] else "-"
                orders.append((item, ts))
        return orders


# ---------- Utilities ----------
GDRIVE_FILE_PATTERNS = [
    r"https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]{20,})",
    r"https?://drive\.google\.com/open\?id=([a-zA-Z0-9_-]{20,})",
    r"https?://drive\.google\.com/uc\?id=([a-zA-Z0-9_-]{20,})",
    r"https?://drive\.google\.com/uc\?export=download&id=([a-zA-Z0-9_-]{20,})",
]

def gdrive_to_direct(url: str) -> str:
    file_id = None
    for pattern in GDRIVE_FILE_PATTERNS:
        m = re.search(pattern, url)
        if m:
            file_id = m.group(1)
            break
    if not file_id:
        return url.strip()
    return f"https://drive.google.com/uc?export=download&id={file_id}"


# ---------- Discord Bot ----------
bot = commands.Bot(command_prefix="!", intents=INTENTS)
sheets: Sheets | None = None


class CatalogSelect(discord.ui.Select):
    def __init__(self, options_labels: list[str]):
        options = [discord.SelectOption(label=label[:100]) for label in options_labels[:25]]
        super().__init__(
            placeholder="เลือกรายการทั้งหมด (จากหัวคอลัมน์ใน Google Sheets)",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        header = self.values[0]
        await interaction.response.defer(thinking=True, ephemeral=True)
        links = sheets.get_links_by_column_header(header) if sheets else []
        if not links:
            return await interaction.followup.send(f"ไม่พบลิงก์ในคอลัมน์ **{header}**", ephemeral=True)

        direct_links = [gdrive_to_direct(u) for u in links]

        chunks = []
        chunk = []
        for url in direct_links:
            chunk.append(url)
            if len(chunk) >= MAX_LINKS_PER_MESSAGE:
                chunks.append("\n".join(chunk))
                chunk = []
        if chunk:
            chunks.append("\n".join(chunk))

        title = f"รายการวิดีโอ: {header}"
        await interaction.followup.send(f"**{title}**\n(จาก Google Sheets)", ephemeral=True)
        for text in chunks:
            await interaction.followup.send(text, ephemeral=True)


class MenuView(discord.ui.View):
    def __init__(self, headers: list[str], timeout: float = 180):
        super().__init__(timeout=timeout)
        self.add_item(CheckBalanceButton())
        self.add_item(HistoryButton())
        self.add_item(CatalogSelect(headers))


class CheckBalanceButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="เช็คยอดเงิน", style=discord.ButtonStyle.primary, emoji="💰")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=False)
        balance = sheets.get_balance(interaction.user.id) if sheets else 0.0
        await interaction.followup.send(
            f"ยอดเงินของคุณ: **{balance:,.2f}** บาท",
            ephemeral=True
        )


class HistoryButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="ประวัติการซื้อทั้งหมด", style=discord.ButtonStyle.secondary, emoji="🧾")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        user_orders = sheets.get_orders(interaction.user.id) if sheets else []
        if not user_orders:
            return await interaction.followup.send("ยังไม่มีประวัติการซื้อ", ephemeral=True)

        lines = [f"• {item} — {ts}" for item, ts in user_orders]
        text = "\n".join(lines)

        MAX = 1800
        chunks = [text[i:i+MAX] for i in range(0, len(text), MAX)]
        await interaction.followup.send("**ประวัติการซื้อของคุณ**", ephemeral=True)
        for c in chunks:
            await interaction.followup.send(c, ephemeral=True)


@bot.tree.command(name="menu", description="แสดงเมนูซื้อ/รายการทั้งหมดจาก Google Sheets")
async def menu_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        headers = sheets.get_menu_headers() if sheets else []
    except Exception as e:
        return await interaction.followup.send(f"โหลดหัวคอลัมน์ไม่สำเร็จ: {e}", ephemeral=True)

    if not headers:
        return await interaction.followup.send("ยังไม่พบหัวคอลัมน์ในชีต `catalog`", ephemeral=True)

    embed = discord.Embed(
        title="ระบบขายแอป/วิดีโอ (เมนู)",
        description="• บริการแอพพรีเมี่ยม/วิดีโอ\n• เลือกหัวข้อจาก **รายการทั้งหมด** เพื่อรับลิงก์วิดีโอ\n",
        color=0x2b2d31
    )
    embed.set_footer(text="ดึงข้อมูลจาก Google Sheets แบบไดนามิก")
    view = MenuView(headers=headers)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


@bot.event
async def on_ready():
    global sheets
    try:
        sheets = Sheets(SERVICE_ACCOUNT_JSON, SHEETS_ID)
    except Exception as e:
        print("ERROR init Sheets:", e)

    try:
        await bot.tree.sync()
    except Exception as e:
        print("ERROR syncing app commands:", e)

    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Ready.")


if __name__ == "__main__":
    if not DISCORD_TOKEN or not SHEETS_ID or not SERVICE_ACCOUNT_JSON:
        raise SystemExit("กรุณาตั้งค่า ENV: DISCORD_TOKEN, GOOGLE_SHEETS_ID และ GOOGLE_SERVICE_ACCOUNT_JSON ก่อนรันบอท")
    bot.run(DISCORD_TOKEN)
