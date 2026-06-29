#!/bin/bash
#
# Zhishu 更新脚本
# 拉取最新代码，更新依赖，重启服务
#
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/zhishu}"
SERVICE_USER="${SERVICE_USER:-zhishu}"
# 默认从当前安装目录 checkout 的分支拉取；可用 REPO_BRANCH 显式覆盖
REPO_BRANCH="${REPO_BRANCH:-}"

GREEN='\033[0;32m'
NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $*"; }

if [ "$(id -u)" -ne 0 ]; then
    echo "请用 sudo 运行" >&2
    exit 1
fi

git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
if [ -z "$REPO_BRANCH" ]; then
    REPO_BRANCH="$(git -C "$INSTALL_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
fi

info "拉取最新代码（分支：${REPO_BRANCH}）..."
git -C "$INSTALL_DIR" fetch origin "$REPO_BRANCH"
git -C "$INSTALL_DIR" reset --hard "origin/$REPO_BRANCH"

info "更新 Python 依赖..."
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"

info "修正文件所有权..."
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

info "重启服务..."
systemctl restart zhishu-api.service

sleep 2
info "服务状态："
systemctl is-active zhishu-api.service

info "更新完成。"
