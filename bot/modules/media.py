MediaConfig = {
    "image/jpeg": "PHOTO",
    "image/png": "PHOTO",
    "video/mp4": "VIDEO",
    "video/mpeg": "VIDEO",
    "video/x-matroska": "VIDEO",
    "video/quicktime": "VIDEO"
}

Caption = "Check link in bio for submit your meme!\r\n#kamevid #memeindonesia #asupanmeme #memelucu #dagelanmeme #lawakan #videolucu #memeindo #freshmeme #awshitpostid #memeberkualitas #memevideoindonesia #memengakak #memeerpan1140 #memelawak #memekocak #wkwkwk #asupanmemeuseless #recehbanget #asupanmemebergizi #ngakak #ngakaksehat #erpan1140 #dagelanvideo #memelucubanget #shitpostindonesia #shitpostingindonesia #darkjokesindonesia #awreceh #hitzeedsamlekom"

class Media : 
    def validate(attachment) :
        return {
            "status" : attachment.content_type in MediaConfig,
            "type" : MediaConfig.get(attachment.content_type)
        }