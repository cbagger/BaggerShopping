FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

ARG KURV_BUILD_COMMIT=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KURV_BUILD_COMMIT=${KURV_BUILD_COMMIT}

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
