import json
import os

class Config:
    def __init__(self, path: str = 'config.json'):
        self.path = path

    def load(self) -> dict:
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"Config file not found at {self.path}")
        
        with open(self.path, encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON format in {self.path}") from e
