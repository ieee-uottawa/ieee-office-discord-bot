import discord
from discord.ext import commands, tasks
from discord import app_commands, ui
from datetime import datetime
import os
import logging
from dotenv import load_dotenv
import requests
import math

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Configuration
SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8080")
API_KEY = os.getenv("DISCORD_BOT_API_KEY", "")  # API key for authentication (optional)

OFFICE_TRACKER_CHANNEL_NAME = os.getenv("OFFICE_TRACKER_CHANNEL_NAME", "office-tracker")

COMMUNITY_GUILD_ID = int(os.getenv("COMMUNITY_GUILD_ID"))
EXEC_GUILD_ID = int(os.getenv("EXEC_GUILD_ID"))

GUILD_MAPPING = {
    EXEC_GUILD_ID: "CONTROL",  # The server with Exit/Refresh
    COMMUNITY_GUILD_ID: "VIEW_ONLY",  # The server with only Refresh
}

# Build request headers with API key if configured
REQUEST_HEADERS = {"Content-Type": "application/json"}
if API_KEY:
    REQUEST_HEADERS["X-API-Key"] = API_KEY

ENDPOINTS = {
    "current": f"{SERVER_URL}/current",
    "members": f"{SERVER_URL}/members",
    "count": f"{SERVER_URL}/count",
    "scan_history": f"{SERVER_URL}/scan-history",
    "history": f"{SERVER_URL}/history",
    "signout_all": f"{SERVER_URL}/sign-out-all",
    "signin_discord": f"{SERVER_URL}/sign-in-discord",
    "signout_discord": f"{SERVER_URL}/sign-out-discord",
}

LAST_REFRESH_TIME = None
REFRESH_COOLDOWN = 15  # seconds

# Setup Intents
intents = discord.Intents.default()
intents.members = True

# Initialize Bot
bot = commands.Bot(command_prefix="!", intents=intents)

# -----------------------------
# Data Storage
# -----------------------------
office_attendees: dict[str, datetime] = {}
server_status: dict = {"ok": True, "error": None}

# -----------------------------
# Helpers
# -----------------------------


def get_current_office_attendees():
    """
    Fetches the current office attendees from the server, sorted by signin time.
    Returns tuple: (attendees_dict, is_success)
    """
    global server_status
    try:
        response = requests.get(ENDPOINTS["current"], headers=REQUEST_HEADERS, timeout=5)
        response.raise_for_status()
        data = response.json()

        # Handle None or empty response
        if data is None:
            logger.warning("API returned null response for current attendees")
            server_status = {"ok": False, "error": "Null response from server"}
            return {}, False

        attendees = {
            entry["name"]: datetime.fromisoformat(entry["signin_time"])
            for entry in data
        }
        server_status = {"ok": True, "error": None}
        return attendees, True
    except requests.RequestException as e:
        logger.error(f"Error fetching current office attendees: {e}")
        server_status = {"ok": False, "error": str(e)}
        return {}, False
    except (KeyError, ValueError) as e:
        logger.error(f"Error parsing attendee data: {e}")
        server_status = {"ok": False, "error": f"Data parsing error: {e}"}
        return {}, False


# -----------------------------
# Views (The Buttons)
# -----------------------------


class BaseOfficeView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def do_refresh(self, interaction: discord.Interaction):
        # Acknowledge the click immediately to prevent "Interaction Failed"
        await interaction.response.defer()
        global LAST_REFRESH_TIME

        now = datetime.now()
        if LAST_REFRESH_TIME is not None:
            elapsed = (now - LAST_REFRESH_TIME).total_seconds()
            if elapsed < REFRESH_COOLDOWN:
                wait = math.ceil(REFRESH_COOLDOWN - elapsed)
                await interaction.followup.send(
                    f"Please wait {wait} second(s) before refreshing.", ephemeral=True
                )
                return

        # Trigger a global update across all servers
        await global_refresh()


class ReadOnlyView(BaseOfficeView):
    def __init__(self):
        super().__init__()

    @ui.button(
        label="Refresh 🔄",
        style=discord.ButtonStyle.gray,
        custom_id="ro_refresh_button",
    )
    async def refresh_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.do_refresh(interaction)


class ControlView(BaseOfficeView):
    def __init__(self):
        super().__init__()

    @ui.button(
        label="Leaving 🟥", style=discord.ButtonStyle.red, custom_id="ctrl_leave_button"
    )
    async def leave(self, interaction: discord.Interaction, button: ui.Button):
        # Acknowledge the click immediately to prevent "Interaction Failed"
        await interaction.response.defer()

        # Make request to sign out user using Discord ID
        user_id = interaction.user.id
        try:
            response = requests.post(
                ENDPOINTS["signout_discord"], json={"discord_id": str(user_id)}, headers=REQUEST_HEADERS, timeout=5
            )
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Error signing out user {user_id}: {e}")
            return

        logger.debug(f"{response.json()['message']}")

        # Update EVERY server
        await global_refresh()

    @ui.button(
        label="Refresh 🔄",
        style=discord.ButtonStyle.gray,
        custom_id="ctrl_refresh_button",
    )
    async def refresh_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.do_refresh(interaction)


# -----------------------------
# Global Update Logic
# -----------------------------
async def global_refresh():
    """
    Updates the dashboard in ALL configured guilds.
    """
    global LAST_REFRESH_TIME
    logger.info("Triggering global dashboard refresh...")
    LAST_REFRESH_TIME = datetime.now()

    # 1. Build the Embed Content (Shared across both)
    global office_attendees, server_status
    office_attendees, is_success = get_current_office_attendees()

    # Check server status and build appropriate embed
    if not server_status["ok"]:
        # Server error
        embed = discord.Embed(
            title="🏢 IEEE Office Presence",
            description="⚠️ **Server Connection Error**",
            color=0xE74C3C,  # Red
        )
        embed.add_field(
            name="Error:",
            value=f"Unable to fetch data from server.\n```{server_status['error']}```",
            inline=False,
        )
        embed.set_footer(text=f"Last update: {datetime.now().strftime('%H:%M:%S')}")
    else:
        # Server OK
        if len(office_attendees) == 0:
            value = "No one is currently in the office."
            color = 0x95A5A6  # Grey
        else:
            # Sort by arrival time (already sorted from backend)
            value = "\n".join(
                [
                    f"• **{name}** (since {time.strftime('%H:%M')})"
                    for name, time in office_attendees.items()
                ]
            )
            color = 0x2ECC71  # Green

        embed = discord.Embed(
            title="🏢 IEEE Office Presence",
            description="Current occupancy status:",
            color=color,
        )
        embed.add_field(name="Currently in office:", value=value, inline=False)
        embed.set_footer(text=f"Last update: {datetime.now().strftime('%H:%M:%S')}")

    # 2. Iterate through our configured guilds
    for guild_id, mode in GUILD_MAPPING.items():
        guild = bot.get_guild(guild_id)
        if not guild:
            continue  # Bot might not be in the server yet

        channel = discord.utils.get(
            guild.text_channels, name=OFFICE_TRACKER_CHANNEL_NAME
        )
        if not channel:
            continue  # Channel doesn't exist in this server

        # Determine which View (buttons) to use for this specific server
        view_to_use = ControlView() if mode == "CONTROL" else ReadOnlyView()

        # Find the last message by the bot and edit it
        # NOTE: Could save Message IDs to a file to avoid history scraping)
        async for msg in channel.history(limit=10):
            if msg.author == bot.user:
                try:
                    await msg.edit(embed=embed, view=view_to_use)
                except discord.HTTPException as e:
                    logger.error(f"Failed to edit message in {guild.name}: {e}")
                break


# -----------------------------
# Slash Commands
# -----------------------------


@bot.tree.command(
    name="setup", description="[Admin] Create the office presence dashboard"
)
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    guild_id = interaction.guild_id

    # Determine mode based on config
    mode = GUILD_MAPPING.get(guild_id)

    if not mode:
        await interaction.response.send_message(
            f"⚠️ This server (ID: {guild_id}) is not configured in `GUILD_MAPPING` code.",
            ephemeral=True,
        )
        return

    # Select View
    view = ControlView() if mode == "CONTROL" else ReadOnlyView()

    embed = discord.Embed(
        title="🏢 IEEE Office Presence",
        description="Initializing...",
        color=0x2ECC71,
    )

    await interaction.response.send_message(embed=embed, view=view)

    # Immediately do a refresh to sync data and format
    await global_refresh()


@bot.tree.command(name="add_member", description="Add a member to the backend")
@app_commands.guilds(discord.Object(id=EXEC_GUILD_ID))
async def add_member(
    interaction: discord.Interaction, member: discord.Member, uid: str, name: str = None
):
    """
    Adds a member to the backend system, linking their Discord ID to a unique identifier (UID) and optional name.
    1. member: The Discord member to add.
    2. uid: The unique identifier for the member (e.g., student ID).
    3. name: Optional name for the member; if not provided, Discord display name is used.
    """
    name_to_use = name if name else member.display_name
    try:
        response = requests.post(
            ENDPOINTS["members"],
            json={
                "name": name_to_use,
                "uid": uid,
                "discord_id": str(member.id),
            },
            headers=REQUEST_HEADERS,
            timeout=5,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Error adding member {member.id}: {e}")
        await interaction.response.send_message(
            f"❌ Failed to add member: {e}", ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"✅ Successfully added {member.mention} with UID `{uid}`.", ephemeral=True
    )


@bot.tree.command(name="list_members", description="List all members in the backend")
@app_commands.guilds(discord.Object(id=EXEC_GUILD_ID))
async def list_members(interaction: discord.Interaction):
    """
    Lists all members currently registered in the backend system.
    """
    try:
        response = requests.get(ENDPOINTS["members"], headers=REQUEST_HEADERS, timeout=5)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        logger.error(f"Error fetching members: {e}")
        await interaction.response.send_message(
            f"❌ Failed to fetch members: {e}", ephemeral=True
        )
        return

    if not data:
        await interaction.response.send_message(
            "No members found in the backend.", ephemeral=True
        )
        return

    member_list = "\n".join(
        [
            f"• **{entry['name']}** (UID: `{entry['uid']}`, Discord ID: `{entry['discord_id']}`)"
            for entry in data
        ]
    )

    embed = discord.Embed(
        title="📋 Registered Members",
        description=member_list,
        color=0x3498DB,
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="scan_history", description="List last 10 scan events")
@app_commands.guilds(discord.Object(id=EXEC_GUILD_ID))
async def scan_history(interaction: discord.Interaction):
    """
    Lists the last 10 scan events from the backend system.
    """
    try:
        response = requests.get(ENDPOINTS["scan_history"], headers=REQUEST_HEADERS, timeout=5)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        logger.error(f"Error fetching scan history: {e}")
        await interaction.response.send_message(
            f"❌ Failed to fetch scan history: {e}", ephemeral=True
        )
        return

    if not data:
        await interaction.response.send_message(
            "No scan history found.", ephemeral=True
        )
        return

    # Build a more readable, robust list for the scan history
    history_lines = []
    for entry in data:
        uid = entry.get("uid", "Unknown UID")
        name = entry.get("name")
        raw_time = entry.get("time") or entry.get("timestamp")

        # Try to parse ISO timestamps to a friendly format, fall back to raw string
        time_str = raw_time or "Unknown time"
        try:
            time_obj = datetime.fromisoformat(raw_time)
            time_str = time_obj.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            # keep raw_time if parsing fails
            pass

        if name:
            history_lines.append(f"• **{name}** (UID `{uid}`) — {time_str}")
        else:
            history_lines.append(f"• **{uid}** — {time_str}")

    history_list = "\n".join(history_lines)

    embed = discord.Embed(
        title="📜 Last 10 Scan Events",
        description=history_list,
        color=0x9B59B6,
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="history", description="View recent office visit history")
@app_commands.guilds(discord.Object(id=EXEC_GUILD_ID))
async def history(interaction: discord.Interaction, limit: int = 10):
    """
    Shows recent completed office visits (sign-in + sign-out sessions).
    limit: Number of recent sessions to display (default 10, max 25)
    """
    # Clamp limit to reasonable range
    limit = max(1, min(limit, 25))
    
    try:
        response = requests.get(ENDPOINTS["history"], headers=REQUEST_HEADERS, timeout=5)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        logger.error(f"Error fetching history: {e}")
        await interaction.response.send_message(
            f"❌ Failed to fetch history: {e}", ephemeral=True
        )
        return

    if not data:
        await interaction.response.send_message(
            "No visit history available yet.", ephemeral=True
        )
        return

    # Take only the requested number of records
    sessions = data[:limit]
    
    history_lines = []
    for session in sessions:
        name = session.get("name", "Unknown")
        signin = session.get("signin_time", "")
        signout = session.get("signout_time", "")
        
        # Parse timestamps
        try:
            signin_dt = datetime.fromisoformat(signin)
            signout_dt = datetime.fromisoformat(signout)
            
            # Calculate duration
            duration = signout_dt - signin_dt
            hours = duration.total_seconds() / 3600
            
            # Format display
            date_str = signin_dt.strftime("%Y-%m-%d")
            signin_time = signin_dt.strftime("%H:%M")
            signout_time = signout_dt.strftime("%H:%M")
            
            if hours >= 1:
                duration_str = f"{hours:.1f}h"
            else:
                minutes = duration.total_seconds() / 60
                duration_str = f"{minutes:.0f}m"
            
            history_lines.append(
                f"• **{name}** — {date_str} {signin_time}-{signout_time} ({duration_str})"
            )
        except Exception as e:
            # Fallback if timestamp parsing fails
            history_lines.append(f"• **{name}** — {signin} to {signout}")
    
    history_text = "\n".join(history_lines)
    
    embed = discord.Embed(
        title="🕒 Office Visit History",
        description=history_text,
        color=0x3498DB,
    )
    embed.set_footer(text=f"Showing last {len(sessions)} visit(s)")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="signout_all", description="[Admin] Sign out all members from the office"
)
@app_commands.checks.has_permissions(administrator=True)
async def signout_all(interaction: discord.Interaction):
    """
    Signs out all members currently signed in to the office.
    """
    try:
        response = requests.post(ENDPOINTS["signout_all"], headers=REQUEST_HEADERS, timeout=5)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Error signing out all members: {e}")
        await interaction.response.send_message(
            f"❌ Failed to sign out all members: {e}", ephemeral=True
        )
        return

    await interaction.response.send_message(
        "✅ All members have been signed out from the office.", ephemeral=True
    )

    # Update EVERY server
    await global_refresh()


@bot.tree.command(name="signin", description="[Admin] Sign in a member to the office")
@app_commands.checks.has_permissions(administrator=True)
async def signin(interaction: discord.Interaction, member: discord.Member):
    """
    Signs in a member to the office using their Discord ID.
    """
    try:
        response = requests.post(
            ENDPOINTS["signin_discord"], json={"discord_id": str(member.id)}, headers=REQUEST_HEADERS, timeout=5
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Error signing in member {member.id}: {e}")
        await interaction.response.send_message(
            f"❌ Failed to sign in member: {e}", ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"✅ {member.mention} has been signed in to the office.", ephemeral=True
    )

    # Update EVERY server
    await global_refresh()


@bot.tree.command(name="signout", description="Sign out a member from the office")
async def signout(interaction: discord.Interaction, member: discord.Member):
    """
    Signs out a member from the office using their Discord ID.
    """
    try:
        response = requests.post(
            ENDPOINTS["signout_discord"], json={"discord_id": str(member.id)}, headers=REQUEST_HEADERS, timeout=5
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Error signing out member {member.id}: {e}")
        await interaction.response.send_message(
            f"❌ Failed to sign out member: {e}", ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"✅ {member.mention} has been signed out from the office.", ephemeral=True
    )

    # Update EVERY server
    await global_refresh()


# -----------------------------
# Background Tasks
# -----------------------------
@tasks.loop(minutes=1)
async def auto_refresh_task():
    """
    Automatically refreshes the dashboard every 1 minutes.
    """
    # TODO: make it so that it has a longer interval during off-hours
    if LAST_REFRESH_TIME is not None:
        elapsed = (datetime.now() - LAST_REFRESH_TIME).total_seconds()
        if elapsed < REFRESH_COOLDOWN:
            return  # Skip refresh if within cooldown
    logger.info("Running scheduled auto-refresh...")
    await global_refresh()


@auto_refresh_task.before_loop
async def before_auto_refresh():
    """
    Wait until the bot is ready before starting the background task.
    """
    await bot.wait_until_ready()


# -----------------------------
# Startup
# -----------------------------
@bot.event
async def on_ready():
    # Important: Register BOTH views so the buttons work after restart
    bot.add_view(ReadOnlyView())
    bot.add_view(ControlView())

    logger.info(f"Logged in as {bot.user}")

    # Sync GLOBAL commands (like /setup)
    await bot.tree.sync()
    logger.info("Global commands synced.")

    # Sync EXEC SERVER commands (like /add_member)
    exec_guild_obj = discord.Object(id=EXEC_GUILD_ID)
    try:
        await bot.tree.sync(guild=exec_guild_obj)
        logger.info(f"Exec Guild ({EXEC_GUILD_ID}) commands synced.")
    except discord.HTTPException as e:
        logger.error(f"Failed to sync Exec guild commands: {e}")

    # Start the auto-refresh background task
    if not auto_refresh_task.is_running():
        auto_refresh_task.start()
        logger.info("Auto-refresh task started (every 1 minutes).")


if __name__ == "__main__":
    bot.run(TOKEN)
