FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY llm_router ./llm_router
COPY run.py ./
EXPOSE 8000
# mount your config:  docker run -p 8000:8000 -v $PWD/config.yaml:/app/config.yaml -e ROUTER_MASTER_KEY=... llm-router
CMD ["python", "run.py", "--host", "0.0.0.0"]
