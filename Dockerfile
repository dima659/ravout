FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/var/data RUN_BOT=true DEV_MODE=false
WORKDIR /app
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt
RUN mkdir -p /app/app /app/static/assets /var/data
# Все исходники намеренно лежат в корне GitHub: Docker сам создаёт папки.
COPY __init__.py core.py main.py bot.py ./app/
COPY index.html style.css app.js ./static/
COPY brand.png ./static/assets/brand.png
COPY start.py .
EXPOSE 8000
CMD ["python", "start.py"]
