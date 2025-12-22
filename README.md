# IEEE Office Discord Bot

Discord bot for tracking IEEE office attendance. Integrates with the [ieee-office-backend](https://github.com/ieee-uottawa/ieee-office-backend) server to provide real-time office presence tracking through Discord. Features interactive buttons for check-in/check-out and comprehensive admin commands.

## Features

- **Real-time Dashboard**: Persistent embed showing current office occupants
- **Interactive Buttons**:
  - "Leaving 🟥" - Sign out from the office (executive server only)
  - "Refresh 🔄" - Manually refresh the dashboard
- **Dual-Server Support**:
  - Executive server with full control capabilities
  - Community server with read-only dashboard viewing
- **Auto-refresh**: Dashboard updates every minute automatically
- **Admin Commands**: Member management, manual check-in/out, history viewing
- **Backend Integration**: Communicates with `ieee-office-backend` REST API
- **Error Handling**: Displays connection status and server errors

## Architecture

The bot connects to the `ieee-office-backend` HTTP service and provides a Discord interface for:

- Viewing current office presence
- Signing in/out via Discord ID
- Managing registered members (linking Discord IDs to RFID UIDs)
- Viewing scan history and visit statistics

## Requirements

- Python 3.12+
- Discord bot token
- Running `ieee-office-backend` server
- Two Discord servers (guilds): executive and community

## Setup Instructions

### 1. Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

Edit `.env` with your values:

```bash
# Required
DISCORD_TOKEN=your_discord_bot_token_here
SERVER_URL=http://localhost:8080
EXEC_GUILD_ID=your_exec_guild_id_here
COMMUNITY_GUILD_ID=your_community_guild_id_here

# Optional
OFFICE_TRACKER_CHANNEL_NAME=office-tracker
```

**How to get Discord IDs:**

1. Enable Developer Mode in Discord (Settings → Advanced → Developer Mode)
2. Right-click on a server → Copy Server ID

### 2. Local Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/ieee-uottawa/ieee-office-discord-bot.git
   cd ieee-office-discord-bot
   ```

2. Create a virtual environment and activate it:

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Run the bot:

   ```bash
   python main.py
   ```

### 3. Docker Setup

Build and run with Docker:

```bash
docker build -t ieee-office-discord-bot .
docker run --env-file .env --name ieee-office-discord-bot ieee-office-discord-bot
```

Or use Docker Compose:

```bash
docker compose up --build
```

## Bot Setup in Discord

### 1. Initial Setup

Run `/setup` in the channel where you want the dashboard to appear. This creates the persistent embed with interactive buttons.

**Requirements:**

- Must have administrator permissions
- Bot must have permissions to send messages and embeds in the channel

### 2. Member Registration

Before members can use the system, register them with their Discord ID and RFID UID:

```bash
/add_member @user uid:"ABC123DEF456"
```

This links their Discord account to their RFID tag for seamless check-in/out.

## Available Commands

### Global Commands (All Servers)

- `/setup` - Create the persistent dashboard embed (requires admin)

### Executive Server Commands

#### Member Management

- `/add_member @user uid:"..." [name:"..."]` - Register a new member with RFID UID
- `/list_members` - View all registered members
- `/scan_history` - View last 10 RFID scans

#### Manual Control

- `/signin @member` - Manually sign in a member (requires admin)
- `/signout @member` - Sign out a member
- `/signout_all` - Sign out all members (requires admin)

#### History & Analytics

- `/history [limit:10]` - View recent office visits with timestamps and durations

## Interactive Buttons

### Executive Server (Control View)

- **Leaving 🟥**: Sign yourself out of the office
- **Refresh 🔄**: Manually refresh the dashboard

### Community Server (Read-Only View)

- **Refresh 🔄**: Manually refresh the dashboard

**Note:** Refresh has a 15-second cooldown to prevent spam.

## Environment Variables

| Variable                       | Required | Default                 | Description                               |
| ------------------------------ | -------- | ----------------------- | ----------------------------------------- |
| `DISCORD_TOKEN`                | Yes      | -                       | Discord bot token from developer portal   |
| `SERVER_URL`                   | Yes      | `http://localhost:8080` | URL of ieee-office-backend server         |
| `EXEC_GUILD_ID`                | Yes      | -                       | Discord server ID with admin controls     |
| `COMMUNITY_GUILD_ID`           | Yes      | -                       | Discord server ID with read-only access   |
| `OFFICE_TRACKER_CHANNEL_NAME`  | No       | `office-tracker`        | Channel name for dashboard                |

## How It Works

1. **Backend Integration**: Bot polls the backend API every minute for current attendees
2. **Dashboard Updates**: Refreshes embeds across all servers showing current occupants
3. **Button Interactions**:
   - "Leaving" button → POST to `/sign-out-discord` endpoint
   - "Refresh" button → Triggers manual dashboard update
4. **Commands**: Admin commands interact with backend REST API for member management

## Permissions Required

The bot needs these Discord permissions:

- Read Messages/View Channels
- Send Messages
- Embed Links
- Use Slash Commands
- Manage Messages (for updating embeds)

## Troubleshooting

### Bot not responding to commands

- Ensure bot has proper permissions in the channel
- Check that commands are synced (bot logs will show sync status on startup)

### Dashboard not updating

- Verify `SERVER_URL` points to running backend
- Check backend logs for errors
- Ensure bot has network access to backend server

### "Unknown User" or "Not Registered"

- Members must be registered via `/add_member` before they can check in/out
- Verify Discord ID is correctly linked to RFID UID in backend

### Connection errors in dashboard

- Backend server may be down or unreachable
- Check `SERVER_URL` configuration
- Verify firewall/network settings allow bot → backend communication

## Development

### Project Structure

```txt
.
├── main.py              # Main bot code
├── requirements.txt     # Python dependencies
├── Dockerfile          # Container build configuration
├── docker-compose.yml  # Docker Compose setup
├── .env.example        # Environment variable template
└── README.md           # This file
```

### Key Components

- **Views**: `ReadOnlyView` and `ControlView` handle button interactions
- **Global Refresh**: `global_refresh()` updates dashboards across all servers
- **Auto-refresh Task**: Background task runs every minute
- **Commands**: Slash commands for admin controls and member management

## CI/CD

GitHub Actions workflow automatically builds and pushes Docker images to Docker Hub when a release is published.

## License

MIT License - see LICENSE file for details
