import datetime
import os.path
import logging
import json
import requests
import discord
import ffmpeg
import shutil
from discord.ext import tasks
from instagrapi import Client as IGClient
from modules import Config, Media
from PIL import Image
from io import BytesIO

import asyncio

cf = Config().load()
cfg_discord = cf['discord']
cfg_instagram = cf['instagram']

logging.basicConfig(filename=f"logs/{datetime.datetime.now().strftime("%Y-%m-%d_T%H-%M-%S")}.log", filemode="w", format="%(asctime)s %(levelname)s %(message)s", level=logging.DEBUG)
# logging.getLogger().addHandler(logging.StreamHandler())

class QueueManager : 
    def __init__(self) :
        logging.info("[queuemanager] init")
        self.queue = []
        if os.path.isdir('_queue') == False:
            logging.info("[queuemanager] '_queue' folder does not exist. create new")
            os.makedirs('_queue')

        if os.path.isdir('_queue/media') == False :
            logging.info("[queuemanager] '_queue/media' folder does not exist. create new")
            os.makedirs('_queue/media')
        
        try :
            with open('_queue/list.json') as f :
                logging.info("[queuemanager] load file")
                self.queue = json.load(f)
        except :
            logging.error(f"[queuemanager] invalid list.json. set queue as empty")
        

    def load_information(self) -> None :
        return {
            "queue" : len(self.queue)
        }
    
    def length(self) :
        return len(self.queue)
        
    def add(self, data) :
        logging.info("[queuemanager] add queue")
        self.queue.append(data)
        self.save()

    def get_first(self, pop = False) :
        output = []
        if pop :
            output = self.queue.pop(0)
            logging.info(f"[queuemanager] get first with pop. data = {output}")
            self.save()
        else :
            output = self.queue[0]
            logging.info(f"[queuemanager] get first without pop. data = {output}")

        return output

    def raw(self) : 
        logging.info(f"[queuemanager] output raw. data = {self.queue}")
        return self.queue
    
    def remove_by_id(self, message) :
        logging.info("[queuemanager] remove queue by id")
        self.queue = [d for d in self.queue if d["id"] != message.message_id]
        self.save()
        
    def save(self):
        with open('_queue/list.json', 'w') as f :
            logging.info("[queuemanager] save queue")
            json.dump(self.queue, f)

class InstagramClient(IGClient) :
    def __init__(self) -> None:
        self.client = IGClient()
        if os.path.isfile('session.json'):
            self.client.load_settings('session.json')
        else :
            self.client.login(cfg_instagram['username'], cfg_instagram['password'])
            self.client.dump_settings('session.json')
    pass

    def upload_queue(self) :
        queue = QueueManager()

        proceed = False

        paths = []
        type = ''
        id = ''

        if (queue.length() > 0) :
            q = queue.get_first(True)
            id = q['id']
            medias = q['attachments']
            idx = 0
            os.makedirs(f'_queue/media/{id}')
            for media in medias :
                type = media['validate']['type']
                if media['validate']['type'] == 'PHOTO':
                    idx += 1
                    filename = f"_queue/media/{id}/{idx}_{q['id']}.jpg"
                    media_response = requests.get(media['url'])
                    Image.open(BytesIO(media_response.content)).convert('RGB').save(filename)
                    paths.append(filename)
                elif media['validate']['type'] == 'VIDEO' :
                    idx += 1
                    filename = f"_queue/media/{id}/{idx}_{q['id']}.mp4"
                    filename_temp = f"_queue/media/{id}/_temp_{idx}_{q['id']}_{media['filename']}"
                    media_response = requests.get(media['url'])
                    with open(filename_temp, "wb") as f :
                        f.write(media_response.content)

                    ffmpeg.input(filename_temp).output(filename, loglevel="quiet").run(overwrite_output=True)

                    paths.append(filename)
                    
            proceed = True

            
        if proceed :
            print('[instagram] process queue')
            if (len(paths) > 1) : 
                response = self.client.album_upload(paths, cfg_instagram['caption'])
            elif (len(paths) == 1) :
                if type == 'PHOTO' :
                    response = self.client.photo_upload(paths[0], cfg_instagram['caption'])
                elif type == 'VIDEO' :
                    response = self.client.video_upload(paths[0], cfg_instagram['caption'])
            print('[instagram] process queue finished')

            shutil.rmtree(f'_queue/media/{id}', ignore_errors=True)

        return {
            "id" : id,
            "status" : proceed
        }


# @tasks.loop(seconds=10)
# async def demo(self) :
#     queue_channel = self.get_channel(cfg_discord['queue_channel_id'])

#     await queue_channel.edit(name="Hello World")
#     print ("Hello World")
#     logging.info("Hello world")

class DiscordClient(discord.Client):
    # @tasks.loop(seconds=10)
    async def update_queue(self) :
        queue_channel = self.get_channel(cfg_discord['queue_channel_id'])
        await queue_channel.edit(name=f"Queue : {QueueManager().length()}")

    @tasks.loop(seconds=5)
    async def upload_meme(self) :
        ig = InstagramClient()
        result = ig.upload_queue()

        if result['status'] :
            submit_channel = self.get_channel(cfg_discord['submit_channel_id'])
            message = await submit_channel.fetch_message(result['id'])
            await message.clear_reactions()
            await message.add_reaction('✅')

            # await self.update_queue()

    async def on_connect(self):
        logging.info("[discord] bot connected")
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="any meme submission 👀"))

    async def on_ready(self):
        logging.info("[discord] bot ready")
        print(f'Logged on as {self.user}!')
        self.upload_meme.start()
        # self.update_queue.start()
        
    async def on_message(self, message):
        if message.author.bot :
            return
        
        if message.author.id == self.user.id :
            return
        
        if message.guild.id != cfg_discord['guild_id']:
            return
        
        if message.channel.id != cfg_discord['submit_channel_id']:
            return
        
        log_channel = self.get_channel(cfg_discord['log_channel_id'])
        
        logging.info("[discord] retrieve new submission")

        # await log_channel.send(content="Hello")
        
        # print (message.author.bot)
        # print(f'Message from {message.author}: {message.content}')

        if len(message.attachments) > 0 :
            isvalid = True
            attachments = []
            for attachment in message.attachments:
                valid = Media.validate(attachment)
                if valid['status'] == False :
                    isvalid = valid['status']
                
                attachments.append({
                    "filename" : attachment.filename,
                    "url" : attachment.url,
                    "validate" : valid
                })

            if isvalid : 
                media = {
                    "id" : message.id,
                    "author" : {
                        "id" : message.author.id,
                        "name" : message.author.global_name
                    },
                    "attachments" : attachments,
                    "date" : datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }

                QueueManager().add(media)

            await message.add_reaction('🕒' if isvalid else '❌')
        else :
            await message.delete()

        #proceed
       
    
    async def on_raw_message_delete(self, message):
        QueueManager().remove_by_id(message)

def main() :
    intents = discord.Intents.default()
    intents.message_content = True

    client = DiscordClient(intents=intents)
    client.run(cfg_discord['token'])

    # ig = InstagramClient()
    # ig.upload_queue()

if __name__ == "__main__":
    main()
        

'''
{
    "message.id" : {
        "files" : [
            "attachment.id" : "file.ext"
        ],
        "author" : "Mantap#123"
    }
}
'''