import os
import re
import json
import discord
from discord.ext import commands
import gspread

# -------------------- CONFIG --------------------
INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.members = True
INTENTS.message_content = False  # เราใช้ slash command + components

# ✅ เอามาแค่ตัวเดียว ใช้ ENV เฉพาะ TOKEN
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")

# ✅ ส่วนนี้ fix ค่าไว้ตรงๆในโค้ด
SHEETS_ID = "15GgroJJD1yj2ipc33HJvvy7ajFzwXfOil6k96HtyLdc"
SERVICE_ACCOUNT_JSON = """
{
  "type": "service_account",
  "project_id": "xxxx",
  "private_key_id": "xxxx",
  "private_key": "-----BEGIN PRIVATE KEY-----\\n....\\n-----END PRIVATE KEY-----\\n",
  "client_email": "xxxx@xxxx.iam.gserviceaccount.com",
  "client_id": "xxxx",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/xxxx"
}
"""

CATALOG_SHEET_NAME = "catalog"
WALLET_SHEET_NAME  = "wallet"
ORDERS_SHEET_NAME  = "orders"

MAX_LINKS_PER_MESSAGE = 8
# ------------------------------------------------


# ---------- Google Sheets helper ----------
class Sheets:
    def __init__(self, creds_json: str, spreadsheet_id: str):
        try:
            creds_dict = json.loads(creds_json)
            self.gc = gspread.service_account_from_dict(creds_dict)
        except Exception as e:
            raise RuntimeError(f"โหลด Google Service Account ไม่สำเร็จ: {e}")

        self.spreadsheet = self.gc.open_by_key(spreadsheet_id)

    def _ws(self, name: str):
        return self.spreadsheet.worksheet(name)

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

# … (โค้ดปุ่ม / เมนู / slash command เหมือนเดิม ไม่ต้องแก้)
# -------------------------------


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

    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print("Ready.")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("❌ กรุณาตั้งค่า ENV: DISCORD_TOKEN ก่อนรันบอท")
    bot.run(DISCORD_TOKEN)
        
