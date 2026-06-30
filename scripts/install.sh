#!/bin/bash
#
# Zhishu 一键安装脚本（Debian/Ubuntu）
#
# 用法：
#   curl -fsSL https://raw.githubusercontent.com/nailao11/zhishu/main/scripts/install.sh | sudo bash
#   或克隆仓库后直接运行：sudo bash scripts/install.sh
#
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/zhishu}"
SERVICE_USER="${SERVICE_USER:-zhishu}"
REPO_URL="${REPO_URL:-https://github.com/nailao11/zhishu.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"

# -------- 颜色输出 --------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

if [ "$(id -u)" -ne 0 ]; then
    error "请用 root 或 sudo 运行此脚本"
    exit 1
fi

info "Step 1/8: 安装系统依赖"
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    git curl ca-certificates \
    build-essential libffi-dev libssl-dev

info "Step 2/8: 创建运行用户 ${SERVICE_USER}"
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
else
    info "用户 ${SERVICE_USER} 已存在，跳过"
fi

info "Step 3/8: 部署代码到 ${INSTALL_DIR}"
if [ -d "$INSTALL_DIR/.git" ]; then
    info "已存在仓库，执行 pull"
    git -C "$INSTALL_DIR" fetch origin "$REPO_BRANCH"
    git -C "$INSTALL_DIR" checkout "$REPO_BRANCH"
    git -C "$INSTALL_DIR" reset --hard "origin/$REPO_BRANCH"
else
    git clone --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

info "Step 4/8: 创建 Python 虚拟环境"
if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip wheel
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

info "Step 5/8: 准备目录和配置"
mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/logs" "$INSTALL_DIR/config"

if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    TOKEN=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets;print(secrets.token_hex(32))")
    sed -i "s|ZHISHU_API_TOKEN=.*|ZHISHU_API_TOKEN=${TOKEN}|" "$INSTALL_DIR/.env"
    info "已生成 API Token，记得保存："
    echo ""
    echo -e "  ${GREEN}${TOKEN}${NC}"
    echo ""
fi

if [ ! -f "$INSTALL_DIR/config/cookies.txt" ]; then
    cp "$INSTALL_DIR/config/cookies.txt.example" "$INSTALL_DIR/config/cookies.txt"
fi
if [ ! -f "$INSTALL_DIR/config/cipher_text.txt" ]; then
    cp "$INSTALL_DIR/config/cipher_text.txt.example" "$INSTALL_DIR/config/cipher_text.txt"
fi
warn "凭证文件还没配置，去 /admin 网页一次性粘贴 Cookie + Cipher-Text 即可"

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
chmod 600 "$INSTALL_DIR/.env" "$INSTALL_DIR/config/cookies.txt" "$INSTALL_DIR/config/cipher_text.txt" 2>/dev/null || true

info "Step 6/8: 安装 systemd 服务"
cp "$INSTALL_DIR/systemd/zhishu-api.service" /etc/systemd/system/zhishu-api.service
systemctl daemon-reload
systemctl enable zhishu-api.service
systemctl restart zhishu-api.service

info "Step 7/8: 安装 cron 定时任务（每天 03:00 抓取）"
CRON_FILE=/etc/cron.d/zhishu-daily
cat > "$CRON_FILE" <<EOF
# 每天凌晨 3 点抓取指数
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 3 * * * $SERVICE_USER cd $INSTALL_DIR && $INSTALL_DIR/venv/bin/python scripts/run_daily.py >> $INSTALL_DIR/logs/cron.log 2>&1
EOF
chmod 644 "$CRON_FILE"

info "Step 8/8: 配置日志滚动（logrotate，保留 45 天）"
LOGROTATE_FILE=/etc/logrotate.d/zhishu
cat > "$LOGROTATE_FILE" <<EOF
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
chmod 644 "$LOGROTATE_FILE"

# -------- 完成提示 --------
echo ""
info "=========================================="
info "安装完成！"
info "=========================================="
echo ""
echo "  安装目录:   $INSTALL_DIR"
echo "  服务状态:   systemctl status zhishu-api"
echo "  服务日志:   tail -f $INSTALL_DIR/logs/api.log"
echo "  Cron 日志:  tail -f $INSTALL_DIR/logs/cron.log"
echo ""
echo "  访问地址:   http://<服务器IP>:8000"
echo "  API 文档:   http://<服务器IP>:8000/docs"
echo ""
echo "  下一步必做（都在网页后台完成）："
echo "    1. 浏览器打开 http://<服务器IP>:8000/admin ，输入上面的 Token"
echo "    2. 在「凭证管理」粘贴 Cookie + Cipher-Text 保存"
echo "    3. 在「管理关键词」添加要监控的关键词"
echo "    4. 可点「实时抓取」立即验证，或等每天 03:00 自动运行"
echo ""
