MediaConfig = {
    "image/jpeg": "PHOTO",
    "image/png": "PHOTO",
    "video/mp4": "VIDEO",
    "video/mpeg": "VIDEO",
    "video/x-matroska": "VIDEO",
    "video/quicktime": "VIDEO"
}

class Media : 
    def validate(attachment) :
        return {
            "status" : attachment.content_type in MediaConfig,
            "type" : MediaConfig.get(attachment.content_type)
        }