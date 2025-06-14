import os
import re
import m3u8
import aiohttp
import aiofiles
import asyncio
import hashlib
import time
import shutil
from pathlib import Path
from urllib.parse import urlparse, urljoin
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

class StreamBot:
    def __init__(self):
        self.current_time = "2025-06-14 11:46:27"
        self.current_user = "harshMrDev"
        self.base_dir = "/tmp/stream_downloads"
        self.chunk_size = 1024 * 1024  # 1MB chunks
        
        # Essential headers
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Origin': 'https://iframe.mediadelivery.net',
            'Referer': 'https://iframe.mediadelivery.net/',
            'Connection': 'keep-alive',
            'Pragma': 'no-cache',
            'Cache-Control': 'no-cache',
        }
        
        # Create base directory
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
            # Create work directory
            video_id = self._extract_video_id(url)
            work_dir = os.path.join(self.base_dir, video_id)
            if os.path.exists(work_dir):
                shutil.rmtree(work_dir)
            os.makedirs(work_dir)

            # Get auth token if needed
            if "mediadelivery.net" in url:
                token = await self._get_token(url)
                if token:
                    self.headers['Authorization'] = f'Bearer {token}'

            # Get playlist info
            playlist_info = await self._get_playlist(url)
            if not playlist_info or not playlist_info['segments']:
                raise Exception("No segments found in playlist")

            # Download segments
            output_file = os.path.join(work_dir, f"{video_id}.mp4")
            await self._download_segments(
                playlist_info['segments'],
                playlist_info['base_url'],
                output_file,
                work_dir,
                msg
            )

            # Verify file exists
            if not os.path.exists(output_file):
                raise Exception("Failed to create output file")

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
            shutil.rmtree(work_dir)
            await msg.delete()

        except Exception as e:
            await msg.edit_text(f"❌ Error: {str(e)}")
            # Cleanup on error
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

    async def _download_segments(self, segments, base_url: str, output_file: str, work_dir: str, msg):
        segment_files = []
        total_segments = len(segments)
        last_progress = 0

        try:
            async with aiohttp.ClientSession() as session:
                for i, segment in enumerate(segments, 1):
                    segment_url = urljoin(base_url, segment.uri)
                    segment_path = os.path.abspath(os.path.join(work_dir, f"segment_{i:03d}.ts"))
                    
                    # Try download with retries
                    success = False
                    for attempt in range(3):
                        try:
                            await self._download_segment(segment_url, segment_path, session)
                            
                            # Verify downloaded segment
                            if os.path.exists(segment_path) and os.path.getsize(segment_path) > 0:
                                segment_files.append(segment_path)
                                success = True
                                break
                            
                        except Exception as e:
                            if attempt == 2:
                                raise Exception(f"Segment {i} failed after 3 attempts")
                            await asyncio.sleep(2)

                    if not success:
                        raise Exception(f"Failed to download segment {i}")

                    # Update progress
                    progress = int((i / total_segments) * 100)
                    if progress > last_progress:
                        await msg.edit_text(
                            f"📥 Downloading: {i}/{total_segments} segments\n"
                            f"📊 Progress: {progress}%"
                        )
                        last_progress = progress

            # Verify all segments
            expected_count = total_segments
            actual_count = len(segment_files)
            if actual_count != expected_count:
                raise Exception(f"Segment count mismatch: expected {expected_count}, got {actual_count}")

            # Merge segments
            await msg.edit_text(
                "🔄 Merging video segments...\n"
                "⏳ This might take a few minutes..."
            )
            await self._merge_segments(segment_files, output_file)

        except Exception as e:
            # Cleanup on error
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

    async def _merge_segments(self, segment_files: list, output_file: str):
        """Merge TS segments into MP4 with enhanced FFmpeg handling"""
        try:
            list_file = f"{output_file}.txt"
            
            # Verify segments exist before creating concat file
            existing_segments = []
            for segment in segment_files:
                if os.path.exists(segment) and os.path.getsize(segment) > 0:
                    # Use absolute paths and escape spaces
                    abs_path = os.path.abspath(segment)
                    existing_segments.append(abs_path)
                else:
                    raise Exception(f"Missing or empty segment: {segment}")

            if not existing_segments:
                raise Exception("No valid segments to merge")

            # Create FFmpeg concat file with absolute paths
            with open(list_file, 'w', encoding='utf-8') as f:
                for file in existing_segments:
                    # Properly escape the path for FFmpeg
                    escaped_path = file.replace("'", "'\\''").replace('\\', '\\\\')
                    f.write(f"file '{escaped_path}'\n")

            # Verify concat file
            if not os.path.exists(list_file) or os.path.getsize(list_file) == 0:
                raise Exception("Failed to create concat file")

            # First try with concat demuxer
            cmd = [
                'ffmpeg',
                '-f', 'concat',
                '-safe', '0',
                '-i', list_file,
                '-c', 'copy',
                '-movflags', '+faststart',
                '-y',
                output_file
            ]

            # Run FFmpeg
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=300
                )
            except asyncio.TimeoutError:
                if process:
                    try:
                        process.kill()
                    except:
                        pass
                raise Exception("FFmpeg merge timeout")

            # If concat demuxer fails, try alternative method
            if process.returncode != 0:
                # Create a file list for direct concatenation
                segment_list = '|'.join(existing_segments)
                
                cmd = [
                    'ffmpeg',
                    '-i', f"concat:{segment_list}",
                    '-c', 'copy',
                    '-movflags', '+faststart',
                    '-y',
                    output_file
                ]

                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=300
                    )
                except asyncio.TimeoutError:
                    if process:
                        try:
                            process.kill()
                        except:
                            pass
                    raise Exception("FFmpeg alternative merge timeout")

                if process.returncode != 0:
                    error_msg = stderr.decode().strip() if stderr else "Unknown FFmpeg error"
                    raise Exception(f"Both merge methods failed: {error_msg}")

            # Verify output
            if not os.path.exists(output_file):
                raise Exception("Merged file not found")

            output_size = os.path.getsize(output_file)
            total_input_size = sum(os.path.getsize(f) for f in existing_segments)

            if output_size < total_input_size * 0.5:
                raise Exception("Merged file too small")

        except Exception as e:
            raise Exception(f"Merge failed: {str(e)}")

        finally:
            # Cleanup
            if os.path.exists(list_file):
                try:
                    os.remove(list_file)
                except:
                    pass

    def _is_valid_url(self, url: str) -> bool:
        return ("mediadelivery.net" in url and "/video" in url) or url.endswith(".m3u8")

    def _extract_video_id(self, url: str) -> str:
        if "mediadelivery.net" in url:
            match = re.search(r'/([a-f0-9-]+)/\d+p/', url)
            return match.group(1) if match else f"video_{int(time.time())}"
        return hashlib.md5(url.encode()).hexdigest()[:12]

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
