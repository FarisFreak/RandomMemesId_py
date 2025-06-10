import discord
import json
import os
import datetime
import shutil
import logging
import asyncio
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
    async def on_ready(self):
        logging.info(f'Logged on as {self.user}')
        asyncio.create_task(self._poll_status_changes())  # Mulai memantau perubahan status

    async def on_message(self, message: discord.Message):
        """Handle incoming messages."""
        if self._should_ignore_message(message):
            return

        logging.info(f"Processing message from {message.author.name} (ID: {message.id})")

        media_type = await self._validate_attachments(message)
        if not media_type:
            await self._handle_invalid_message(message)
            return

        db_entry = await self._process_attachments(message, media_type)
        await self._save_to_db_and_react(message, db_entry)

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        """Handle raw message delete events."""
        if not self._should_ignore_raw_message(payload):
            return

        logging.info(f"Message delete event received for message ID: {payload.message_id}")
        await self._handle_message_deletion(payload.message_id)

    # === Helper Methods ===

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
            not message.attachments
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
            "attachments": attachments_metadata,
            "date": datetime.datetime.now(),
            "status": status,
            "priority": 0,
            "stop": stop,
            "error": errors,
            "reacted": False
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
                    await self._react_poll_changes(message_id=item['id'], status=item['status'])
            except Exception as e:
                logging.error(f"Error while polling status changes: {e}")
            await asyncio.sleep(5)

    async def _react_poll_changes(self, message_id: int, status: str):
        """React to poll changes."""
        # Determine the reaction based on the status
        reaction_map = {
            'success': ('✅', "success"),
            'failed': ('❌', "failed"),
            'uploading': ('⌛', "uploading"),
            'pending': ('🕒', "pending")
        }
        default_reaction = ('❔', "unknown")

        # Get the reaction and status description
        reaction, status_desc = reaction_map.get(status, default_reaction)

        if status in reaction_map:
            logging.info(f"Processing status '{status_desc}' for message ID {message_id}. Adding reaction: {reaction}")
        else:
            logging.warning(f"Unknown status '{status}' for message ID {message_id}. Using default reaction: {reaction}")

        try:
            # Fetch the message from the channel
            channel = self.get_channel(_bot_config['submit_channel_id'])
            _message = await channel.fetch_message(message_id)
            logging.info(f"Fetched message ID {message_id} from channel {_bot_config['submit_channel_id']}")

            # Remove previous reactions added by the bot
            for emoji, _ in reaction_map.values():
                try:
                    await _message.remove_reaction(emoji, self.user)
                    logging.debug(f"Removed previous reaction '{emoji}' from message ID {message_id}")
                except discord.NotFound:
                    logging.debug(f"No previous reaction '{emoji}' found for message ID {message_id}")

            # Add the new reaction to the message
            await _message.add_reaction(reaction)
            logging.info(f"Added reaction '{reaction}' to message ID {message_id}")

            # Update the database to mark 'reacted' as True
            update_result = await _collections.update_one({"id": message_id}, {"$set": {"reacted": True}})
            if update_result.modified_count > 0:
                logging.info(f"Updated 'reacted' to True for message ID {message_id} in MongoDB")
            else:
                logging.warning(f"No updates made to 'reacted' for message ID {message_id} in MongoDB")

        except discord.NotFound:
            logging.error(f"Message ID {message_id} not found in channel {_bot_config['submit_channel_id']}")
        except discord.Forbidden:
            logging.error(f"Bot lacks permission to manage reactions for message ID {message_id}")
        except Exception as e:
            logging.error(f"Unexpected error while processing poll changes for message ID {message_id}: {e}")



# Initialize bot
intents = discord.Intents.default()
intents.message_content = True

bot = BotClient(intents=intents)
bot.run(_bot_config['token'])