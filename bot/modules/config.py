import json

class Config :
    def __init__(self):
        self.path = 'config.json'

    def load(self) :
        with open(self.path) as f :
            return json.load(f)
