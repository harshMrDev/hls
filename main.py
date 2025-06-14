import os
import re
import m3u8
import aiohttp
import aiofiles
import asyncio
import hashlib
import time
import json
from pathlib import Path
from urllib.parse import urlparse, urljoin, parse_qs
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters

class StreamBot:
    def __init__(self):
        self.current_time = "2025-06-14 05:35:55"
        self.current_user = "harshMrDev"
        self.temp_dir = "/tmp/stream_downloads"
        self.chunk_size = 1024 * 1024
        
        # Updated headers with all possible auth headers
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
        url = update.message.text.strip()
        
        if not self._is_valid_url(url):
            await update.message.reply_text(
                "❌ Invalid URL format!\n\n"
                "Send MediaDelivery or M3U8 URL"
            )
            return

        msg = await update.message.reply_text("🔄 Processing URL...")

        try:
            # Get auth tokens
            auth_data = await self._get_auth_data(url)
            if auth_data:
                self.headers.update(auth_data)

            # Get video info first
            video_info = await self._get_video_info(url)
            if not video_info:
                raise Exception("Failed to get video info")

            # Update message with video info
            await msg.edit_text(
                f"📥 Found video:\n"
                f"Quality: {video_info.get('quality', 'N/A')}\n"
                f"Duration: {video_info.get('duration', 'N/A')}s\n"
                f"Starting download..."
            )

            # Download video
            output_file = await self._process_download(url, msg, video_info)

            # Send to Telegram
            await msg.edit_text("📤 Uploading to Telegram...")
            
            with open(output_file, 'rb') as video:
                await update.message.reply_video(
                    video,
                    caption=f"✅ Download Complete!\n"
                           f"🎥 {video_info.get('quality', 'N/A')}\n"
                           f"⏱️ {video_info.get('duration', 'N/A')}s\n"
                           f"🕒 {self.current_time}\n"
                           f"👤 @{self.current_user}",
                    supports_streaming=True
                )

            # Cleanup
            os.remove(output_file)
            await msg.delete()

        except Exception as e:
            await msg.edit_text(f"❌ Error: {str(e)}")

    async def _get_auth_data(self, url: str) -> dict:
        """Get authentication data"""
        try:
            video_id = self._extract_video_id(url)
            auth_url = f"https://iframe.mediadelivery.net/auth/{video_id}"
            
            async with aiohttp.ClientSession() as session:
                # First request to get cookie
                async with session.get(auth_url, ssl=False) as response:
                    if response.status == 200:
                        cookie = response.headers.get('Set-Cookie', '')
                        
                # Second request to get token
                headers = {**self.headers, 'Cookie': cookie}
                async with session.post(
                    auth_url,
                    headers=headers,
                    json={"url": url},
                    ssl=False
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return {
                            'Authorization': f"Bearer {data.get('token')}",
                            'Cookie': cookie,
                            'X-CSRF-TOKEN': data.get('csrf_token', ''),
                        }
            return {}
        except:
            return {}

    async def _get_video_info(self, url: str) -> dict:
        """Get video information"""
        try:
            video_id = self._extract_video_id(url)
            info_url = f"https://iframe.mediadelivery.net/embed/{video_id}/info"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(info_url, headers=self.headers, ssl=False) as response:
                    if response.status == 200:
                        return await response.json()
            return {}
        except:
            return {}

    async def _process_download(self, url: str, msg, video_info: dict) -> str:
        """Process the download"""
        video_id = self._extract_video_id(url)
        work_dir = Path(self.temp_dir) / video_id
        work_dir.mkdir(parents=True, exist_ok=True)
        
        # Get playlist
        playlist_info = await self._get_playlist(url)
        if not playlist_info['segments']:
            raise Exception("No segments found")

        # Download segments
        output_file = work_dir / f"{video_id}.mp4"
        segment_files = []
        total_segments = len(playlist_info['segments'])

        async with aiohttp.ClientSession() as session:
            for i, segment in enumerate(playlist_info['segments'], 1):
                segment_url = urljoin(playlist_info['base_url'], segment.uri)
                segment_file = work_dir / f"segment_{i}.ts"

                # Try download with retries
                success = False
                for attempt in range(5):
                    try:
                        await self._download_segment(
                            segment_url, 
                            segment_file,
                            session,
                            attempt
                        )
                        segment_files.append(str(segment_file))
                        success = True
                        break
                    except Exception as e:
                        if attempt == 4:  # Last attempt
                            raise Exception(f"Segment {i} failed: {str(e)}")
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
                        await self._refresh_auth(url)  # Refresh auth between attempts

                if success and i % max(1, total_segments // 20) == 0:
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

    async def _download_segment(self, url: str, file_path: Path, session: aiohttp.ClientSession, attempt: int):
        """Download segment with enhanced error handling"""
        try:
            # Add attempt number to headers to avoid caching
            headers = {**self.headers, 'X-Attempt': str(attempt)}
            
            async with session.get(url, headers=headers, ssl=False) as response:
                if response.status != 200:
                    raise Exception(f"Status {response.status}")
                
                # Validate content
                content_type = response.headers.get('Content-Type', '')
                if not content_type.startswith(('video/', 'application/octet-stream')):
                    raise Exception(f"Invalid content type: {content_type}")
                
                # Check content length
                content_length = int(response.headers.get('Content-Length', 0))
                if content_length < 100:  # Arbitrary minimum size
                    raise Exception("Segment too small")
                
                async with aiofiles.open(file_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(self.chunk_size):
                        await f.write(chunk)
                        
                # Verify file size
                if os.path.getsize(file_path) < 100:
                    raise Exception("Downloaded segment too small")
                    
        except Exception as e:
            if os.path.exists(file_path):
                os.remove(file_path)
            raise Exception(f"Download failed: {str(e)}")

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
