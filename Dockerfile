FROM python:3.9-slim

# Set environment variables
ENV CURRENT_TIME="2025-06-14 05:09:22"
ENV CURRENT_USER="harshMrDev"

# Install FFmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Create temp directory
RUN mkdir -p /tmp/stream_downloads

# Run bot
CMD ["python", "main.py"]