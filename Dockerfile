FROM docker.m.daocloud.io/library/python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git tzdata && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
ENV PYTHONUNBUFFERED=1 PYTHONOPTIMIZE=1 GIT_PYTHON_REFRESH=quiet
COPY server/ ./server/
WORKDIR /app/server
RUN mkdir -p /app/data && chmod +x entrypoint.sh
EXPOSE 3004
CMD ["./entrypoint.sh"]
