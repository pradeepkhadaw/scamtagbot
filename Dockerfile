# Dockerfile
FROM python:3.11-slim

# Set workdir
WORKDIR /app

# Copy files
COPY . /app

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Default port
EXPOSE 8080

# Start command (for STD worker by default)
CMD ["python", "main.py", "std"]
