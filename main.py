import os
import re
import m3u8
import aiohttp
import aiofiles
import asyncio
import hashlib
import time
from pathlib import Path
from urllib.parse import urlparse, urljoin
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

class StreamBot:
    def __init__(self):
        self.current_time = "2025-06-14 05:43:31"
        self.current_user = "harshMrDev"
        self.temp_dir = "/tmp/stream_downloads"
        self.chunk_size = 1024 * 1024  # 1MB chunks
        
        # Essential headers for MediaDelivery
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Origin': 'https://iframe.mediadelivery.net',
            'Referer': 'https://iframe.mediadelivery.net/',
        }
        
        os.makedirs(self.temp_dir, exist_ok=True)

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
            # Get auth token first
            if "mediadelivery.net" in url:
                token = await self._get_token(url)
                if token:
                    self.headers['Authorization'] = f'Bearer {token}'

            # Get master playlist
            playlist_url = await self._get_master_playlist(url)
            if not playlist_url:
                playlist_url = url

            # Get segments
            segments_info = await self._get_segments(playlist_url)
            if not segments_info or not segments_info['segments']:
                raise Exception("No segments found")

            # Create work directory
            video_id = self._extract_video_id(url)
            work_dir = Path(self.temp_dir) / video_id
            work_dir.mkdir(parents=True, exist_ok=True)

            # Download segments
            output_file = await self._download_segments(
                segments_info['segments'],
                segments_info['base_url'],
                work_dir / f"{video_id}.mp4",
                msg
            )

            # Send video
            await msg.edit_text("📤 Uploading...")
            
            with open(output_file, 'rb') as video:
                await update.message.reply_video(
                    video,
                    caption=f"✅ Download Complete!\n"
                           f"🕒 {self.current_time}\n"
                           f"👤 @{self.current_user}",
                    supports_streaming=True
                )

            # Cleanup
            os.remove(output_file)
            await msg.delete()

        except Exception as e:
            await msg.edit_text(f"❌ Error: {str(e)}")

    async def _get_token(self, url: str) -> str:
        """Get auth token for MediaDelivery"""
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

    async def _get_master_playlist(self, url: str) -> str:
        """Get master playlist URL"""
        if not "mediadelivery.net" in url:
            return url
            
        try:
            video_id = self._extract_video_id(url)
            quality = self._extract_quality(url)
            playlist_url = f"https://iframe.mediadelivery.net/m/{video_id}/{quality}/playlist.m3u8"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(playlist_url, headers=self.headers, ssl=False) as response:
                    if response.status == 200:
                        return playlist_url
            return url
        except:
            return url

    async def _get_segments(self, url: str) -> dict:
        """Get M3U8 segments"""
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self.headers, ssl=False) as response:
                if response.status != 200:
                    raise Exception(f"Failed to get playlist: {response.status}")
                
                m3u8_content = await response.text()
                playlist = m3u8.loads(m3u8_content)
                
                return {
                    "segments": playlist.segments,
                    "base_url": self._get_base_url(url)
                }

    async def _download_segments(self, segments, base_url: str, output_file: Path, msg):
        """Download and merge segments"""
        segment_files = []
        total_segments = len(segments)

        async with aiohttp.ClientSession() as session:
            for i, segment in enumerate(segments, 1):
                segment_url = urljoin(base_url, segment.uri)
                segment_file = output_file.parent / f"segment_{i}.ts"
                
                # Try download with retries
                for attempt in range(3):
                    try:
                        await self._download_segment(segment_url, segment_file, session)
                        segment_files.append(str(segment_file))
                        break
                    except Exception as e:
                        if attempt == 2:  # Last attempt
                            raise Exception(f"Failed to download segment {i}")
                        await asyncio.sleep(1)

                # Update progress every 5%
                if i % max(1, total_segments // 20) == 0:
                    await msg.edit_text(
                        f"📥 Progress: {i}/{total_segments}\n"
                        f"📊 {i/total_segments*100:.1f}%"
                    )

        # Merge segments
        await msg.edit_text("🔄 Merging...")
        await self._merge_segments(segment_files, str(output_file))

        # Cleanup segments
        for file in segment_files:
            if os.path.exists(file):
                os.remove(file)

        return str(output_file)

    async def _download_segment(self, url: str, file_path: Path, session: aiohttp.ClientSession):
        """Download single segment"""
        async with session.get(url, headers=self.headers, ssl=False) as response:
            if response.status != 200:
                raise Exception(f"Status {response.status}")
            
            async with aiofiles.open(file_path, 'wb') as f:
                async for chunk in response.content.iter_chunked(self.chunk_size):
                    await f.write(chunk)

    async def _merge_segments(self, segment_files: list, output_file: str):
        """Merge segments into final video"""
        list_file = f"{output_file}.txt"
        with open(list_file, 'w') as f:
            for file in segment_files:
                f.write(f"file '{file}'\n")

        process = await asyncio.create_subprocess_exec(
            'ffmpeg',
            '-f', 'concat',
            '-safe', '0',
            '-i', list_file,
            '-c', 'copy',
            output_file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        await process.communicate()
        
        if os.path.exists(list_file):
            os.remove(list_file)

    def _is_valid_url(self, url: str) -> bool:
        """Validate URL format"""
        return ("mediadelivery.net" in url and "/video" in url) or url.endswith(".m3u8")

    def _extract_video_id(self, url: str) -> str:
        """Extract video ID from URL"""
        if "mediadelivery.net" in url:
            match = re.search(r'/([a-f0-9-]+)/\d+p/', url)
            return match.group(1) if match else f"video_{int(time.time())}"
        return hashlib.md5(url.encode()).hexdigest()[:12]

    def _extract_quality(self, url: str) -> str:
        """Extract quality from URL"""
        match = re.search(r'/(\d+p)/', url)
        return match.group(1) if match else "480p"

    def _get_base_url(self, url: str) -> str:
        """Get base URL for segments"""
        return '/'.join(url.split('/')[:-1])

def main():
    """Start the bot"""
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
