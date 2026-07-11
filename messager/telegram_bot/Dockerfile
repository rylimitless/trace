FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py ./

# The bot polls Telegram and calls the backend at BACKEND_URL.
# Required env: TELEGRAM_BOT_TOKEN (from @BotFather), BACKEND_URL (your Render backend URL).
CMD ["python", "bot.py"]
