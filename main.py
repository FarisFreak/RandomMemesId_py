import logging
import discord
import os

from core import Config, QueueManager
from bot import DiscordClient

# Ensure logs folder exists
os.makedirs("logs", exist_ok=True)

cf = Config().load()
_config_discord = cf['discord']

def main():
    intents = discord.Intents.default()
    intents.message_content = True
    queue = QueueManager()

    client = DiscordClient(intents=intents, queue=queue)
    try:
        client.run(_config_discord['token'])
    except KeyboardInterrupt:
        logging.info("[Main] Shutting down")


if __name__ == "__main__":
    main()
