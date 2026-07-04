#!/bin/bash
#
# Zhishu 更新脚本
# 拉取最新代码，更新依赖，重启服务
#
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/zhishu}"
SERVICE_USER="${SERVICE_USER:-zhishu}"
REPO_BRANCH="${REPO_BRANCH:-main}"

GREEN='\033[0;32m'
NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $*"; }

if [ "$(id -u)" -ne 0 ]; then
    echo "请用 sudo 运行" >&2
    exit 1
fi

info "拉取最新代码..."
# --add 会重复追加，先查再加，避免 gitconfig 越积越多
git config --global --get-all safe.directory 2>/dev/null | grep -qxF "$INSTALL_DIR" \
    || git config --global --add safe.directory "$INSTALL_DIR"
git -C "$INSTALL_DIR" fetch origin "$REPO_BRANCH"
git -C "$INSTALL_DIR" reset --hard "origin/$REPO_BRANCH"

info "更新 Python 依赖..."
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"

info "修正文件所有权..."
# 预创建日志文件，保证属主是运行用户（logrotate copytruncate 需要写权限）
mkdir -p "$INSTALL_DIR/logs"
touch "$INSTALL_DIR/logs/api.log" "$INSTALL_DIR/logs/cron.log" "$INSTALL_DIR/logs/daily.log"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

info "同步 systemd 服务配置..."
cp "$INSTALL_DIR/systemd/zhishu-api.service" /etc/systemd/system/zhishu-api.service
systemctl daemon-reload

info "配置日志滚动（logrotate，保留 45 天）..."
cat > /etc/logrotate.d/zhishu <<EOF
# 统一管理 api.log / cron.log / daily.log：每天滚动、保留 45 天、压缩归档
$INSTALL_DIR/logs/*.log {
    daily
    rotate 45
    missingok
    notifempty
    compress
    delaycompress
    copytruncate
    su $SERVICE_USER $SERVICE_USER
}
EOF
chmod 644 /etc/logrotate.d/zhishu
# 清掉旧版按月命名的日志（现在统一写 daily.log）
rm -f "$INSTALL_DIR"/logs/daily_*.log 2>/dev/null || true

info "更新 cron 定时任务（每天 15:05 抓取）..."
cat > /etc/cron.d/zhishu-daily <<EOF
# 每天 15:05 抓取指数
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
5 15 * * * $SERVICE_USER cd $INSTALL_DIR && $INSTALL_DIR/venv/bin/python scripts/run_daily.py >> $INSTALL_DIR/logs/cron.log 2>&1
EOF
chmod 644 /etc/cron.d/zhishu-daily

info "重启服务..."
systemctl restart zhishu-api.service

sleep 2
info "服务状态："
systemctl is-active zhishu-api.service

info "更新完成。"
