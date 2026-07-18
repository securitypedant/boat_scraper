FROM python:3.12-slim-bookworm

WORKDIR /app

# Bake version from build arg
ARG GIT_COMMIT=unknown
RUN echo "${GIT_COMMIT}" > /app/.version

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium + its system deps (handles everything)
RUN playwright install chromium && playwright install-deps chromium

# Copy application code
COPY . .

# Ensure mountable data directory exists
RUN mkdir -p /app/data

EXPOSE 5000
ENV FLASK_APP=web.app
CMD ["gunicorn", "-k", "gthread", "-w", "1", "--threads", "4", "-b", "0.0.0.0:5000", "--timeout", "120", "web.app:app"]
