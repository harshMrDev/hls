import os
import re
import m3u8
import aiohttp
import aiofiles
import asyncio
import hashlib
import time
import shutil
from urllib.parse import urljoin  # <-- Fix: Import urljoin!
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

class StreamBot:
    def __init__(self):
        self.current_time = "2025-06-14 13:00:50"
        self.current_user = "harshMrDev"
        self.base_dir = "/tmp/stream_downloads"
        self.chunk_size = 1024 * 1024  # 1MB chunks
        
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Origin': 'https://iframe.mediadelivery.net',
            'Referer': 'https://iframe.mediadelivery.net/',
        }
        
        if os.path.exists(self.base_dir):
            shutil.rmtree(self.base_dir)
        os.makedirs(self.base_dir)

    async def start_command(self, update: Update, context):
        await update.message.reply_text(
            f"👋 Welcome to Stream Downloader!\n\n"
            f"Just send me MediaDelivery or M3U8 URL\n\n"
            f"🕒 Time: {self.current_time}\n"
            f"👤 Handler: @{self.current_user}"
        )

    async def handle_url(self, update: Update, context):
        url = update.message.text.strip()
        
        if not self._is_valid_url(url):
            await update.message.reply_text("❌ Send a valid streaming URL")
            return

        msg = await update.message.reply_text("🔄 Processing...")

        try:
            video_id = self._extract_video_id(url)
            work_dir = os.path.join(self.base_dir, video_id)
            if os.path.exists(work_dir):
                shutil.rmtree(work_dir)
            os.makedirs(work_dir)

            if "mediadelivery.net" in url:
                token = await self._get_token(url)
                if token:
                    self.headers['Authorization'] = f'Bearer {token}'

            playlist_info = await self._get_playlist(url)
            if not playlist_info or not playlist_info['segments']:
                raise Exception("No segments found in playlist")

            output_file = os.path.join(work_dir, f"{video_id}.mp4")
            await self._download_and_merge(
                playlist_info['segments'],
                playlist_info['base_url'],
                output_file,
                work_dir,
                msg
            )

            if not os.path.exists(output_file):
                raise Exception("Failed to create output file")

            await msg.edit_text("📤 Uploading...")
            with open(output_file, 'rb') as video:
                await update.message.reply_video(
                    video,
                    caption=f"✅ Download Complete!\n"
                           f"🕒 {self.current_time}\n"
                           f"👤 @{self.current_user}",
                    supports_streaming=True
                )

            shutil.rmtree(work_dir)
            await msg.delete()

        except Exception as e:
            await msg.edit_text(f"❌ Error: {str(e)}")
            if 'work_dir' in locals() and os.path.exists(work_dir):
                shutil.rmtree(work_dir)

    async def _get_token(self, url: str) -> str:
        try:
            video_id = self._extract_video_id(url)
            auth_url = f"https://iframe.mediadelivery.net/auth/{video_id}/token"
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    auth_url,
                    headers=self.headers,
                    json={"url": url},
                    ssl=False
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get('token', '')
            return ''
        except:
            return ''

    async def _get_playlist(self, url: str) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self.headers, ssl=False) as response:
                if response.status != 200:
                    raise Exception(f"Failed to get playlist: {response.status}")
                m3u8_content = await response.text()
                playlist = m3u8.loads(m3u8_content)
                if not playlist.segments:
                    raise Exception("Empty playlist")
                return {
                    "segments": playlist.segments,
                    "base_url": self._get_base_url(url)
                }

    async def _download_and_merge(self, segments, base_url: str, output_file: str, work_dir: str, msg):
        segment_files = []
        total_segments = len(segments)
        last_progress = 0

        try:
            async with aiohttp.ClientSession() as session:
                for i, segment in enumerate(segments, 1):
                    segment_url = urljoin(base_url, segment.uri)
                    segment_path = os.path.join(work_dir, f"segment_{i:05d}.ts")
                    for attempt in range(3):
                        try:
                            await self._download_segment(segment_url, segment_path, session)
                            if os.path.exists(segment_path) and os.path.getsize(segment_path) > 0:
                                segment_files.append(segment_path)
                                break
                        except Exception as e:
                            if attempt == 2:
                                raise Exception(f"Failed to download segment {i}: {e}")
                            await asyncio.sleep(1)
                    progress = int((i / total_segments) * 100)
                    if progress > last_progress or i == total_segments:
                        await msg.edit_text(
                            f"📥 Downloading: {i}/{total_segments} segments\n"
                            f"📊 Progress: {progress}%"
                        )
                        last_progress = progress

            if len(segment_files) != total_segments:
                raise Exception(f"Missing segments: got {len(segment_files)}, expected {total_segments}")

            total_mb = sum(os.path.getsize(f) for f in segment_files) / (1024 * 1024)
            await msg.edit_text(
                f"🔄 Merging video segments...\n"
                f"📦 Total size: {total_mb:.1f}MB\n"
                f"⌛ Please wait..."
            )

            concat_file = os.path.join(work_dir, "all_segments.ts")
            with open(concat_file, 'wb') as outfile:
                for seg in segment_files:
                    with open(seg, 'rb') as infile:
                        shutil.copyfileobj(infile, outfile, length=1024*1024)

            cmd = [
                'ffmpeg',
                '-y',
                '-i', concat_file,
                '-c', 'copy',
                '-bsf:a', 'aac_adtstoasc',
                '-movflags', '+faststart',
                output_file
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise Exception(f"FFmpeg error: {stderr.decode().strip()}")

            if not os.path.exists(output_file):
                raise Exception("Merged file not found after FFmpeg")

            # Cleanup
            if os.path.exists(concat_file):
                os.remove(concat_file)
            for seg in segment_files:
                if os.path.exists(seg):
                    os.remove(seg)

        except Exception as e:
            for file in segment_files:
                if os.path.exists(file):
                    try:
                        os.remove(file)
                    except:
                        pass
            raise e

    async def _download_segment(self, url: str, file_path: str, session: aiohttp.ClientSession):
        async with session.get(url, headers=self.headers, ssl=False) as response:
            if response.status != 200:
                raise Exception(f"Status {response.status}")
            async with aiofiles.open(file_path, 'wb') as f:
                async for chunk in response.content.iter_chunked(self.chunk_size):
                    await f.write(chunk)

    def _is_valid_url(self, url: str) -> bool:
        return ("mediadelivery.net" in url and "/video" in url) or url.endswith(".m3u8")

    def _extract_video_id(self, url: str) -> str:
        if "mediadelivery.net" in url:
            match = re.search(r'/([a-f0-9-]+)/\d+p/', url)
            return match.group(1) if match else f"video_{int(time.time())}"
        return hashlib.md5(url.encode()).hexdigest()[:12]

    def _get_base_url(self, url: str) -> str:
        # Use rsplit to ensure base URL is correct for urljoin
        return url.rsplit('/', 1)[0] + '/'

def main():
    bot = StreamBot()
    app = Application.builder().token(os.getenv('BOT_TOKEN')).build()
    app.add_handler(CommandHandler("start", bot.start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_url))
    print(f"""
    🤖 Starting Stream Downloader Bot
    ⏰ Time: {bot.current_time}
    👤 Handler: @{bot.current_user}
    """)
    app.run_polling()

if __name__ == "__main__":
    main()
