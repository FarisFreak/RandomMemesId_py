import logging
import datetime
from pathlib import Path
from pymongo import MongoClient
from pymongo.collection import Collection

from .config import Config

# Setup logging
LOG_FILE = f"logs/{datetime.datetime.now().strftime('%Y-%m-%d_T%H-%M-%S')}.log"
logging.basicConfig(
    filename=LOG_FILE,
    filemode="w",
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.DEBUG
)

class QueueManager:
    def __init__(self):
        logging.info("[QueueManager] Initializing...")

        config = Config().load()['mongodb']

        uri = f"mongodb://{config['username']}:{config['password']}@{config['host']}:{config['port']}/{config['database']}?authSource={config['database']}"
        self._client = MongoClient(uri)
        self._db = self._client[config['database']]
        self._collection: Collection = self._db['queue']

        # Setup local queue/media directory
        self.queue_dir = Path('_queue')
        self.media_dir = self.queue_dir / 'media'
        self.queue_dir.mkdir(exist_ok=True)
        self.media_dir.mkdir(exist_ok=True)

        logging.getLogger("pymongo").setLevel(logging.WARNING)
        logging.info("[QueueManager] Ready.")

    def add(self, data: dict):
        logging.info(f"[QueueManager] Adding item to queue: {data.get('id')}")
        self._collection.insert_one(data)

    def get_all(self) -> list:
        logging.info("[QueueManager] Fetching all pending queue items...")
        return self._query_items({'stop': False, 'status': 'pending'}, [('position', 1), ('_id', 1)])

    def get_priority_all(self) -> list:
        logging.info("[QueueManager] Fetching all priority-based pending items...")
        return self._query_items({'stop': False, 'status': 'pending'}, [('priority', 1), ('_id', 1)])

    def get_failed_all(self) -> list:
        logging.info("[QueueManager] Fetching all failed items...")
        return self._query_items({'stop': False, 'status': 'failed'}, [('priority', 1), ('_id', 1)])

    def get_first(self) -> dict | None:
        logging.info("[QueueManager] Fetching first pending item...")
        item = self._collection.find_one({'stop': False, 'status': 'pending'}, sort=[('priority', 1), ('_id', 1)])
        logging.info(f"[QueueManager] First item: {item}")
        return item

    def remove_by_id(self, id: int):
        logging.info(f"[QueueManager] Removing item with ID: {id}")
        self._collection.delete_one({"id": id})

    def stop_by_id(self, id: int):
        logging.info(f"[QueueManager] Stopping item with ID: {id}")
        self._collection.update_one({"id": id}, {"$set": {"stop": True}})

    def update_status(self, id: int, status: str):
        logging.info(f"[QueueManager] Updating status of ID {id} to '{status}'")
        self._collection.update_one({"id": id}, {"$set": {"status": status}})

    def update_error(self, id: int, error: str):
        logging.info(f"[QueueManager] Updating error for ID {id}: {error}")
        self._collection.update_one({"id": id}, {"$set": {"error": error, "status": "failed"}})

    def set_priority(self, ids: list[int]):
        """Set priority in ascending order. First = highest priority (lowest number)."""
        logging.info(f"[QueueManager] Setting priority order for: {ids}")
        total = len(ids)
        for idx, item_id in enumerate(ids):
            priority = idx - total
            self._collection.update_one({"id": item_id}, {"$set": {"priority": priority}})

    def length(self) -> int:
        count = self._collection.count_documents({})
        logging.info(f"[QueueManager] Total items in queue: {count}")
        return count

    def _query_items(self, filter: dict, sort: list[tuple]) -> list:
        items = list(self._collection.find(filter).sort(sort))
        logging.info(f"[QueueManager] Retrieved {len(items)} item(s)")
        return items
