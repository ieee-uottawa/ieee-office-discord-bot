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
WEEKLY_REPORT_CHANNEL_ID = os.getenv("WEEKLY_REPORT_CHANNEL_ID")  # Channel for automated weekly reports
WEEKLY_REPORT_ENABLED = os.getenv("WEEKLY_REPORT_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")

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
    "visits": f"{SERVER_URL}/visits",
    "signout_all": f"{SERVER_URL}/sign-out-all",
    "signin_discord": f"{SERVER_URL}/sign-in-discord",
    "signout_discord": f"{SERVER_URL}/sign-out-discord",
}

LAST_REFRESH_TIME = None
REFRESH_COOLDOWN = 10  # seconds

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
        response = requests.get(
            ENDPOINTS["current"], headers=REQUEST_HEADERS, timeout=5
        )
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


def calculate_leaderboard(days: int = 7, top_n: int = 10):
    """
    Fetches visit data and calculates leaderboard statistics.
    Filters out auto-signouts at 4 AM (nightly cleanup).
    
    Args:
        days: Number of days to look back
        top_n: Number of top members to return
    
    Returns:
        tuple: (leaderboard_data, error_message)
               leaderboard_data is list of dicts with: name, visits, total_hours, avg_hours
    """
    try:
        # Calculate date range
        from_date = (datetime.now() - __import__('datetime').timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
        to_date = datetime.now().strftime("%Y-%m-%dT23:59:59Z")
        
        # Fetch visits
        params = {"from": from_date, "to": to_date, "limit": 1000}
        response = requests.get(
            ENDPOINTS["visits"], params=params, headers=REQUEST_HEADERS, timeout=10
        )
        response.raise_for_status()
        visits = response.json()
        
        if not visits:
            return [], None
        
        # Aggregate by member, filtering out 4 AM auto-signouts
        member_stats = {}
        for visit in visits:
            name = visit.get("name", "Unknown")
            signin = visit.get("signin_time", "")
            signout = visit.get("signout_time", "")
            
            try:
                signin_dt = datetime.fromisoformat(signin)
                signout_dt = datetime.fromisoformat(signout)
                
                # Filter out auto-signouts at 4 AM (nightly cleanup)
                if signout_dt.hour == 4 and signout_dt.minute == 0:
                    continue
                
                duration_hours = (signout_dt - signin_dt).total_seconds() / 3600
                
                # Skip unreasonably long visits (>24 hours, likely errors)
                if duration_hours > 24:
                    continue
                
                if name not in member_stats:
                    member_stats[name] = {"visits": 0, "total_hours": 0}
                
                member_stats[name]["visits"] += 1
                member_stats[name]["total_hours"] += duration_hours
            except Exception as e:
                logger.warning(f"Error processing visit: {e}")
                continue
        
        # Calculate averages and format
        leaderboard = []
        for name, stats in member_stats.items():
            leaderboard.append({
                "name": name,
                "visits": stats["visits"],
                "total_hours": stats["total_hours"],
                "avg_hours": stats["total_hours"] / stats["visits"] if stats["visits"] > 0 else 0
            })
        
        # Sort by visits (primary) and total hours (secondary)
        leaderboard.sort(key=lambda x: (x["visits"], x["total_hours"]), reverse=True)
        
        return leaderboard[:top_n], None
        
    except requests.RequestException as e:
        logger.error(f"Error fetching leaderboard data: {e}")
        return [], f"Failed to fetch data: {e}"
    except Exception as e:
        logger.error(f"Error calculating leaderboard: {e}")
        return [], f"Error processing data: {e}"


def build_leaderboard_embed(leaderboard_data: list, title: str, days: int = None, footer_text: str = None) -> discord.Embed:
    """
    Builds a leaderboard embed from leaderboard data.
    
    Args:
        leaderboard_data: List of dicts with name, visits, total_hours
        title: Embed title
        days: Number of days (for footer)
        footer_text: Optional custom footer text
    
    Returns:
        discord.Embed with formatted leaderboard
    """
    description_lines = []
    
    medals = ["🥇", "🥈", "🥉"]
    for idx, member in enumerate(leaderboard_data):
        rank = idx + 1
        medal = medals[idx] if idx < 3 else f"{rank}."
        
        hours_str = f"{member['total_hours']:.1f}h"
        visits_str = f"{member['visits']} visit{'s' if member['visits'] != 1 else ''}"
        
        description_lines.append(
            f"{medal} **{member['name']}** — {visits_str} ({hours_str})"
        )
    
    embed = discord.Embed(
        title=title,
        description="\n".join(description_lines),
        color=0xFFD700,  # Gold
    )
    
    # Set footer
    if footer_text:
        embed.set_footer(text=footer_text)
    elif days:
        embed.set_footer(text=f"Last {days} days • Excluding auto-signouts at 4 AM")
    
    return embed


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
                ENDPOINTS["signout_discord"],
                json={"discord_id": str(user_id)},
                headers=REQUEST_HEADERS,
                timeout=5,
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


class PaginatedView(ui.View):
    """
    A reusable paginated view with Previous/Next buttons.
    """
    def __init__(self, pages: list[discord.Embed], timeout: int = 180):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.current_page = 0
        self.max_page = len(pages) - 1
        
        # Update button states for initial page
        self.update_buttons()
    
    def update_buttons(self):
        """Update button states based on current page."""
        self.previous_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page == self.max_page)
    
    def get_current_embed(self) -> discord.Embed:
        """Get the embed for the current page with page indicator."""
        embed = self.pages[self.current_page]
        # Preserve existing footer if present, append page info
        current_footer = embed.footer.text if embed.footer else ""
        if current_footer:
            page_info = f" • Page {self.current_page + 1}/{self.max_page + 1}"
        else:
            page_info = f"Page {self.current_page + 1}/{self.max_page + 1}"
        embed.set_footer(text=current_footer + page_info)
        return embed
    
    @ui.button(label="◀ Previous", style=discord.ButtonStyle.gray, custom_id="paginated_prev")
    async def previous_button(self, interaction: discord.Interaction, button: ui.Button):
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        try:
            await interaction.response.edit_message(embed=self.get_current_embed(), view=self)
        except discord.errors.NotFound:
            # Message was deleted, stop the view
            self.stop()
    
    @ui.button(label="Next ▶", style=discord.ButtonStyle.gray, custom_id="paginated_next")
    async def next_button(self, interaction: discord.Interaction, button: ui.Button):
        self.current_page = min(self.max_page, self.current_page + 1)
        self.update_buttons()
        try:
            await interaction.response.edit_message(embed=self.get_current_embed(), view=self)
        except discord.errors.NotFound:
            # Message was deleted, stop the view
            self.stop()
    
    @ui.button(label="🗑️ Close", style=discord.ButtonStyle.red, custom_id="paginated_close")
    async def close_button(self, interaction: discord.Interaction, button: ui.Button):
        try:
            await interaction.response.defer()
            await interaction.delete_original_response()
        except discord.errors.NotFound:
            pass  # Message already deleted
        self.stop()
    
    async def on_timeout(self):
        """Disable buttons when view times out."""
        for item in self.children:
            item.disabled = True


def create_pages(items: list, items_per_page: int, title: str, 
                 formatter: callable, color: int = 0x3498DB) -> list[discord.Embed]:
    """
    Split items into pages and create embeds.
    
    Args:
        items: List of items to paginate
        items_per_page: Number of items per page
        title: Embed title
        formatter: Function to format each item as a string
        color: Embed color
    
    Returns:
        List of Discord embeds (one per page)
    """
    pages = []
    total_pages = (len(items) + items_per_page - 1) // items_per_page  # Ceiling division
    
    for page_num in range(total_pages):
        start_idx = page_num * items_per_page
        end_idx = min(start_idx + items_per_page, len(items))
        page_items = items[start_idx:end_idx]
        
        description = "\n".join([formatter(item) for item in page_items])
        
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
        )
        pages.append(embed)
    
    return pages


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


@bot.tree.command(name="add_member", description="[Admin] Add a member to the backend")
@app_commands.checks.has_permissions(administrator=True)
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


@bot.tree.command(name="members", description="List all members in the backend")
@app_commands.guilds(discord.Object(id=EXEC_GUILD_ID))
async def members(interaction: discord.Interaction):
    """
    Lists all members currently registered in the backend system.
    """
    try:
        response = requests.get(
            ENDPOINTS["members"], headers=REQUEST_HEADERS, timeout=5
        )
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

    # Create paginated embeds (15 members per page)
    def format_member(entry):
        return f"• **{entry['name']}** (ID: `{entry['id']}`, UID: `{entry['uid']}`, Discord: `{entry['discord_id']}`)"
    
    pages = create_pages(
        items=data,
        items_per_page=15,
        title="📋 Registered Members",
        formatter=format_member,
        color=0x3498DB
    )
    
    # Add total count to first page
    pages[0].description = f"*Total: {len(data)} member(s)*\n\n" + pages[0].description
    
    # If only one page, send without pagination
    if len(pages) == 1:
        pages[0].set_footer(text=f"Total: {len(data)} member(s)")
        await interaction.response.send_message(embed=pages[0], ephemeral=True)
    else:
        view = PaginatedView(pages)
        await interaction.response.send_message(
            embed=view.get_current_embed(), 
            view=view, 
            ephemeral=True
        )


@bot.tree.command(name="scan_history", description="List last 10 scan events")
@app_commands.guilds(discord.Object(id=EXEC_GUILD_ID))
async def scan_history(interaction: discord.Interaction):
    """
    Lists the last 10 scan events from the backend system.
    """
    try:
        response = requests.get(
            ENDPOINTS["scan_history"], headers=REQUEST_HEADERS, timeout=5
        )
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


@bot.tree.command(name="visits", description="View office visits with optional filters")
@app_commands.guilds(discord.Object(id=EXEC_GUILD_ID))
async def visits(
    interaction: discord.Interaction,
    from_date: str = None,
    to_date: str = None,
    limit: int = 100,
):
    """
    Retrieves office visits with optional date range filters.
    Results are paginated for readability.
    1. from_date: Start date in YYYY-MM-DD format (optional).
    2. to_date: End date in YYYY-MM-DD format (optional).
    3. limit: Maximum number of visits to return (default 100, max 500).
    """
    # Clamp limit to reasonable maximum
    limit = max(1, min(limit, 500))

    # Build query parameters
    params = {"limit": limit}
    if from_date:
        # Convert YYYY-MM-DD to RFC3339 format
        try:
            from_dt = datetime.strptime(from_date, "%Y-%m-%d")
            params["from"] = from_dt.strftime("%Y-%m-%dT00:00:00Z")
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid from_date format. Use YYYY-MM-DD (e.g., 2024-01-15).",
                ephemeral=True,
            )
            return

    if to_date:
        # Convert YYYY-MM-DD to RFC3339 format
        try:
            to_dt = datetime.strptime(to_date, "%Y-%m-%d")
            params["to"] = to_dt.strftime("%Y-%m-%dT23:59:59Z")
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid to_date format. Use YYYY-MM-DD (e.g., 2024-01-31).",
                ephemeral=True,
            )
            return

    try:
        response = requests.get(
            ENDPOINTS["visits"], params=params, headers=REQUEST_HEADERS, timeout=10
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        logger.error(f"Error fetching visits: {e}")
        await interaction.response.send_message(
            f"❌ Failed to fetch visits: {e}", ephemeral=True
        )
        return

    if not data:
        await interaction.response.send_message(
            "No visits found matching the criteria.", ephemeral=True
        )
        return

    # Format visit data
    def format_visit(visit):
        name = visit.get("name", "Unknown")
        signin = visit.get("signin_time", "")
        signout = visit.get("signout_time", "")

        try:
            signin_dt = datetime.fromisoformat(signin)
            signout_dt = datetime.fromisoformat(signout)
            duration = signout_dt - signin_dt
            hours = duration.total_seconds() / 3600

            date_str = signin_dt.strftime("%Y-%m-%d")
            signin_time = signin_dt.strftime("%H:%M")
            signout_time = signout_dt.strftime("%H:%M")

            if hours >= 1:
                duration_str = f"{hours:.1f}h"
            else:
                minutes = duration.total_seconds() / 60
                duration_str = f"{minutes:.0f}m"

            return f"• **{name}** — {date_str} {signin_time}-{signout_time} ({duration_str})"
        except Exception:
            return f"• **{name}** — {signin} to {signout}"
    
    # Create paginated embeds (20 visits per page)
    pages = create_pages(
        items=data,
        items_per_page=20,
        title="📊 Office Visits",
        formatter=format_visit,
        color=0x2ECC71
    )
    
    # Add filter info to first page
    filter_parts = [f"Total: {len(data)} visit(s)"]
    if from_date:
        filter_parts.append(f"from {from_date}")
    if to_date:
        filter_parts.append(f"to {to_date}")
    filter_text = " • ".join(filter_parts)
    
    pages[0].description = f"*{filter_text}*\n\n" + pages[0].description
    
    # Send with or without pagination
    if len(pages) == 1:
        pages[0].set_footer(text=filter_text)
        await interaction.response.send_message(embed=pages[0], ephemeral=True)
    else:
        view = PaginatedView(pages, timeout=300)  # 5 min timeout for longer lists
        await interaction.response.send_message(
            embed=view.get_current_embed(),
            view=view,
            ephemeral=True
        )


@bot.tree.command(
    name="delete_visits", description="[Admin] Delete visits within a date range"
)
@app_commands.guilds(discord.Object(id=EXEC_GUILD_ID))
@app_commands.checks.has_permissions(administrator=True)
async def delete_visits(
    interaction: discord.Interaction, from_date: str = None, to_date: str = None
):
    """
    Deletes office visits within the specified date range.
    At least one of from_date or to_date must be provided.
    1. from_date: Start date in YYYY-MM-DD format (optional).
    2. to_date: End date in YYYY-MM-DD format (optional).
    """
    if not from_date and not to_date:
        await interaction.response.send_message(
            "❌ At least one of from_date or to_date must be provided.", ephemeral=True
        )
        return

    # Build query parameters
    params = {}
    if from_date:
        try:
            from_dt = datetime.strptime(from_date, "%Y-%m-%d")
            params["from"] = from_dt.strftime("%Y-%m-%dT00:00:00Z")
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid from_date format. Use YYYY-MM-DD (e.g., 2024-01-15).",
                ephemeral=True,
            )
            return

    if to_date:
        try:
            to_dt = datetime.strptime(to_date, "%Y-%m-%d")
            params["to"] = to_dt.strftime("%Y-%m-%dT23:59:59Z")
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid to_date format. Use YYYY-MM-DD (e.g., 2024-01-31).",
                ephemeral=True,
            )
            return

    try:
        response = requests.delete(
            ENDPOINTS["visits"], params=params, headers=REQUEST_HEADERS, timeout=10
        )
        response.raise_for_status()
        result = response.json()
        deleted_count = result.get("deleted", 0)
    except requests.RequestException as e:
        logger.error(f"Error deleting visits: {e}")
        await interaction.response.send_message(
            f"❌ Failed to delete visits: {e}", ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"✅ Successfully deleted {deleted_count} visit(s).", ephemeral=True
    )


@bot.tree.command(
    name="signout_all", description="[Admin] Sign out all members from the office"
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guilds(discord.Object(id=EXEC_GUILD_ID))
async def signout_all(interaction: discord.Interaction):
    """
    Signs out all members currently signed in to the office.
    """
    try:
        response = requests.post(
            ENDPOINTS["signout_all"], headers=REQUEST_HEADERS, timeout=5
        )
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
@app_commands.guilds(discord.Object(id=EXEC_GUILD_ID))
async def signin(interaction: discord.Interaction, member: discord.Member):
    """
    Signs in a member to the office using their Discord ID.
    """
    try:
        response = requests.post(
            ENDPOINTS["signin_discord"],
            json={"discord_id": str(member.id)},
            headers=REQUEST_HEADERS,
            timeout=5,
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
@app_commands.guilds(discord.Object(id=EXEC_GUILD_ID))
async def signout(interaction: discord.Interaction, member: discord.Member):
    """
    Signs out a member from the office using their Discord ID.
    """
    try:
        response = requests.post(
            ENDPOINTS["signout_discord"],
            json={"discord_id": str(member.id)},
            headers=REQUEST_HEADERS,
            timeout=5,
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


@bot.tree.command(name="leaderboard", description="View office attendance leaderboard")
@app_commands.guilds(discord.Object(id=EXEC_GUILD_ID))
async def leaderboard(
    interaction: discord.Interaction,
    period: str = "week",
    top: int = 10
):
    """
    Shows attendance leaderboard for a time period.
    1. period: Time period - 'week' (7 days), 'month' (30 days), 'semester' (120 days), 'all' (all time)
    2. top: Number of top members to show (default 10, max 25)
    """
    # Map period to days
    period_map = {
        "week": 7,
        "month": 30,
        "semester": 120,
        "all": 3650  # ~10 years, effectively all time
    }
    
    period_lower = period.lower()
    if period_lower not in period_map:
        await interaction.response.send_message(
            f"❌ Invalid period. Choose from: week, month, semester, all",
            ephemeral=True
        )
        return
    
    days = period_map[period_lower]
    top = max(1, min(top, 25))  # Clamp between 1 and 25
    
    # Defer response as this might take a moment
    await interaction.response.defer(ephemeral=True)
    
    leaderboard_data, error = calculate_leaderboard(days=days, top_n=top)
    
    if error:
        await interaction.followup.send(f"❌ {error}", ephemeral=True)
        return
    
    if not leaderboard_data:
        await interaction.followup.send(
            f"No visit data found for the last {days} days.",
            ephemeral=True
        )
        return
    
    # Build leaderboard embed using shared function
    period_name = period_lower.capitalize() if period_lower != "all" else "All Time"
    embed = build_leaderboard_embed(
        leaderboard_data=leaderboard_data,
        title=f"🏆 Office Leaderboard — {period_name}",
        days=days
    )
    
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="weekly_reports", description="[Admin] Enable or disable the weekly report task")
@app_commands.guilds(discord.Object(id=EXEC_GUILD_ID))
@app_commands.checks.has_permissions(administrator=True)
async def weekly_reports_toggle(interaction: discord.Interaction, enabled: bool):
    """
    Enable or disable the automated weekly report.
    - enabled: true to start, false to stop
    """
    global WEEKLY_REPORT_ENABLED

    # If enabling but no channel configured
    if enabled and not WEEKLY_REPORT_CHANNEL_ID:
        await interaction.response.send_message(
            "❌ WEEKLY_REPORT_CHANNEL_ID is not configured. Set it in the environment.",
            ephemeral=True,
        )
        return

    # Update the in-memory flag
    WEEKLY_REPORT_ENABLED = enabled

    # Start/stop task accordingly
    if enabled:
        if not weekly_report_task.is_running():
            weekly_report_task.start()
        await interaction.response.send_message(
            "✅ Weekly reports have been enabled.", ephemeral=True
        )
    else:
        if weekly_report_task.is_running():
            weekly_report_task.stop()
        await interaction.response.send_message(
            "🛑 Weekly reports have been disabled.", ephemeral=True
        )


# -----------------------------
# Background Tasks
# -----------------------------
@tasks.loop(hours=168)  # Run every week (168 hours)
async def weekly_report_task():
    """
    Posts weekly attendance report to configured channel.
    Runs every Sunday at the time the bot was started.
    """
    if not WEEKLY_REPORT_CHANNEL_ID:
        return  # Report channel not configured
    
    try:
        channel = bot.get_channel(int(WEEKLY_REPORT_CHANNEL_ID))
        if not channel:
            logger.error(f"Weekly report channel {WEEKLY_REPORT_CHANNEL_ID} not found")
            return
        
        # Get top 5 for the week
        leaderboard_data, error = calculate_leaderboard(days=7, top_n=5)
        
        if error:
            logger.error(f"Failed to generate weekly report: {error}")
            return
        
        if not leaderboard_data:
            # No data for the week, skip report
            return
        
        # Build report embed using shared function
        embed = build_leaderboard_embed(
            leaderboard_data=leaderboard_data,
            title="📊 Weekly Office Report",
            footer_text="Keep up the great work! 🎉 • Excluding auto-signouts at 4 AM"
        )
        
        # Add introductory text and adjust color
        embed.description = "Here are the top office attendees from last week:\n\n" + embed.description
        embed.color = 0x3498DB  # Blue for weekly report
        
        await channel.send(embed=embed)
        logger.info(f"Weekly report posted to channel {WEEKLY_REPORT_CHANNEL_ID}")
        
    except Exception as e:
        logger.error(f"Error posting weekly report: {e}")


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
    
    # Start the weekly report background task if enabled
    if WEEKLY_REPORT_ENABLED:
        if WEEKLY_REPORT_CHANNEL_ID and not weekly_report_task.is_running():
            weekly_report_task.start()
            logger.info(f"Weekly report task started (every 7 days).")
    else:
        logger.info("Weekly report task disabled by WEEKLY_REPORT_ENABLED=false.")


if __name__ == "__main__":
    bot.run(TOKEN)
