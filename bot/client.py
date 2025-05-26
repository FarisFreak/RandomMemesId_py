import logging
import discord
import datetime
import asyncio

from discord.ext import tasks
from io import BytesIO

from core import Config, QueueManager
from .utils import InstagramClient, Media

class DiscordClient(discord.Client):
    def __init__(self, *, intents: discord.Intents, queue: QueueManager, **kwargs):
        logging.info("[DiscordClient] Initializing Discord client")
        super().__init__(intents=intents, **kwargs)
        self.queue = queue

        cf = Config().load()
        self._config_discord = cf['discord']
        self._config_delay = cf['delay']

        self.upload_meme.change_interval(minutes=self._config_delay)

    async def update_queue(self):
        _length = self.queue.length()
        await self.change_presence(activity=\
                                   discord.Activity(type=discord.ActivityType.watching, name="any meme submission 👀") if _length < 1 else \
                                    discord.Activity(type=discord.ActivityType.competing, name=f"Queue : {_length}"))
        logging.info(f"[Discord] Updated queue : { _length }")

    @tasks.loop(minutes=60)
    async def upload_meme(self):
        if (self.queue.length() != 0):
            ig = InstagramClient(self.queue)
            result = await ig.upload_queue()

            channel = self.get_channel(self._config_discord['submit_channel_id'])
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

    async def on_message(self, message: discord.Message):
        if message.author.bot or message.author.id == self.user.id:
            return
        if message.guild.id != self._config_discord['guild_id'] or message.channel.id != self._config_discord['submit_channel_id']:
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
                "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "pending",
                "priority": 0,
                "stop": False
            }
            self.queue.add(media_entry)
            await self.update_queue()

        if (self._config_discord['log_channel_id'] is not None) or (self._config_discord['log_channel_id'] != 0):
            log_channel = self.get_channel(self._config_discord['log_channel_id'])
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

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        self.queue.remove_by_id(payload)
        asyncio.create_task(self.update_queue())