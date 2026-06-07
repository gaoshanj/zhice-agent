FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# 安装 Playwright 浏览器
RUN playwright install chromium --with-deps

# 复制源码
COPY src/ ./src/

# 非 root 运行
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
