import aiohttp
import aiofiles
import m3u8
import os
import asyncio
import re
from urllib.parse import urlparse, urljoin
from pathlib import Path

class StreamDownloader:
    def __init__(self):
        self.current_time = "2025-06-14 03:47:24"
        self.current_user = "harshMrDev"
        self.temp_dir = "/tmp/stream_downloads"
        self.chunk_size = 1024 * 1024  # 1MB chunks
        
    async def process_url(self, url: str, message_callback=None):
        """Process any streaming URL (MediaDelivery or M3U8)"""
        try:
            # Identify URL type
            if "mediadelivery.net" in url:
                return await self.handle_mediadelivery(url, message_callback)
            elif url.endswith(".m3u8"):
                return await self.handle_m3u8(url, message_callback)
            else:
                raise ValueError("Unsupported URL format")
                
    async def handle_mediadelivery(self, url: str, message_callback=None):
        """Handle MediaDelivery specific URLs"""
        try:
            # Extract video ID and quality
            video_id, quality = self._parse_mediadelivery_url(url)
            
            if message_callback:
                await message_callback(f"📥 Processing MediaDelivery video...\nID: {video_id}\nQuality: {quality}")
            
            # Get M3U8 manifest
            playlist_info = await self._get_playlist(url)
            
            # Download segments
            output_file = await self._download_segments(
                playlist_info['segments'],
                playlist_info['base_url'],
                video_id,
                message_callback
            )
            
            return {
                "type": "mediadelivery",
                "file_path": output_file,
                "video_id": video_id,
                "quality": quality,
                "size": os.path.getsize(output_file),
                "segments": len(playlist_info['segments'])
            }
            
        except Exception as e:
            raise Exception(f"MediaDelivery error: {str(e)}")
            
    async def handle_m3u8(self, url: str, message_callback=None):
        """Handle generic M3U8 URLs"""
        try:
            if message_callback:
                await message_callback("📥 Processing M3U8 stream...")
            
            # Get M3U8 manifest
            playlist_info = await self._get_playlist(url)
            
            # Generate video ID from URL
            video_id = self._generate_video_id(url)
            
            # Download segments
            output_file = await self._download_segments(
                playlist_info['segments'],
                playlist_info['base_url'],
                video_id,
                message_callback
            )
            
            return {
                "type": "m3u8",
                "file_path": output_file,
                "video_id": video_id,
                "size": os.path.getsize(output_file),
                "segments": len(playlist_info['segments'])
            }
            
        except Exception as e:
            raise Exception(f"M3U8 error: {str(e)}")

    async def _get_playlist(self, url: str) -> dict:
        """Get M3U8 playlist information"""
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    raise Exception(f"Failed to fetch playlist: {response.status}")
                
                m3u8_content = await response.text()
                playlist = m3u8.loads(m3u8_content)
                
                return {
                    "segments": playlist.segments,
                    "duration": playlist.target_duration,
                    "base_url": self._get_base_url(url)
                }

    async def _download_segments(self, segments, base_url: str, video_id: str, callback=None):
        """Download and merge segments"""
        # Create work directory
        work_dir = os.path.join(self.temp_dir, video_id)
        os.makedirs(work_dir, exist_ok=True)
        
        segment_files = []
        total_segments = len(segments)
        
        if callback:
            await callback(f"Found {total_segments} segments to download...")
        
        # Download all segments
        async with aiohttp.ClientSession() as session:
            for i, segment in enumerate(segments):
                segment_url = urljoin(base_url, segment.uri)
                output_file = os.path.join(work_dir, f"segment_{i}.ts")
                
                await self._download_segment(segment_url, output_file, session)
                segment_files.append(output_file)
                
                if callback and i % 5 == 0:
                    await callback(f"Downloaded {i+1}/{total_segments} segments...")
        
        # Merge segments
        if callback:
            await callback("🔄 Merging segments...")
            
        output_file = os.path.join(work_dir, f"{video_id}.mp4")
        await self._merge_segments(segment_files, output_file)
        
        # Cleanup
        for file in segment_files:
            os.remove(file)
        
        return output_file

    async def _download_segment(self, url: str, output_file: str, session: aiohttp.ClientSession):
        """Download individual segment"""
        try:
            async with session.get(url) as response:
                if response.status != 200:
                    raise Exception(f"Segment download failed: {response.status}")
                    
                async with aiofiles.open(output_file, 'wb') as f:
                    while chunk := await response.content.read(self.chunk_size):
                        await f.write(chunk)
        except Exception as e:
            raise Exception(f"Segment download error: {str(e)}")

    async def _merge_segments(self, segment_files: list, output_file: str):
        """Merge TS segments into MP4"""
        try:
            # Create file list
            list_file = f"{output_file}.txt"
            with open(list_file, 'w') as f:
                for segment in segment_files:
                    f.write(f"file '{segment}'\n")
            
            # Merge using FFmpeg
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
            os.remove(list_file)
            
        except Exception as e:
            raise Exception(f"Merge failed: {str(e)}")

    def _parse_mediadelivery_url(self, url: str) -> tuple:
        """Parse MediaDelivery URL components"""
        pattern = r'/([a-f0-9-]+)/(\d+p)/'
        match = re.search(pattern, url)
        if not match:
            raise ValueError("Invalid MediaDelivery URL format")
        return match.groups()  # (video_id, quality)

    def _get_base_url(self, url: str) -> str:
        """Get base URL for segments"""
        return '/'.join(url.split('/')[:-1])

    def _generate_video_id(self, url: str) -> str:
        """Generate video ID from URL"""
        return hashlib.md5(url.encode()).hexdigest()[:12]