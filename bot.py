# bot.py
import os
import datetime
import pytz
import discord
from discord import app_commands
from dotenv import load_dotenv
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ---------- ENV ----------
load_dotenv()
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN")
SPREADSHEET_ID  = os.getenv("SPREADSHEET_ID")           # use ID, not name
WORKSHEET_NAME  = os.getenv("WORKSHEET_NAME", "TradingJournal")
TZNAME          = os.getenv("TZ", "Asia/Makassar")
GUILD_ID_ENV    = os.getenv("GUILD_ID", "0")

if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN in .env")
if not SPREADSHEET_ID:
    raise RuntimeError("Missing SPREADSHEET_ID in .env")

GUILD_ID  = int(GUILD_ID_ENV) if GUILD_ID_ENV.isdigit() else 0
guild_obj = discord.Object(id=GUILD_ID) if GUILD_ID else None

# ---------- GOOGLE SHEETS AUTH ----------
# Sheets scope is sufficient; Drive scope optional. Keep both if you like.
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)
gc = gspread.authorize(creds)

# Open spreadsheet by ID (avoids title mismatches)
sh = gc.open_by_key(SPREADSHEET_ID)
print("Worksheets available:", [w.title for w in sh.worksheets()])

# Get the worksheet, or create it with headers
try:
    ws = sh.worksheet(WORKSHEET_NAME)
except gspread.exceptions.WorksheetNotFound:
    ws = sh.add_worksheet(title=WORKSHEET_NAME, rows=1000, cols=9)
    ws.update(
        values=[[
            'timestamp_local','discord_user','message_id','ticker',
            'position','price_open','price_close','gain_loss','gain_loss%'
        ]],
        range_name='A1:I1',
    )
    print(f"Created worksheet '{WORKSHEET_NAME}' with headers.")

# ---------- DISCORD CLIENT / SLASH CMDS ----------
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

def calc_pl(position: str, price_open: float | None, price_close: float | None):
    """Return per-unit P/L and P/L% (floats) or (None, None) if not closable."""
    if price_open is None or price_close is None:
        return None, None
    pos = position.lower()
    if pos not in ("long", "short"):
        raise ValueError("position must be 'long' or 'short'")
    gl = (price_close - price_open) if pos == "long" else (price_open - price_close)
    gl_pct = (gl / price_open) * 100 if price_open != 0 else 0.0
    return gl, gl_pct

def append_journal_row(user: str, message_id: int, ticker: str, position: str,
                       price_open: float, price_close: float | None):
    tz = pytz.timezone(TZNAME)
    ts = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

    ticker = ticker.upper().strip()
    position = position.lower().strip()
    if position not in ("long", "short"):
        raise ValueError("position must be 'long' or 'short'")

    gl, gl_pct = calc_pl(position, price_open, price_close)

    row = [
        ts,                         # timestamp_local
        user,                       # discord_user
        str(message_id),            # message_id
        ticker,                     # ticker
        position,                   # position
        price_open,                 # price_open
        ("" if price_close is None else price_close),  # price_close
        ("" if gl is None else gl),                    # gain_loss
        ("" if gl_pct is None else gl_pct),            # gain_loss%
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")

@tree.command(name="journal", description="Log a trade journal row to Google Sheets")
@app_commands.describe(
    ticker="Ticker symbol (e.g., EMTK, VWO)",
    position="long or short",
    price_open="Entry price",
    price_close="Exit price (optional)"
)
@app_commands.choices(position=[
    app_commands.Choice(name="long", value="long"),
    app_commands.Choice(name="short", value="short"),
])
async def journal(
    interaction: discord.Interaction,
    ticker: str,
    position: app_commands.Choice[str],
    price_open: float,
    price_close: float | None = None
):
    try:
        # Respond immediately so the token stays valid while we do I/O
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Do the slow work (Google Sheets)
        append_journal_row(
            user=f"{interaction.user.name}#{interaction.user.discriminator}",
            message_id=interaction.id,
            ticker=ticker,
            position=position.value,
            price_open=price_open,
            price_close=price_close
        )

        suffix = "" if price_close is None else f" | closed @ {price_close}"
        await interaction.followup.send(
            f"✅ Journaled {position.value.upper()} {ticker.upper()} | open @ {price_open}{suffix}",
            ephemeral=True
        )

    except Exception as e:
        # If we've already deferred, use followup; otherwise send initial response
        if interaction.response.is_done():
            await interaction.followup.send(f"❌ Failed: {e}", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)

# Simple test command to verify slash commands are synced
@tree.command(name="ping", description="Test command visibility")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("pong", ephemeral=True)

@client.event
async def on_ready():
    # Sync slash commands to your guild for instant availability if GUILD_ID is set,
    # otherwise do a global sync (can take a while to appear).
    try:
        if guild_obj:
            synced = await tree.sync(guild=guild_obj)
            where = f"guild {GUILD_ID}"
        else:
            synced = await tree.sync()
            where = "global"
        print(f"Logged in as {client.user} | synced {len(synced)} commands to {where}")
        print("Commands:", [c.name for c in synced])
    except Exception as e:
        print("Slash command sync failed:", e)

client.run(DISCORD_TOKEN)
