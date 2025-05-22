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

from pymongo import MongoClient

from modules import Config, Media

# Ensure logs folder exists
os.makedirs("logs", exist_ok=True)

# Load config
cf = Config().load()
cfg_discord = cf['discord']
cfg_instagram = cf['instagram']
cfg_delay = cf['delay']
cfg_mongo = cf['mongodb']

# Setup logging
LOG_FILE = f"logs/{datetime.datetime.now().strftime('%Y-%m-%d_T%H-%M-%S')}.log"
logging.basicConfig(filename=LOG_FILE, filemode="w", format="%(asctime)s %(levelname)s %(message)s", level=logging.DEBUG)


class QueueManager:
    def __init__(self):
        logging.info("[QueueManager] Initializing queue")
        logging.info("[QueueManager] Loading MongoDB configuration")
        logging.getLogger("pymongo").setLevel(logging.WARNING)
        self._db_username = cfg_mongo['username']
        self._db_password = cfg_mongo['password']
        self._db_host = cfg_mongo['host']
        self._db_port = cfg_mongo['port']
        self._db_name = cfg_mongo['database']

        self._db_client = MongoClient(f"mongodb://{self._db_username}:{self._db_password}@{self._db_host}:{self._db_port}/{self._db_name}?authSource={self._db_name}")
        self._db = self._db_client[self._db_name]

        self._data = self._db['queue']

        # ------

        self.queue_dir = Path('_queue')
        self.media_dir = self.queue_dir / 'media'

        self.queue_dir.mkdir(exist_ok=True)
        self.media_dir.mkdir(exist_ok=True)

    def add(self, data):
        logging.info("[QueueManager] Adding item to queue")
        self._data.insert_one(data)

    def get_first(self, pop=False):
        logging.info("[QueueManager] Retrieving first item from queue")
        
        item = self._data.find_one_and_delete({}, sort=[('_id', 1)]) if pop else self._data.find_one(sort=[('_id', 1)])
        logging.info(f"[QueueManager] Retrieved item{' and pop' if pop else '' }: {item}")
        return item

    def remove_by_id(self, message):
        logging.info("[QueueManager] Removing item by ID")
        self._data.delete_one({"id": message.message_id})

    def length(self):
        return self._data.count_documents({})

class InstagramClient:
    def __init__(self, queue : QueueManager):
        self.client = IGClient()
        self.queue = queue
        session_path = Path('session.json')
        if session_path.exists():
            self.client.load_settings(session_path)
        else:
            self.client.login(cfg_instagram['username'], cfg_instagram['password'])
            self.client.dump_settings(session_path)

    async def process_media(self, media, id, idx):
        url = media['url']
        media_type = media['validate']['type']
        filename_base = f"_queue/media/{id}/{idx}_{id}"
        response = requests.get(url)

        if media_type == 'PHOTO':
            path = f"{filename_base}.jpg"
            Image.open(BytesIO(response.content)).convert('RGB').save(path)
        elif media_type == 'VIDEO':
            path = f"{filename_base}.mp4"
            temp_path = f"{filename_base}_temp_{media['filename']}"
            with open(temp_path, 'wb') as f:
                f.write(response.content)

            process = await asyncio.create_subprocess_exec(
                'ffmpeg', '-i', temp_path, path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                logging.info(f"[Instagram] FFmpeg failed: {stderr.decode()}")
            else:
                logging.info("[Instagram] FFmpeg finished successfully.")

            # ffmpeg.input(temp_path).output(path, loglevel="quiet").run(overwrite_output=True)
        else:
            return None

        return path

    async def upload_queue(self):
        if self.queue.length() == 0:
            return {"id": None, "status": False}

        item = self.queue.get_first(pop=True)
        id = item['id']
        media_paths = []
        os.makedirs(f'_queue/media/{id}', exist_ok=True)

        for idx, media in enumerate(item['attachments'], start=1):
            logging.info(f"[Instagram] Processing media: {media}")
            path = await self.process_media(media, id, idx)
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
    def __init__(self, *, intents: discord.Intents, queue: QueueManager, **kwargs):
        logging.info("[DiscordClient] Initializing Discord client")
        super().__init__(intents=intents, **kwargs)
        self.queue = queue

    async def update_queue(self):
        _length = self.queue.length()
        await self.change_presence(activity=\
                                   discord.Activity(type=discord.ActivityType.watching, name="any meme submission 👀") if _length < 1 else \
                                    discord.Activity(type=discord.ActivityType.competing, name=f"Queue : {_length}"))
        logging.info(f"[Discord] Updated queue : { _length }")

    @tasks.loop(minutes=cfg_delay)
    async def upload_meme(self):
        if (self.queue.length() != 0):
            ig = InstagramClient(self.queue)
            result = await ig.upload_queue()

            channel = self.get_channel(cfg_discord['submit_channel_id'])
            msg = await channel.fetch_message(result['id'])
            await msg.clear_reactions()
            await msg.add_reaction('✅' if result['status'] else '❌')
            await self.update_queue()

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
            self.queue.add(media_entry)
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

            asyncio.create_task(log_channel.send(embed=embed, files=files_to_embed))

        await message.add_reaction('🕒' if is_valid else '❌')

    async def on_raw_message_delete(self, payload):
        self.queue.remove_by_id(payload)
        asyncio.create_task(self.update_queue())


def main():
    intents = discord.Intents.default()
    intents.message_content = True
    queue = QueueManager()

    client = DiscordClient(intents=intents, queue=queue)
    try:
        client.run(cfg_discord['token'])
    except KeyboardInterrupt:
        logging.info("[Main] Shutting down")


if __name__ == "__main__":
    main()
