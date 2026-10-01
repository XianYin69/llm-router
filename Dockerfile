FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV LLMROUTER_CONFIG=/app/config.yaml PORT=8000
EXPOSE 8000
CMD ["python", "run.py", "--host", "0.0.0.0"]
