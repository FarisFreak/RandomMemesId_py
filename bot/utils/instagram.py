import logging
import os
from pathlib import Path
from io import BytesIO

import requests
from instagrapi import Client as IGClient
from PIL import Image
import ffmpeg
import shutil
import asyncio
from core import QueueManager, Config


class InstagramClient:
    def __init__(self, queue : QueueManager):
        self.client = IGClient()
        self.queue = queue
        cf = Config().load()
        self._config = cf['instagram']
        session_path = Path('session.json')
        if session_path.exists():
            self.client.load_settings(session_path)
        else:
            self.client.login(self._config['username'], self._config['password'])
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
        if await self.queue.length() == 0:
            return {"id": None, "status": False}

        item = await self.queue.get_first()

        try:
            await self.queue.update_status(item['id'], 'processing')
            id = item['id']
            media_paths = []

            _path = f'_queue/media/{id}'
            os.makedirs(_path, exist_ok=True)

            for idx, media in enumerate(item['attachments'], start=1):
                logging.info(f"[Instagram] Processing media: {media}")
                path = await self.process_media(media, id, idx)
                if path:
                    media_paths.append(path)

            if not media_paths:
                return {"id": id, "status": False}

            await self.queue.update_status(id, 'uploading')
            if len(media_paths) > 1:
                self.client.album_upload(media_paths, self._config['caption'])
            elif media['validate']['type'] == 'PHOTO':
                self.client.photo_upload(media_paths[0], self._config['caption'])
            elif media['validate']['type'] == 'VIDEO':
                self.client.video_upload(media_paths[0], self._config['caption'])

            shutil.rmtree(_path, ignore_errors=True)

            await self.queue.update_status(id, 'uploaded')
            logging.info(f"[Instagram] Media with ID {id} processed successfully.")
            await self.queue.remove_by_id(id)
            return {"id": id, "status": True}
        except Exception as e:
            # Handle the exception
            logging.error(f"[Instagram] Processing media: {e}")
            await self.queue.update_error(id, str(e))
            await self.queue.stop_queue(id)
            return {"id": id, "status": False}