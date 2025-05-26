import logging
import datetime
from pathlib import Path
from pymongo import MongoClient

from .config import Config

# Setup logging
LOG_FILE = f"logs/{datetime.datetime.now().strftime('%Y-%m-%d_T%H-%M-%S')}.log"
logging.basicConfig(filename=LOG_FILE, filemode="w", format="%(asctime)s %(levelname)s %(message)s", level=logging.DEBUG)

class QueueManager:
    def __init__(self):
        logging.info("[QueueManager] Initializing queue")
        logging.info("[QueueManager] Loading MongoDB configuration")
        logging.getLogger("pymongo").setLevel(logging.WARNING)

        cf = Config().load()

        self._config = cf['mongodb']

        self._db_username = self._config['username']
        self._db_password = self._config['password']
        self._db_host = self._config['host']
        self._db_port = self._config['port']
        self._db_name = self._config['database']

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
        
        item = self._data.find_one({'stop': False, 'status': 'pending'}, sort=[('_id', 1)])


        logging.info(f"[QueueManager] Retrieved item{' and pop' if pop else '' }: {item}")
        return item

    def remove_by_id(self, id):
        logging.info("[QueueManager] Removing item by ID")
        self._data.delete_one({"id": id})

    def stop_queue(self, id):
        logging.info(f"[QueueManager] Stopping queue item with ID {id}")
        self._data.update_one({"id": id}, {"$set": {"stop": True}})

    def update_status(self, id, status):
        logging.info(f"[QueueManager] Updating status of item with ID {id} to '{status}'")
        self._data.update_one({"id": id}, {"$set": {"status": status}})

    def length(self):
        return self._data.count_documents({})