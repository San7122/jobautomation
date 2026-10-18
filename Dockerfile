# UAE Job Copilot — containerized daily runner
# Build:  docker build -t uae-job-copilot .
# Run:    docker run --env-file .env uae-job-copilot run
#         docker run --env-file .env uae-job-copilot doctor   # config check

FROM python:3.12-slim

WORKDIR /app

# System deps for Playwright (chromium) + reportlab (resume PDFs)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        libnss3 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libgbm1 \
        libgtk-3-0 \
        libgssapi-krb5-2 \
        libldap-2.5-0 \
        libpango-1.0-0 \
        libu2f-udev \
        libvulkan1 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        fonts-liberation \
        xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium \
    && playwright install-deps chromium

# Application code
COPY . .

# Ensure data/logs dirs exist
RUN mkdir -p data data/resumes data/digests logs

# Default command: show help
ENTRYPOINT ["python3", "main.py"]
CMD ["--help"]
