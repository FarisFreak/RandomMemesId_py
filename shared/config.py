import json
import logging
from pathlib import Path

class Config:
    _instance = None
    _config = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._config is None:
            self.load_config()
    
    def load_config(self):
        try:
            config_path = Path(__file__).parent.parent / 'config' / 'config.json'
            with open(config_path, 'r', encoding='utf-8') as file:
                self._config = json.load(file)
            logging.info("Configuration loaded successfully.")
        except Exception as e:
            logging.error(f"Failed to load configuration: {e}")
            raise
    
    @property
    def bot(self):
        return self._config.get('bot', {})
    
    @property
    def worker(self):
        return self._config.get('worker', {})
    
    @property
    def mongodb(self):
        return self._config.get('mongodb', {})
    
config = Config()