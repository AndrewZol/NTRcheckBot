FROM python:3.11-slim

RUN apt-get update && apt-get install -y libzbar0 && rm -rf /var/lib/apt/lists/*

# Устанавливаем Node.js для MCP
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-u", "bot.py"]
