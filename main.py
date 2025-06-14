import os
import re
import m3u8
import aiohttp
import aiofiles
import asyncio
import hashlib
import time
import json
import uuid
from pathlib import Path
from urllib.parse import urlparse, urljoin, parse_qs
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters

class StreamBot:
    def __init__(self):
        self.current_time = "2025-06-14 05:40:00"
        self.current_user = "harshMrDev"
        self.temp_dir = "/tmp/stream_downloads"
        self.chunk_size = 1024 * 1024
        self.session_id = str(uuid.uuid4())
        
        # Enhanced headers for MediaDelivery
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Origin': 'https://iframe.mediadelivery.net',
            'Referer': 'https://iframe.mediadelivery.net/',
            'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'Connection': 'keep-alive',
            'Priority': 'u=1, i',
            'X-Session-ID': str(uuid.uuid4()),
            'X-Requested-With': 'XMLHttpRequest'
        }
        
        os.makedirs(self.temp_dir, exist_ok=True)

    async def start_command(self, update: Update, context):
        await update.message.reply_text(
            f"👋 Welcome to Stream Downloader!\n\n"
            f"I can handle:\n"
            f"1️⃣ MediaDelivery URLs:\n"
            f"   https://iframe.mediadelivery.net/ID/quality/video.drm\n\n"
            f"2️⃣ M3U8 URLs:\n"
            f"   Any URL ending with .m3u8\n\n"
            f"Just send me the URL!\n\n"
            f"🕒 Time: {self.current_time}\n"
            f"👤 Handler: @{self.current_user}"
        )

    async def handle_url(self, update: Update, context):
        url = update.message.text.strip()
        
        if not self._is_valid_url(url):
            await update.message.reply_text(
                "❌ Invalid URL format!\n\n"
                "Send MediaDelivery or M3U8 URL"
            )
            return

        msg = await update.message.reply_text("🔄 Processing URL...")

        try:
            # Initialize session
            await self._init_session(url)
            
            # Get playlist data
            playlist_url = await self._get_playlist_url(url)
            if not playlist_url:
                raise Exception("Failed to get playlist URL")

            await msg.edit_text("📥 Initializing download...")
            
            # Process download
            output_file = await self._process_download(playlist_url, msg)

            # Send video
            await msg.edit_text("📤 Uploading to Telegram...")
            
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

    async def _init_session(self, url: str):
        """Initialize session with proper authentication"""
        try:
            video_id = self._extract_video_id(url)
            
            # Step 1: Get initial auth
            init_url = f"https://iframe.mediadelivery.net/embed/{video_id}"
            async with aiohttp.ClientSession() as session:
                async with session.get(init_url, headers=self.headers, ssl=False) as response:
                    if response.status == 200:
                        # Get cookies
                        cookies = response.cookies
                        cookie_string = '; '.join([f"{k}={v.value}" for k, v in cookies.items()])
                        self.headers['Cookie'] = cookie_string
                        
                        # Get CSRF token from response
                        text = await response.text()
                        csrf_match = re.search(r'csrf-token["\'] content=["\'](.*?)["\']', text)
                        if csrf_match:
                            self.headers['X-CSRF-TOKEN'] = csrf_match.group(1)
            
            # Step 2: Get auth token
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
                        if 'token' in data:
                            self.headers['Authorization'] = f"Bearer {data['token']}"
                            
            # Step 3: Validate session
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://iframe.mediadelivery.net/validate/{video_id}",
                    headers=self.headers,
                    ssl=False
                ) as response:
                    if response.status != 200:
                        raise Exception("Session validation failed")
                        
        except Exception as e:
            raise Exception(f"Session initialization failed: {str(e)}")

    async def _get_playlist_url(self, url: str) -> str:
        """Get the actual playlist URL"""
        try:
            video_id = self._extract_video_id(url)
            quality = self._extract_quality(url)
            
            manifest_url = f"https://iframe.mediadelivery.net/manifest/{video_id}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(manifest_url, headers=self.headers, ssl=False) as response:
                    if response.status == 200:
                        data = await response.json()
                        playlists = data.get('playlists', [])
                        for playlist in playlists:
                            if playlist.get('quality') == quality:
                                return playlist.get('url')
            
            return url  # Fallback to original URL
        except:
            return url

    async def _process_download(self, url: str, msg) -> str:
        """Process the download with enhanced segment handling"""
        video_id = self._extract_video_id(url)
        work_dir = Path(self.temp_dir) / video_id
        work_dir.mkdir(parents=True, exist_ok=True)
        
        # Get playlist with retries
        playlist_info = None
        for attempt in range(3):
            try:
                playlist_info = await self._get_m3u8_data(url)
                if playlist_info and playlist_info['segments']:
                    break
            except:
                await asyncio.sleep(2 ** attempt)
                await self._init_session(url)
        
        if not playlist_info or not playlist_info['segments']:
            raise Exception("Could not get valid playlist data")

        # Download segments
        output_file = work_dir / f"{video_id}.mp4"
        segment_files = []
        total_segments = len(playlist_info['segments'])

        async with aiohttp.ClientSession() as session:
            for i, segment in enumerate(playlist_info['segments'], 1):
                segment_url = urljoin(playlist_info['base_url'], segment.uri)
                segment_file = work_dir / f"segment_{i}.ts"

                # Try download with retries
                for attempt in range(5):
                    try:
                        await self._download_segment(segment_url, segment_file, session)
                        segment_files.append(str(segment_file))
                        break
                    except Exception as e:
                        if attempt == 4:
                            raise Exception(f"Failed to download segment {i}")
                        await asyncio.sleep(2 ** attempt)
                        await self._init_session(url)  # Re-init session before retry

                if i % max(1, total_segments // 20) == 0:
                    await msg.edit_text(
                        f"📥 Progress: {i}/{total_segments} segments\n"
                        f"📊 {i/total_segments*100:.1f}%"
                    )

        # Merge segments
        await msg.edit_text("🔄 Merging video...")
        await self._merge_segments(segment_files, str(output_file))

        # Cleanup segments
        for file in segment_files:
            if os.path.exists(file):
                os.remove(file)

        return str(output_file)

    async def _get_m3u8_data(self, url: str) -> dict:
        """Get M3U8 playlist data"""
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self.headers, ssl=False) as response:
                if response.status != 200:
                    raise Exception(f"Failed to fetch playlist: {response.status}")
                
                m3u8_content = await response.text()
                playlist = m3u8.loads(m3u8_content)
                
                return {
                    "segments": playlist.segments,
                    "base_url": self._get_base_url(url)
                }

    async def _download_segment(self, url: str, file_path: Path, session: aiohttp.ClientSession):
        """Download segment with validation"""
        async with session.get(url, headers=self.headers, ssl=False) as response:
            if response.status != 200:
                raise Exception(f"Status {response.status}")
            
            content_length = int(response.headers.get('Content-Length', 0))
            if content_length < 100:
                raise Exception("Invalid segment size")
            
            async with aiofiles.open(file_path, 'wb') as f:
                async for chunk in response.content.iter_chunked(self.chunk_size):
                    await f.write(chunk)

    async def _merge_segments(self, segment_files: list, output_file: str):
        """Merge TS segments into MP4"""
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
        return ("mediadelivery.net" in url and "video.drm" in url) or url.endswith(".m3u8")

    def _extract_video_id(self, url: str) -> str:
        if "mediadelivery.net" in url:
            match = re.search(r'/([a-f0-9-]+)/\d+p/', url)
            return match.group(1) if match else f"video_{int(time.time())}"
        return hashlib.md5(url.encode()).hexdigest()[:12]

    def _extract_quality(self, url: str) -> str:
        """Extract quality from URL"""
        match = re.search(r'/(\d+p)/', url)
        return match.group(1) if match else "480p"

    def _get_base_url(self, url: str) -> str:
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
