#!/bin/bash
# Azure VM 初始化脚本 (Ubuntu 22.04)
set -e

echo "=== 安装 Docker ==="
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

echo "=== 安装 Docker Compose ==="
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" \
  -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

echo "=== 安装 git ==="
sudo apt-get update && sudo apt-get install -y git

echo "=== 克隆项目 ==="
git clone https://github.com/<your-org>/zhice-agent.git
cd zhice-agent
cp .env.example .env

echo "=== 请编辑 .env 填入配置后，运行以下命令启动服务 ==="
echo "  cd zhice-agent && docker-compose -f deploy/docker-compose.yml up -d"
