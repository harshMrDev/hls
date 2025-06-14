from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from downloader.stream_downloader import StreamDownloader
import os

class StreamBot:
    def __init__(self):
        self.downloader = StreamDownloader()
        self.current_time = "2025-06-14 03:47:24"
        self.current_user = "harshMrDev"

    async def start_command(self, update: Update, context):
        """Handle /start command"""
        await update.message.reply_text(
            f"👋 Welcome to Stream Downloader Bot!\n\n"
            f"I can handle:\n"
            f"1️⃣ MediaDelivery URLs:\n"
            f"   https://iframe.mediadelivery.net/ID/quality/video.drm\n\n"
            f"2️⃣ M3U8 URLs:\n"
            f"   Any URL ending with .m3u8\n\n"
            f"Just send me the URL and I'll download it!\n\n"
            f"🕒 Time: {self.current_time}\n"
            f"👤 Handler: @{self.current_user}"
        )

    async def handle_url(self, update: Update, context):
        """Handle video URLs"""
        url = update.message.text.strip()
        
        # Validate URL
        if not self._is_valid_url(url):
            await update.message.reply_text(
                "❌ Please send a valid URL!\n\n"
                "Supported formats:\n"
                "1. MediaDelivery: https://iframe.mediadelivery.net/ID/quality/video.drm\n"
                "2. M3U8: Any URL ending with .m3u8"
            )
            return
        
        msg = await update.message.reply_text(
            "🔄 Processing URL...\n"
            "⏳ Please wait..."
        )
        
        try:
            # Progress callback
            async def progress_callback(text):
                await msg.edit_text(
                    f"🔄 {text}\n"
                    f"👤 Handler: @{self.current_user}"
                )
            
            # Process URL
            result = await self.downloader.process_url(url, progress_callback)
            
            # Send video
            await msg.edit_text("📤 Uploading video to Telegram...")
            
            with open(result['file_path'], 'rb') as video:
                await update.message.reply_video(
                    video,
                    caption=(
                        f"✅ Download Complete!\n\n"
                        f"🎥 Type: {result['type']}\n"
                        f"📊 Size: {result['size']/1024/1024:.1f}MB\n"
                        f"🎯 Segments: {result['segments']}\n"
                        f"🕒 Time: {self.current_time}\n"
                        f"👤 Processed by: @{self.current_user}"
                    ),
                    supports_streaming=True
                )
            
            # Cleanup
            os.remove(result['file_path'])
            await msg.delete()
            
        except Exception as e:
            await msg.edit_text(f"❌ Error: {str(e)}")

    def _is_valid_url(self, url: str) -> bool:
        """Validate URL format"""
        return (
            "mediadelivery.net" in url or 
            url.endswith(".m3u8")
        )

def main():
    """Start the bot"""
    bot = StreamBot()
    
    application = Application.builder().token(os.getenv('BOT_TOKEN')).build()
    
    application.add_handler(CommandHandler("start", bot.start_command))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        bot.handle_url
    ))
    
    print(f"""
    🤖 Starting Stream Downloader Bot
    ⏰ Time: {bot.current_time}
    👤 Handler: @{bot.current_user}
    """)
    
    application.run_polling()

if __name__ == "__main__":
    main()