FROM python:3.11-slim

# Set workdir
WORKDIR /app

# Copy files
COPY std_bot.py user_bot.py requirements.txt /app/

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Default port for Heroku
EXPOSE 8080

# Heroku uses Procfile, so no CMD needed
