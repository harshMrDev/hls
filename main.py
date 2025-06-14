import os
import re
import m3u8
import aiohttp
import aiofiles
import asyncio
import hashlib
import time
from pathlib import Path
from urllib.parse import urlparse, urljoin, parse_qs
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters

class StreamBot:
    def __init__(self):
        self.current_time = "2025-06-14 05:29:05"
        self.current_user = "harshMrDev"
        self.temp_dir = "/tmp/stream_downloads"
        self.chunk_size = 1024 * 1024  # 1MB chunks
        
        # Enhanced headers for MediaDelivery
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Origin': 'https://iframe.mediadelivery.net',
            'Referer': 'https://iframe.mediadelivery.net/',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'Connection': 'keep-alive',
            'Pragma': 'no-cache',
            'Cache-Control': 'no-cache'
        }
        
        os.makedirs(self.temp_dir, exist_ok=True)

    async def start_command(self, update: Update, context):
        """Handle /start command"""
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
        """Handle incoming URLs"""
        url = update.message.text.strip()
        
        if not self._is_valid_url(url):
            await update.message.reply_text(
                "❌ Please send a valid streaming URL:\n"
                "1. MediaDelivery URL or\n"
                "2. M3U8 URL"
            )
            return

        msg = await update.message.reply_text(
            "🔄 Processing your URL...\n"
            "⏳ Please wait..."
        )

        try:
            # Parse URL components
            parsed_url = urlparse(url)
            query_params = parse_qs(parsed_url.query)
            
            # Get required auth parameters
            context_id = query_params.get('contextId', [''])[0]
            token = query_params.get('token', [''])[0]
            
            # Update headers with auth
            if context_id:
                self.headers['X-Context-ID'] = context_id
            if token:
                self.headers['Authorization'] = f'Bearer {token}'

            # Get playlist
            playlist_info = await self._get_playlist(url)
            
            # Create work directory
            video_id = self._extract_video_id(url)
            work_dir = Path(self.temp_dir) / video_id
            work_dir.mkdir(parents=True, exist_ok=True)

            # Download segments
            output_file = await self._download_segments(
                playlist_info['segments'],
                playlist_info['base_url'],
                work_dir / f"{video_id}.mp4",
                msg,
                query_params
            )

            # Send video
            await msg.edit_text("📤 Uploading to Telegram...")
            
            with open(output_file, 'rb') as video:
                await update.message.reply_video(
                    video,
                    caption=(
                        f"✅ Download Complete!\n\n"
                        f"🎥 Type: {'MediaDelivery' if 'mediadelivery.net' in url else 'M3U8'}\n"
                        f"📊 Size: {os.path.getsize(output_file)/1024/1024:.1f}MB\n"
                        f"🎯 Segments: {len(playlist_info['segments'])}\n"
                        f"🕒 Time: {self.current_time}\n"
                        f"👤 Handler: @{self.current_user}"
                    ),
                    supports_streaming=True
                )

            # Cleanup
            if os.path.exists(output_file):
                os.remove(output_file)
            await msg.delete()

        except Exception as e:
            await msg.edit_text(f"❌ Error: {str(e)}")

    async def _get_playlist(self, url: str) -> dict:
        """Get M3U8 playlist"""
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self.headers, ssl=False) as response:
                if response.status == 403:
                    await self._refresh_auth(url)
                    async with session.get(url, headers=self.headers, ssl=False) as retry_response:
                        if retry_response.status != 200:
                            raise Exception(f"Failed to fetch playlist: {retry_response.status}")
                        m3u8_content = await retry_response.text()
                elif response.status != 200:
                    raise Exception(f"Failed to fetch playlist: {response.status}")
                else:
                    m3u8_content = await response.text()

                playlist = m3u8.loads(m3u8_content)
                if not playlist.segments:
                    raise Exception("No segments found in playlist")
                    
                return {
                    "segments": playlist.segments,
                    "base_url": self._get_base_url(url)
                }

    async def _download_segments(self, segments, base_url: str, output_file: Path, msg, query_params: dict):
        """Download and merge segments"""
        segment_files = []
        total_segments = len(segments)
        retry_count = 5
        retry_delays = [1, 2, 4, 8, 16]

        async with aiohttp.ClientSession() as session:
            for i, segment in enumerate(segments, 1):
                segment_url = urljoin(base_url, segment.uri)
                segment_file = output_file.parent / f"segment_{i}.ts"

                # Add auth to segment URL
                segment_url = self._add_auth_params(segment_url, query_params)

                # Try download with retries
                for attempt in range(retry_count):
                    try:
                        await self._download_segment(segment_url, segment_file, session)
                        segment_files.append(str(segment_file))
                        break
                    except Exception as e:
                        if attempt == retry_count - 1:
                            raise Exception(f"Failed to download segment {i}: {str(e)}")
                        await asyncio.sleep(retry_delays[attempt])
                        await self._refresh_auth(segment_url)

                # Update progress
                if i % max(1, total_segments // 20) == 0:
                    await msg.edit_text(
                        f"📥 Downloading: {i}/{total_segments} segments\n"
                        f"📊 Progress: {i/total_segments*100:.1f}%"
                    )

        # Merge segments
        await msg.edit_text("🔄 Merging segments...")
        await self._merge_segments(segment_files, str(output_file))

        # Cleanup segments
        for file in segment_files:
            if os.path.exists(file):
                os.remove(file)

        return str(output_file)

    async def _download_segment(self, url: str, file_path: Path, session: aiohttp.ClientSession):
        """Download individual segment"""
        async with session.get(url, headers=self.headers, ssl=False) as response:
            if response.status != 200:
                raise Exception(f"Status {response.status}")
            
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

    async def _refresh_auth(self, url: str):
        """Refresh auth token"""
        try:
            video_id = self._extract_video_id(url)
            refresh_url = f"https://iframe.mediadelivery.net/auth/refresh/{video_id}"
            
            async with aiohttp.ClientSession() as session:
                async with session.post(refresh_url, headers=self.headers, ssl=False) as response:
                    if response.status == 200:
                        data = await response.json()
                        if 'token' in data:
                            self.headers['Authorization'] = f"Bearer {data['token']}"
        except:
            pass

    def _add_auth_params(self, url: str, params: dict) -> str:
        """Add auth params to URL"""
        parsed = urlparse(url)
        query_dict = parse_qs(parsed.query)
        
        for key, value in params.items():
            if isinstance(value, list):
                query_dict[key] = value[0]
            else:
                query_dict[key] = value

        query_string = '&'.join(f"{k}={v}" for k, v in query_dict.items())
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}{'?' + query_string if query_string else ''}"

    def _is_valid_url(self, url: str) -> bool:
        """Validate URL format"""
        return ("mediadelivery.net" in url and "video.drm" in url) or url.endswith(".m3u8")

    def _extract_video_id(self, url: str) -> str:
        """Extract video ID from URL"""
        if "mediadelivery.net" in url:
            match = re.search(r'/([a-f0-9-]+)/\d+p/', url)
            return match.group(1) if match else f"video_{int(time.time())}"
        return hashlib.md5(url.encode()).hexdigest()[:12]

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
