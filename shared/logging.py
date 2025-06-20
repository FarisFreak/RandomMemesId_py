import logging
import os
from pathlib import Path

class Logger:
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.__initialized = False
        return cls._instance
    
    def __init__(self, filename: str = "app"):
        if not self.__initialized:
            self.filename = filename
            self._setup_logging()
            self.__initialized = True
    
    def _setup_logging(self):
        """Setup proper logging configuration"""
        # Create logs directory relative to current file
        current_dir = Path(__file__).parent
        logs_dir = current_dir.parent / "logs"
        logs_dir.mkdir(exist_ok=True)
        
        log_file = logs_dir / f"{self.filename}.log"
        
        # Clear existing handlers if any
        logging.root.handlers.clear()
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Create handlers
        file_handler = logging.FileHandler(
            log_file, 
            encoding='utf-8',
            mode='a'  # Append mode
        )
        file_handler.setFormatter(formatter)
        
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        
        # Configure root logger
        logging.basicConfig(
            level=logging.INFO,
            handlers=[file_handler, console_handler],
            force=True  # Override existing config
        )