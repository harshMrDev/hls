FROM python:3.9-slim

# Set environment variables
ENV CURRENT_TIME="2025-06-14 03:50:33"
ENV CURRENT_USER="harshMrDev"

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Create temp directory
RUN mkdir -p /tmp/stream_downloads

# Command to run the bot
CMD ["python", "-m", "bot"]