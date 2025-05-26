import asyncio

async def convert(path, temp_path) :
    process = await asyncio.create_subprocess_exec(
        'ffmpeg', '-i', path, temp_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        print("FFmpeg failed:")
        print(stderr.decode())  # Tampilkan error FFmpeg
    else:
        print("FFmpeg finished successfully.")

async def main() :
    path = 'vid.mov'
    temp_path = 'temp.mp4'
    await convert(path, temp_path)
    print('Conversion complete')

if __name__ == '__main__':
    asyncio.run(main())