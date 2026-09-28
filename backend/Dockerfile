FROM python:3.12-slim

# ansible 走 SSH 需要 ssh client；keyscan 需要 openssh-client
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
