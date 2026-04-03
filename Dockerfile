FROM ghcr.io/openclaw/openclaw:latest

USER root

# Python 3 + 中文字体 + uv
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      curl \
      python3-pip && \
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    ln -s /root/.local/bin/uv /usr/local/bin/uv && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 系统级安装 portfolio-health-check 依赖（agent 用 python/python3 直接调用）
RUN pip3 install --break-system-packages pandas numpy requests matplotlib
