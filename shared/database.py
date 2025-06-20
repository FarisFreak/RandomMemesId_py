import logging
import datetime
from typing import Optional, Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from .config import config
from .logging import Logger

class DatabaseManager:
    _instance = None
    _client = None
    _db = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(DatabaseManager, cls).__new__(cls)
            module_name = kwargs.get('module_name', 'database')
            Logger(filename=module_name or 'database')
        return cls._instance
    
    def __init__(self, module_name: str = None):
        if self._client is None:
            self._connect()

    def _connect(self):
        try:
            self._client = AsyncIOMotorClient(
                config.mongodb['uri'],
                serverSelectionTimeoutMS=5000  # Timeout after 5 seconds
            )
            self._db = self._client[config.mongodb['db_name']]
            logging.info("Connected to MongoDB successfully.")
        except Exception as e:
            logging.error(f"Failed to connect to MongoDB: {e}")
            raise

    @property
    def queue_collection(self) -> AsyncIOMotorCollection:
        return self._db['queue']
    
    # Queue Operations
    async def insert_queue_item(self, item: Dict[str, Any]) -> bool:
        try:
            await self.queue_collection.insert_one(item)
            return True
        except Exception as e:
            logging.error(f"Failed to insert queue item: {e}")
            return False
        
    async def get_next_pending_item(self) -> Optional[Dict[str, Any]]:
        return await self.queue_collection.find_one(
            {"stop": False, "status": "pending"}, 
            sort=[("priority", 1), ("_id", 1)]
        )
    
    async def update_queue_status(self, item_id: int, status: str, **kwargs) -> bool:
        try:
            update_data = {
                "status": status, 
                "reacted": False,
                "updated_at": datetime.datetime.now()
            }
            update_data.update(kwargs)
            
            result = await self.queue_collection.update_one(
                {"id": item_id}, 
                {"$set": update_data}
            )
            return result.modified_count > 0
        except Exception as e:
            logging.error(f"Failed to update queue status: {e}")
            return False
    
    async def get_queue_stats(self) -> Dict[str, int]:
        try:
            pipeline = [
                {"$group": {
                    "_id": "$status",
                    "count": {"$sum": 1}
                }}
            ]
            stats = {}
            async for item in self.queue_collection.aggregate(pipeline):
                stats[item['_id']] = item['count']
            return stats
        except Exception as e:
            logging.error(f"Failed to get queue stats: {e}")
            return {}
    
    async def get_queue_items(self, status: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            query = {} if status is None else {"status": status}
            items = await self.queue_collection.find(query).limit(limit).sort("_id", -1).to_list(length=None)
            return items
        except Exception as e:
            logging.error(f"Failed to get queue items: {e}")
            return []
    
    async def delete_queue_item(self, item_id: int) -> bool:
        try:
            result = await self.queue_collection.delete_one({"id": item_id})
            return result.deleted_count > 0
        except Exception as e:
            logging.error(f"Failed to delete queue item: {e}")
            return False
    
    async def find_unreacted_status_changes(self) -> List[Dict[str, Any]]:
        try:
            query = {
                "status": {"$in": ['success', 'failed', 'uploading']},
                "reacted": False
            }
            return await self.queue_collection.find(query).to_list(length=None)
        except Exception as e:
            logging.error(f"Failed to find unreacted status changes: {e}")
            return []
        
# db = DatabaseManager()