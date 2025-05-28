import logging
import datetime
from pathlib import Path
from motor.motor_asyncio import AsyncIOMotorClient

from .config import Config

# Setup logging
LOG_FILE = f"logs/{datetime.datetime.now().strftime('%Y-%m-%d_T%H-%M-%S')}.log"
logging.basicConfig(filename=LOG_FILE, filemode="w", format="%(asctime)s %(levelname)s %(message)s", level=logging.DEBUG)

class QueueManager:
    def __init__(self):
        logging.info("[QueueManager] Initializing async queue manager")
        logging.getLogger("pymongo").setLevel(logging.WARNING)

        cf = Config().load()
        self._config = cf["mongodb"]

        self._db_username = self._config["username"]
        self._db_password = self._config["password"]
        self._db_host = self._config["host"]
        self._db_port = self._config["port"]
        self._db_name = self._config["database"]

        self._mongo_uri = (
            f"mongodb://{self._db_username}:{self._db_password}@{self._db_host}:"
            f"{self._db_port}/{self._db_name}?authSource={self._db_name}"
        )

        self._client = AsyncIOMotorClient(self._mongo_uri)
        self._db = self._client[self._db_name]
        self._collection = self._db["queue"]

        self.queue_dir = Path("_queue")
        self.media_dir = self.queue_dir / "media"
        self.queue_dir.mkdir(exist_ok=True)
        self.media_dir.mkdir(exist_ok=True)

    async def add(self, data: dict):
        logging.info("[QueueManager] Adding item to queue")
        await self._collection.insert_one(data)

    async def get_all(self):
        logging.info("[QueueManager] Retrieving all items")
        cursor = self._collection.find()
        result = []
        async for doc in cursor:
            result.append(self._clean_document(doc))
        return result

    async def get_priority_all(self):
        logging.info("[QueueManager] Retrieving prioritized items")
        cursor = self._collection.find({"stop": False, "status": "pending"}).sort([("priority", 1), ("_id", 1)])
        result = []
        async for doc in cursor:
            result.append(self._clean_document(doc))
        return result

    async def get_failed_all(self):
        logging.info("[QueueManager] Retrieving failed items")
        cursor = self._collection.find({"stop": True, "status": "failed"}).sort([("priority", 1), ("_id", 1)])
        result = []
        async for doc in cursor:
            result.append(self._clean_document(doc))
        return result

    async def get_first(self):
        logging.info("[QueueManager] Retrieving first item in queue")
        return await self._collection.find_one({"stop": False, "status": "pending"}, sort=[("priority", 1), ("_id", 1)])

    async def remove_by_id(self, id_):
        logging.info(f"[QueueManager] Removing item with ID {id_}")
        await self._collection.delete_one({"id": id_})

    async def stop_queue(self, id_):
        logging.info(f"[QueueManager] Stopping item with ID {id_}")
        await self._collection.update_one({"id": id_}, {"$set": {"stop": True}})

    async def update_status(self, id_, status: str):
        logging.info(f"[QueueManager] Updating status of ID {id_} to '{status}'")
        await self._collection.update_one({"id": id_}, {"$set": {"status": status}})

    async def update_error(self, id_, message: str):
        logging.info(f"[QueueManager] Updating status of ID {id_} error: '{message}'")
        await self._collection.update_one({"id": id_}, {"$set": {"status": 'error', "error": message}})

    async def set_priority(self, ids: list[int]):
        logging.info(f"[QueueManager] Setting priority for {ids}")
        total = len(ids)

        # Update IDs in the list with new priority
        for idx, item_id in enumerate(ids):
            priority = idx - total
            await self._collection.update_one({"id": item_id}, {"$set": {"priority": priority}})

        # Clear priority for others
        await self._collection.update_many(
            {"id": {"$nin": ids}},
            {"$set": {"priority": 0}}
        )

    async def length(self):
        return await self._collection.count_documents({})
    
    def _clean_document(self, doc):
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return doc

