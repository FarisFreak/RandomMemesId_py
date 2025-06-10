import discord

MediaConfig = {
    "image/jpeg": "PHOTO",
    "image/png": "PHOTO",
    "video/mp4": "VIDEO",
    "video/mpeg": "VIDEO",
    "video/x-matroska": "VIDEO",
    "video/quicktime": "VIDEO"
}

class Media:
    @staticmethod
    def validate(attachment: discord.Attachment) -> dict:
        media_type = MediaConfig.get(attachment.content_type)
        return {
            "status": media_type is not None,
            "type": media_type
        }