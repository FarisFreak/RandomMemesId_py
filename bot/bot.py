import datetime
import logging
import json
import os
from pathlib import Path
from io import BytesIO

import requests
import discord
from discord.ext import tasks
from instagrapi import Client as IGClient
from PIL import Image
import ffmpeg
import shutil
import asyncio

from modules import Config, Media

# Ensure logs folder exists
os.makedirs("logs", exist_ok=True)

# Load config
cf = Config().load()
cfg_discord = cf['discord']
cfg_instagram = cf['instagram']
cfg_delay = cf['delay']

# Setup logging
LOG_FILE = f"logs/{datetime.datetime.now().strftime('%Y-%m-%d_T%H-%M-%S')}.log"
logging.basicConfig(filename=LOG_FILE, filemode="w", format="%(asctime)s %(levelname)s %(message)s", level=logging.DEBUG)


class QueueManager:
    def __init__(self):
        logging.info("[QueueManager] Initializing queue")
        self.queue_dir = Path('_queue')
        self.media_dir = self.queue_dir / 'media'
        self.queue_file = self.queue_dir / 'list.json'

        self.queue_dir.mkdir(exist_ok=True)
        self.media_dir.mkdir(exist_ok=True)
        self.queue = self.load_queue()

    def load_queue(self):
        if self.queue_file.exists():
            try:
                with open(self.queue_file) as f:
                    logging.info("[QueueManager] Loading existing queue")
                    return json.load(f)
            except Exception as e:
                logging.error(f"[QueueManager] Failed to load queue: {e}")
        return []

    def save(self):
        with open(self.queue_file, 'w') as f:
            json.dump(self.queue, f)
        logging.info("[QueueManager] Queue saved")

    def add(self, data):
        logging.info("[QueueManager] Adding item to queue")
        self.queue.append(data)
        self.save()

    def get_first(self, pop=False):
        if not self.queue:
            return None
        item = self.queue.pop(0) if pop else self.queue[0]
        logging.info(f"[QueueManager] Retrieved item: {item}")
        if pop:
            self.save()
        return item

    def remove_by_id(self, message):
        logging.info("[QueueManager] Removing item by ID")
        self.queue = [q for q in self.queue if q["id"] != message.message_id]
        self.save()

    def length(self):
        return len(self.queue)

    def raw(self):
        return self.queue

    def info(self):
        return {"queue": len(self.queue)}


class InstagramClient:
    def __init__(self):
        self.client = IGClient()
        session_path = Path('session.json')
        if session_path.exists():
            self.client.load_settings(session_path)
        else:
            self.client.login(cfg_instagram['username'], cfg_instagram['password'])
            self.client.dump_settings(session_path)

    def process_media(self, media, id, idx):
        url = media['url']
        media_type = media['validate']['type']
        filename_base = f"_queue/media/{id}/{idx}_{id}"
        response = requests.get(url)

        if media_type == 'PHOTO':
            path = f"{filename_base}.jpg"
            Image.open(BytesIO(response.content)).convert('RGB').save(path)
        elif media_type == 'VIDEO':
            temp_path = f"{filename_base}_temp_{media['filename']}"
            path = f"{filename_base}.mp4"
            with open(temp_path, 'wb') as f:
                f.write(response.content)
            ffmpeg.input(temp_path).output(path, loglevel="quiet").run(overwrite_output=True)
        else:
            return None

        return path

    def upload_queue(self):
        queue = QueueManager()
        if queue.length() == 0:
            return {"id": None, "status": False}

        item = queue.get_first(pop=True)
        id = item['id']
        media_paths = []
        os.makedirs(f'_queue/media/{id}', exist_ok=True)

        for idx, media in enumerate(item['attachments'], start=1):
            logging.info(f"[Instagram] Processing media: {media}")
            path = self.process_media(media, id, idx)
            if path:
                media_paths.append(path)

        if not media_paths:
            return {"id": id, "status": False}

        if len(media_paths) > 1:
            self.client.album_upload(media_paths, cfg_instagram['caption'])
        elif media['validate']['type'] == 'PHOTO':
            self.client.photo_upload(media_paths[0], cfg_instagram['caption'])
        elif media['validate']['type'] == 'VIDEO':
            self.client.video_upload(media_paths[0], cfg_instagram['caption'])

        shutil.rmtree(f'_queue/media/{id}', ignore_errors=True)
        return {"id": id, "status": True}


class DiscordClient(discord.Client):
    async def update_queue(self):
        channel = self.get_channel(cfg_discord['queue_channel_id'])
        await channel.edit(name=f"Queue : {QueueManager().length()}")
        logging.info(f"[Discord] Updated queue : {QueueManager().length()}")

    @tasks.loop(minutes=cfg_delay)
    async def upload_meme(self):
        if (QueueManager().length() != 0):
            ig = InstagramClient()
            result = ig.upload_queue()
            if result['status']:
                channel = self.get_channel(cfg_discord['submit_channel_id'])
                msg = await channel.fetch_message(result['id'])
                await msg.clear_reactions()
                await msg.add_reaction('✅')

    async def on_connect(self):
        logging.info("[Discord] Connected")
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="any meme submission 👀"))

    async def on_ready(self):
        logging.info("[Discord] Bot is ready")
        print(f'Logged in as {self.user}!')
        self.upload_meme.start()
        await self.update_queue()

    async def on_message(self, message):
        if message.author.bot or message.author.id == self.user.id:
            return
        if message.guild.id != cfg_discord['guild_id'] or message.channel.id != cfg_discord['submit_channel_id']:
            return

        logging.info("[Discord] New submission received")

        if not message.attachments:
            await message.delete()
            return

        is_valid = True
        attachments_data, files_to_embed = [], []

        for attachment in message.attachments:
            valid = Media.validate(attachment)
            if not valid['status']:
                is_valid = False

            attachments_data.append({
                "filename": attachment.filename,
                "url": attachment.url,
                "validate": valid
            })

            try:
                buffer = await attachment.read()
                files_to_embed.append(discord.File(fp=BytesIO(buffer), filename=attachment.filename))
            except Exception as e:
                logging.error(f"[Discord] Error reading attachment {attachment.filename}: {e}")
                is_valid = False

        if is_valid:
            media_entry = {
                "id": message.id,
                "author": {
                    "id": message.author.id,
                    "name": message.author.global_name
                },
                "attachments": attachments_data,
                "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            QueueManager().add(media_entry)
            await self.update_queue()

        if (cfg_discord['log_channel_id'] is not None) or (cfg_discord['log_channel_id'] != 0):
            log_channel = self.get_channel(cfg_discord['log_channel_id'])
            embed = discord.Embed(
                title="New Submission",
                description=f"Submission from {message.author.mention}",
                color=0x00ff00,
                timestamp=datetime.datetime.now()
            ).add_field(name="ID", value=message.id)\
            .add_field(name="Author", value=message.author.mention)\
            .add_field(name="Attachments", value=len(message.attachments))\
            .set_footer(text=f"ID : {message.id}")

            await log_channel.send(embed=embed, files=files_to_embed)

        await message.add_reaction('🕒' if is_valid else '❌')

    async def on_raw_message_delete(self, payload):
        QueueManager().remove_by_id(payload)


def main():
    intents = discord.Intents.default()
    intents.message_content = True

    client = DiscordClient(intents=intents)
    try:
        client.run(cfg_discord['token'])
    except KeyboardInterrupt:
        logging.info("[Main] Shutting down")


if __name__ == "__main__":
    main()
