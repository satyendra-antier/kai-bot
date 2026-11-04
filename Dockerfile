FROM node:22-alpine AS admin
WORKDIR /admin
COPY admin/package.json ./
RUN npm install
COPY admin/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=admin /admin/dist ./kai/static/admin
ENV PYTHONPATH=/app
CMD ["uvicorn", "kai.api:app", "--host", "0.0.0.0", "--port", "3000"]
