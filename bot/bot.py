import discord
import json
import os
import datetime
import shutil
import logging
import asyncio
import io
from motor.motor_asyncio import AsyncIOMotorClient
from utils import Media

# Ensure the logs directory exists
os.makedirs("../logs", exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("../logs/bot.log", encoding="utf-8"),  # Save logs to a file
        logging.StreamHandler()  # Print logs to console
    ]
)

# Load configuration
try:
    with open('../config/config.json') as f:
        config = json.load(f)
        _bot_config = config['bot']
        _db_config = config['mongodb']

        if not all([_bot_config.get('guild_id'), _bot_config.get('submit_channel_id'), _db_config.get('uri')]):
            raise Exception("Missing required configuration values")
        
except Exception as e:
    logging.error(f"Failed lo load config: {e}")
    exit(1)
    

# MongoDB connection setup
try:
    mongodb_client = AsyncIOMotorClient(_db_config['uri'], serverSelectionTimeoutMS=5000)
    _db = mongodb_client[_db_config['db_name']]
    logging.info("Successfully connected to MongoDB")
except Exception as e:
    logging.error(f"Failed to connect to MongoDB: {e}")
    exit(1)

_collections = _db['queue']


class BotClient(discord.Client):
    def __init__(self, *, intents: discord.Intents, **kwargs):
        super().__init__(intents=intents, **kwargs)
        self._prev_queue = -1

    async def on_ready(self):
        logging.info(f'Logged on as {self.user}')
        asyncio.create_task(self._poll_status_changes())
        asyncio.create_task(self._update_queue())

    async def on_message(self, message: discord.Message):
        """Handle incoming messages."""
        if self._should_ignore_message(message):
            return
        
        logging.info(f"Processing message from {message.author.name} (ID: {message.id})")

        if not self._is_valid_message(message):
            await self._handle_invalid_message(message)
            return

        media_type = await self._validate_attachments(message)
        if not media_type:
            await self._handle_invalid_message(message)
            return

        db_entry = await self._process_attachments(message, media_type)
        await self._save_to_db_and_react(message, db_entry)
        await self._log_queue(message)

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        """Handle raw message delete events."""
        if not self._should_ignore_raw_message(payload):
            return

        logging.info(f"Message delete event received for message ID: {payload.message_id}")
        await self._handle_message_deletion(payload.message_id)

    # === Helper Methods ===

    def _is_valid_message(self, message: discord.Message) -> bool:
        """Check if the message meets validity conditions."""
        if not message.attachments:
            logging.info(f"Empty attachment in message ID: {message.id}")
            return False

        if len(message.attachments) > 10:
            logging.info(f"Attachments exceed limit of 10 in message ID: {message.id}")
            return False

        return True

    def _should_ignore_raw_message(self, payload: discord.RawMessageDeleteEvent) -> bool:
        """Check if the raw message should be ignored."""
        if payload.guild_id != int(_bot_config['guild_id']) or payload.channel_id != int(_bot_config['submit_channel_id']):
            logging.debug(f"Ignoring raw message outside allowed guild/channel (ID: {payload.message_id})")
            return False
        return True

    def _should_ignore_message(self, message: discord.Message) -> bool:
        """Check if the message should be ignored."""
        conditions = [
            message.author.bot or message.author.id == self.user.id,
            message.guild.id != _bot_config['guild_id'],
            message.channel.id != _bot_config['submit_channel_id'],
        ]
        if any(conditions):
            logging.debug(f"Ignoring message ID {message.id} due to conditions")
            return True
        return False

    async def _validate_attachments(self, message: discord.Message) -> list:
        """Validate all attachments in the message."""
        media_type = []
        for attachment in message.attachments:
            valid = Media.validate(attachment)
            if not valid['status']:
                logging.warning(f"Invalid attachment ID {attachment.id} in message ID {message.id}")
                return []  # Return empty list if any attachment is invalid
            media_type.append({
                'id': attachment.id,
                'valid': valid
            })
        logging.info(f"All attachments in message ID {message.id} are valid")
        return media_type

    async def _handle_invalid_message(self, message: discord.Message):
        """Handle invalid messages by deleting them."""
        logging.warning(f"Invalid attachment(s) in message ID: {message.id}. Deleting message.")
        await message.delete()

    async def _save_to_db_and_react(self, message: discord.Message, db_entry: dict):
        """Save the message entry to MongoDB and react to the message."""
        _error_db = False
        try:
            await _collections.insert_one(db_entry)
            logging.info(f"Inserted message ID {message.id} into MongoDB")
        except Exception as e:
            _error_db = True
            logging.error(f"Failed to insert message ID {message.id} into MongoDB: {e}")

        reaction = '❌' if db_entry["stop"] and _error_db == True else '🕒'
        await message.add_reaction(reaction)
        logging.info(f"Reacted with '{reaction}' to message ID {message.id}")

    async def _save_attachment(self, message_id: int, attachment: discord.Attachment, base_path: str, idx: int) -> dict:
        """Save a single attachment and return its metadata."""
        _, ext = os.path.splitext(attachment.filename)
        filename = f'{idx}_{message_id}_{attachment.id}{ext}'
        fullpath = os.path.join(base_path, filename)

        try:
            await attachment.save(fullpath)
            logging.info(f"Saved attachment ID {attachment.id} to {fullpath}")
        except Exception as e:
            logging.error(f"Failed to save attachment ID {attachment.id}: {e}")
            raise ValueError(f"Failed to save attachment {attachment.id}: {e}")

        return {
            "id": attachment.id,
            "ext": ext,
            "filename": filename
        }

    async def _process_attachments(self, message: discord.Message, media_type: list) -> dict:
        """Process attachments, save files, and prepare DB entry."""
        message_id = message.id
        base_path = self._create_media_directory(message_id)

        attachments_metadata = []
        errors = []

        for idx, attachment in enumerate(message.attachments, start=1):
            media_info = next((item for item in media_type if item['id'] == attachment.id), None)
            media_type_name = media_info['valid']['type'] if media_info else "UNKNOWN"
            logging.info(f"Processing attachment ID {attachment.id}: {attachment.filename}, Size: {attachment.size} bytes")
            try:
                metadata = await self._save_attachment(message_id, attachment, base_path, idx)
                metadata["type"] = media_type_name
                attachments_metadata.append(metadata)
                logging.info(f"Processed attachment ID {attachment.id} successfully")
            except ValueError as e:
                errors.append(str(e))
                logging.error(f"Error processing attachment ID {attachment.id}: {e}")

        status = "pending" if not errors else "error"
        stop = bool(errors)
        logging.info(f"Message ID {message_id} processed with status: {status}, stop: {stop}")

        return {
            "id": message_id,
            "author": {
                "id": message.author.id,
                "name": message.author.name
            },
            "caption": message.content if len(message.content) > 0 else None,
            "attachments": attachments_metadata,
            "date": datetime.datetime.now(),
            "status": status,
            "priority": 0,
            "stop": stop,
            "error": errors,
            "reacted": False,
            "updated_at": None,
            "log_message_id": None
        }

    def _create_media_directory(self, message_id: int) -> str:
        """Create a directory for media files associated with a message."""
        base_path = f'../.queue/media/{message_id}'
        os.makedirs(base_path, exist_ok=True)
        logging.info(f"Created directory for message ID {message_id}: {base_path}")
        return base_path

    async def _handle_message_deletion(self, message_id: int):
        """Handle message deletion by removing media files and DB entry."""
        media_status = await self._get_media_status(message_id)
        if media_status and media_status['status'] != 'success':
            await self._delete_media_files(message_id)
            await self._delete_message_from_db(message_id)

    async def _get_media_status(self, message_id: int) -> dict:
        """Fetch media status from MongoDB."""
        logging.info(f"Fetching media status for message ID: {message_id}")
        result = await _collections.find_one({"id": message_id})
        if not result:
            logging.warning(f"No media status found for message ID: {message_id}")
        return result

    async def _delete_media_files(self, message_id: int):
        """Delete media files associated with the message."""
        logging.info(f"Deleting media files (if exist) for message ID: {message_id}")
        base_path = f'../.queue/media/{message_id}'
        try:
            if os.path.exists(base_path):
                shutil.rmtree(base_path)
                logging.info(f"Deleted media files for message ID: {message_id}")
            else:
                logging.info(f"No media files found to delete for message ID: {message_id}")
        except Exception as e:
            logging.error(f"Failed to delete media files for message ID {message_id}: {e}")

    async def _delete_message_from_db(self, message_id: int):
        """Delete the message entry from MongoDB."""
        result = await _collections.find_one({"id": message_id})
        if not result:
            logging.warning(f"Message ID {message_id} not found in MongoDB")
            return

        delete_result = await _collections.delete_one({"id": message_id})
        if delete_result.deleted_count > 0:
            logging.info(f"Successfully deleted message ID {message_id} from MongoDB")
        else:
            logging.warning(f"Message ID {message_id} not found in MongoDB")

    async def _log_queue(self, message: discord.Message):
        """Log queue to log channel."""
        # Check if log channel ID is configured
        if not _bot_config.get('log_channel_id'):
            logging.warning("Log channel ID is not configured. Skipping log.")
            return

        # Get the log channel
        log_channel = self.get_channel(int(_bot_config['log_channel_id']))
        if not log_channel:
            logging.error(f"Log channel with ID {_bot_config['log_channel_id']} not found.")
            return

        logging.info(f"Logging submission from {message.author.name} (ID: {message.id}) to log channel")

        try:
            _current_date = datetime.datetime.now()
            # Create an embed for the log
            embed = discord.Embed(
                title="New Submission",
                description=f"Submission from {message.author.mention}",
                color=0x00ff00,
                timestamp=_current_date
            )
            embed.add_field(name="ID", value=message.id, inline=False)
            embed.add_field(name="Author", value=message.author.mention, inline=False)
            embed.add_field(name="Caption", value=message.content if len(message.content) > 0 else "-", inline=False)
            embed.add_field(name="Attachments", value=len(message.attachments), inline=False)
            embed.add_field(name="Permalink", value=f"[Jump to Message](https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id})", inline=False)
            embed.add_field(name="Status", value="🕒 Pending", inline=False)
            embed.set_footer(text=f"Last Update: {_current_date.strftime('%d-%m-%Y %H:%M:%S')}")

            # Convert attachments to discord.File objects
            files = []
            for attachment in message.attachments:
                file_data = await attachment.read()  # Read the attachment data
                file = discord.File(
                    fp=io.BytesIO(file_data),  # Wrap the data in a BytesIO object
                    filename=attachment.filename
                )
                files.append(file)

            # Send the embed and files to the log channel
            _log_message = await log_channel.send(embed=embed, files=files)
            await self._update_queue_log_chat(message.id, _log_message.id)
            logging.info(f"Successfully logged submission (ID: {message.id}) to log channel")

        except Exception as e:
            logging.error(f"Failed to log submission (ID: {message.id}) to log channel: {e}")

    async def _update_queue_log_chat(self, message_id: int, message_log_id: int):
        """Update the log message ID in the database."""
        try:
            logging.info(f"Updating log message ID for submission (ID: {message_id})")
            result = await _collections.find_one_and_update(
                {"id": message_id},
                {"$set": {"log_message_id": message_log_id}}
            )
            if result:
                logging.info(f"Updated log message ID for submission (ID: {message_id})")
            else:
                logging.warning(f"No document found to update log message ID for submission (ID: {message_id})")
        except Exception as e:
            logging.error(f"Failed to update log chat message ID (ID: {message_id}): {e}")

    async def _update_queue(self):
        """Update for queue length."""

        while True:
            try:
                query = {
                    "status": "pending",
                    "stop": False
                }
                
                results = await _collections.find(query).to_list(length=None)
                current_queue_length = len(results)

                if current_queue_length != self._prev_queue:
                    logging.info(f"Updated queue length: {current_queue_length}")
                    self._prev_queue = current_queue_length

                    await self.change_presence(activity=\
                                    discord.Activity(type=discord.ActivityType.watching, name="any meme submission 👀") if current_queue_length < 1 else \
                                        discord.Activity(type=discord.ActivityType.competing, name=f"Queue : {current_queue_length}"))

            except Exception as e:
                logging.error(f"Error while updating queue: {e}")

            await asyncio.sleep(5)

    async def _poll_status_changes(self):
        """Poll for changes in the 'status' field where 'reacted' is false."""
        while True:
            try:
                query = {
                    "status": {"$in": ['success', 'failed', 'uploading']},
                    "reacted": False
                }
                async for item in _collections.find(query):
                    logging.info(f"Detected change in document ID {item['_id']}: {item}")
                    await self._react_poll_changes(message_id=item['id'], status=item['status'], log_message_id=item['log_message_id'])
            except Exception as e:
                logging.error(f"Error while polling status changes: {e}")
            await asyncio.sleep(5)

    async def _react_poll_changes(self, message_id: int, status: str, log_message_id: int = None):
        """React to poll changes."""
        # Determine the reaction and status details
        reaction_map = {
            'success': ('✅', "success", "✅ Uploaded"),
            'failed': ('❌', "failed", "❌ Failed to upload"),
            'uploading': ('⌛', "uploading", "⌛ Uploading"),
            'pending': ('🕒', "pending", "Queue pending")
        }
        default_reaction = ('❔', "unknown", "UNK")

        # Get the reaction and status description
        reaction, status_desc, status_msg = reaction_map.get(status, default_reaction)

        if status in reaction_map:
            logging.info(f"Processing status '{status_desc}' for message ID {message_id}. Adding reaction: {reaction}")
        else:
            logging.warning(f"Unknown status '{status}' for message ID {message_id}. Using default reaction: {reaction}")

        try:
            # Fetch and process the main message
            await self._fetch_and_process_main_message(message_id, reaction, reaction_map)
            
            # Process log reply if log_message_id is provided
            if log_message_id and _bot_config.get('log_channel_id'):
                await self._process_log_update(log_message_id, status_msg)

            # Update the database to mark 'reacted' as True
            await self._update_database_reacted_flag(message_id)

        except discord.NotFound:
            logging.error(f"Message ID {message_id} not found in channel {_bot_config['submit_channel_id']}")
        except discord.Forbidden:
            logging.error(f"Bot lacks permission to manage reactions for message ID {message_id}")
        except Exception as e:
            logging.error(f"Unexpected error while processing poll changes for message ID {message_id}: {e}")

    async def _fetch_and_process_main_message(self, message_id: int, reaction: str, reaction_map: dict):
        """Fetch the main message, remove previous reactions, and add a new reaction."""
        # Fetch the message from the channel
        channel = self.get_channel(_bot_config['submit_channel_id'])
        _message = await channel.fetch_message(message_id)
        logging.info(f"Fetched message ID {message_id} from channel {_bot_config['submit_channel_id']}")

        # Remove previous reactions added by the bot
        for emoji, _, _ in reaction_map.values():
            try:
                await _message.remove_reaction(emoji, self.user)
                logging.debug(f"Removed previous reaction '{emoji}' from message ID {message_id}")
            except discord.NotFound:
                logging.debug(f"No previous reaction '{emoji}' found for message ID {message_id}")

        # Add the new reaction to the message
        await _message.add_reaction(reaction)
        logging.info(f"Added reaction '{reaction}' to message ID {message_id}")

        return _message

    async def _process_log_update(self, log_message_id: int, status_msg: str):
        """Process the log reply by fetching the log message and replying to it."""
        logging.info(f"Processing log reply for message ID {log_message_id} in log channel ID {_bot_config['log_channel_id']}")

        try:
            # Get the log channel
            channel_log = self.get_channel(int(_bot_config['log_channel_id']))
            if not channel_log:
                logging.error(f"Log channel with ID {_bot_config['log_channel_id']} not found.")
                return

            logging.debug(f"Successfully fetched log channel ID {_bot_config['log_channel_id']}")

            # Fetch the log message
            _message_log = await channel_log.fetch_message(log_message_id)
            logging.debug(f"Successfully fetched log message ID {log_message_id}")

            # Reply to the log message
            try:
                _new_embeds = self._reconstruct_embed(_message_log, status_msg)
                await _message_log.edit(embed=_new_embeds)
                logging.info(f"Log message updated {log_message_id} with status: {status_msg}")
                
            except AttributeError:
                # Fallback for older discord.py versions
                await channel_log.send(f"{_message_log.author.mention} {status_msg}")
                logging.info(f"Fallback reply sent to log message ID {log_message_id} with status: {status_msg}")

        except discord.NotFound:
            logging.error(f"Log message with ID {log_message_id} not found in channel {_bot_config['log_channel_id']}.")
        except discord.Forbidden:
            logging.error(f"Bot lacks permission to fetch messages in log channel {_bot_config['log_channel_id']}.")
        except Exception as e:
            logging.error(f"Unexpected error while processing log reply for message ID {log_message_id}: {e}")

    async def _update_database_reacted_flag(self, message_id: int):
        """Update the database to mark 'reacted' as True."""
        update_result = await _collections.update_one({"id": message_id}, {"$set": {"reacted": True}})
        if update_result.modified_count > 0:
            logging.info(f"Updated 'reacted' to True for message ID {message_id} in MongoDB")
        else:
            logging.warning(f"No updates made to 'reacted' for message ID {message_id} in MongoDB")

    def _reconstruct_embed(self, message: discord.Message, status: str = "UNK"):
        """Reconstruct an embed from the message's existing embed."""
        try:
            # Check if the message contains any embeds
            if not message.embeds:
                logging.warning(f"No embeds found in message ID {message.id}")
                return None

            # Get the first embed (or iterate through all embeds if needed)
            original_embed = message.embeds[0]

            # Reconstruct the embed
            reconstructed_embed = discord.Embed(
                title=original_embed.title,
                description=original_embed.description,
                color=original_embed.color,
                timestamp=original_embed.timestamp
            )

            # Add fields
            for field in original_embed.fields:
                if field.name == "Status" :
                    reconstructed_embed.add_field(name="Status", value=status, inline=False)
                else:
                    reconstructed_embed.add_field(name=field.name, value=field.value, inline=field.inline)

            # Add footer
            if original_embed.footer and original_embed.footer.text:
                _current_date = datetime.datetime.now()
                reconstructed_embed.set_footer(text=f"Last Update: {_current_date.strftime('%d-%m-%Y %H:%M:%S')}")

            # Add author (if exists)
            if original_embed.author and original_embed.author.name:
                reconstructed_embed.set_author(name=original_embed.author.name, icon_url=original_embed.author.icon_url)


            logging.info(f"Successfully reconstructed embed from message ID {message.id}")
            return reconstructed_embed

        except Exception as e:
            logging.error(f"Failed to reconstruct embed from message ID {message.id}: {e}")
            return None


# Initialize bot
intents = discord.Intents.default()
intents.message_content = True

bot = BotClient(intents=intents)
bot.run(_bot_config['token'])