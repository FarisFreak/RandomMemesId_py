import json
import os
import logging
import asyncio
import shutil
import datetime
from PIL import Image
from io import BytesIO
from pathlib import Path
from instagrapi import Client
from motor.motor_asyncio import AsyncIOMotorClient
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Ensure the logs directory exists
os.makedirs("../logs", exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("../logs/worker.log", encoding="utf-8"),  # Save logs to a file
        logging.StreamHandler()  # Print logs to console
    ]
)

# Load configuration
try:
    with open('../config/config.json') as f:
        config = json.load(f)
        _worker_config = config['worker']
        _db_config = config['mongodb']

        if not all([_worker_config.get('username'), _worker_config.get('password'), _db_config.get('uri')]):
            raise Exception("Missing required configuration values")

except Exception as e:
    logging.error(f"Failed to load config: {e}")
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

TIMESTAMP_FILE = "../config/initial_run_time.json"

class WorkerClient:
    def __init__(self):
        logging.info("Initializing worker..")
        self.client = Client()

        _session_path = Path('session.json')
        if _session_path.exists():
            logging.info("Session found. Logging in with session.")
            self.client.load_settings(_session_path)
        else:
            logging.info("Session not found. Logging in with username & password.")
            self.client.login(_worker_config['username'], _worker_config['password'])
            self.client.dump_settings(_session_path)

        self._caption = _worker_config['caption'] or '#fyp'

        logging.info("Worker ready.")

    async def upload_media(self) -> bool:
        """Process and upload media from the queue."""
        try:
            item = await self._get_next_queue_item()
            if not item:
                logging.info("Empty queue")
                return False

            base_path = f"../.queue/media/{item['id']}"
            converted_medias = []
            _media_type = 'PHOTO'

            if len(item['attachments']) > 1:
                _media_type = 'ALBUM'
            else:
                _media_type = item['attachments'][0]['type']

            for media in item['attachments']:
                try:
                    converted_media = await self._process_media(media, base_path)
                    converted_medias.append(converted_media)
                except Exception as e:
                    logging.error(f"Failed to process media {media['filename']}: {e}")
                    raise e
                
            if not converted_medias:
                raise ValueError(f"Unknown error {item['id']} (empty media)")
            
            logging.info(f"Uploading queue {item['id']}..")
            await self._update_queue_status(item_id=item['id'], status="uploading")
            
            if len(converted_medias) > 1:
                self.client.album_upload(converted_medias, self._caption)
            elif _media_type == 'PHOTO':
                self.client.photo_upload(converted_medias[0], self._caption)
            elif _media_type == 'VIDEO':
                self.client.video_upload(converted_medias[0], self._caption)

            logging.info(f"Queue ID {item['id']} successfully processed.")
            await self._update_queue_status(item['id'], "success")
            self._cleanup_media(base_path)
            return True

        except Exception as e:
            await self._handle_processing_failure(item_id=item['id'], error=e)
            logging.error(f"Unexpected error while processing queue: {e}")
            return False

    async def _get_next_queue_item(self):
        """Fetch the next pending item from the queue."""
        logging.info("Fetching first pending queue item...")
        return await _collections.find_one({"stop": False, "status": "pending"}, sort=[("priority", 1), ("_id", 1)])

    async def _process_media(self, media: dict, base_path: str) -> str:
        """Process a single media file (convert to required format)."""
        logging.info(f"Processing media: {media['filename']}")
        original_path = Path(f"{base_path}/{media['filename']}")
        converted_path = f"{original_path}_converted"

        if media['type'] == 'PHOTO':
            return self._convert_photo(original_path, converted_path)
        elif media['type'] == 'VIDEO':
            return await self._convert_video(original_path, converted_path)
        else:
            raise ValueError(f"Unsupported media type: {media['type']}")

    def _convert_photo(self, original_path: Path, converted_path: str) -> str:
        """Convert a photo to JPG format."""
        converted_path = f"{converted_path}.jpg"
        Image.open(original_path).convert('RGB').save(converted_path)
        logging.info("Photo converted to .jpg")
        return converted_path

    async def _convert_video(self, original_path: Path, converted_path: str) -> str:
        """Convert a video using FFmpeg."""
        converted_path = f"{converted_path}.mp4"
        process = await asyncio.create_subprocess_exec(
            'ffmpeg', '-y', '-i', str(original_path), converted_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise Exception(f"FFmpeg failed: {stderr.decode()}")
        logging.info("FFmpeg finished successfully.")
        return converted_path

    async def _handle_processing_failure(self, item_id: int, error: Exception):
        """Handle failure during media processing."""
        logging.error(f"Failed to process media for queue ID {item_id}: {error}")
        await _collections.update_one(
            {"id": item_id},
            {"$set": { 
                "status": "failed", 
                "stop": True, 
                "error": [str(error)],
                "updated_at": datetime.datetime.now()
                }
            }
        )

    async def _update_queue_status(self, item_id: int, status: str):
        """Update the status of a queue item in MongoDB."""
        logging.info(f"Updating queue ID {item_id} status to '{status}'")
        await _collections.update_one(
            {"id": item_id}, 
            {"$set": {
                "status": status, 
                "reacted": False,
                "updated_at": datetime.datetime.now()
                }
            }
        )

    def _cleanup_media(self, base_path: str):
        """Clean up media files after processing."""
        logging.info(f"Cleaning up media for base path: {base_path}")
        if os.path.exists(base_path):
            shutil.rmtree(base_path)

def _delay_until_next_hour() -> datetime.datetime:
    now = datetime.datetime.now()
    next_hour = (now + datetime.timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    logging.info(f"First run at {next_hour}")
    return next_hour

def save_initial_run_time(now: datetime.datetime, next_hour: datetime.datetime):
    initial_run_time = now.isoformat()
    next_run_time = next_hour.isoformat()
    os.makedirs(os.path.dirname(TIMESTAMP_FILE), exist_ok=True)
    with open(TIMESTAMP_FILE, "w") as f:
        json.dump({"initial_run_time": initial_run_time, "next_run_time": next_run_time}, f)
    logging.info("Initial run time saved to file")

async def main():
    worker = WorkerClient()

    _run_timestamp = datetime.datetime.now()
    _delay_next_hour_timestamp = _delay_until_next_hour()

    save_initial_run_time(_run_timestamp, _delay_next_hour_timestamp)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(worker.upload_media, 'interval', minutes=int(_worker_config['delay']) or 60, next_run_time=_delay_next_hour_timestamp)

    logging.info("Starting scheduler...")
    scheduler.start()

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        logging.info("Async task was cancelled.")
    except KeyboardInterrupt:
        logging.info("Shutting down scheduler gracefully...")
        scheduler.shutdown(wait=True)
    finally:
        logging.info("Scheduler has been stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Program terminated by user.")

