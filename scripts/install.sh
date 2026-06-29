#!/bin/bash
#
# Zhishu 一键安装脚本（Debian/Ubuntu）
#
# 用法 A（curl-pipe-bash，从远端拉指定分支）：
#   REPO_BRANCH=main curl -fsSL \
#     https://raw.githubusercontent.com/nailao11/zhishu/main/scripts/install.sh \
#     | sudo -E bash
#
# 用法 B（本地 clone，直接用 working tree 当前分支）：
#   git clone https://github.com/nailao11/zhishu.git && cd zhishu
#   sudo bash scripts/install.sh
#   （会自动用当前 checkout 的分支，不再硬编码）
#
# 可覆盖变量：
#   INSTALL_DIR    安装目录，默认 /opt/zhishu
#   SERVICE_USER   运行用户，默认 zhishu
#   REPO_URL       远端仓库地址
#   REPO_BRANCH    分支名（远端模式必填；本地模式留空则用当前 checkout）
#
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/zhishu}"
SERVICE_USER="${SERVICE_USER:-zhishu}"
REPO_URL="${REPO_URL:-https://github.com/nailao11/zhishu.git}"
REPO_BRANCH="${REPO_BRANCH:-}"

# 如果脚本是从本地 clone 运行的，自动用 working tree 的分支，免去硬编码
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || echo "")"
LOCAL_REPO=""
if [ -n "$SCRIPT_DIR" ] && [ -d "$SCRIPT_DIR/../.git" ]; then
    LOCAL_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
    if [ -z "$REPO_BRANCH" ]; then
        REPO_BRANCH="$(git -C "$LOCAL_REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")"
    fi
fi
# 还没拿到分支名（远端模式但用户没设 REPO_BRANCH）兜底
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

# -------- 1. 安装系统依赖 --------
info "Step 1/7: 安装系统依赖"
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    git curl ca-certificates \
    build-essential libffi-dev libssl-dev

# -------- 2. 创建运行用户 --------
info "Step 2/7: 创建运行用户 ${SERVICE_USER}"
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
else
    info "用户 ${SERVICE_USER} 已存在，跳过"
fi

# -------- 3. 克隆或更新代码 --------
info "Step 3/7: 部署代码到 ${INSTALL_DIR}（分支：${REPO_BRANCH}）"
if [ -d "$INSTALL_DIR/.git" ]; then
    info "已存在仓库，执行 pull"
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
    git -C "$INSTALL_DIR" fetch origin "$REPO_BRANCH"
    git -C "$INSTALL_DIR" checkout "$REPO_BRANCH"
    git -C "$INSTALL_DIR" reset --hard "origin/$REPO_BRANCH"
elif [ -n "$LOCAL_REPO" ]; then
    info "从本地 clone 部署：${LOCAL_REPO} → ${INSTALL_DIR}"
    git clone --branch "$REPO_BRANCH" "$LOCAL_REPO" "$INSTALL_DIR"
    # 改 origin 指向真实远端，方便日后 update.sh 拉新版本
    git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"
else
    git clone --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

# -------- 4. 创建 Python 虚拟环境并安装依赖 --------
info "Step 4/7: 创建 Python 虚拟环境"
if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip wheel
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# -------- 5. 准备数据目录和配置 --------
info "Step 5/7: 准备目录和配置"
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
    warn "Cookie 文件还未配置，请先编辑 $INSTALL_DIR/config/cookies.txt"
    warn "或通过 API 调用 POST /api/cookie 上传"
fi

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
chmod 600 "$INSTALL_DIR/.env" "$INSTALL_DIR/config/cookies.txt"

# -------- 6. 安装 systemd 服务 --------
info "Step 6/7: 安装 systemd 服务"
cp "$INSTALL_DIR/systemd/zhishu-api.service" /etc/systemd/system/zhishu-api.service
systemctl daemon-reload
systemctl enable zhishu-api.service
systemctl restart zhishu-api.service

# -------- 7. 安装 cron 定时任务 --------
info "Step 7/7: 安装 cron 定时任务（每天 03:00 抓取）"
CRON_FILE=/etc/cron.d/zhishu-daily
cat > "$CRON_FILE" <<EOF
# 每天凌晨 3 点抓取百度指数
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 3 * * * $SERVICE_USER cd $INSTALL_DIR && $INSTALL_DIR/venv/bin/python scripts/run_daily.py >> $INSTALL_DIR/logs/cron.log 2>&1
EOF
chmod 644 "$CRON_FILE"

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
echo "  下一步必做："
echo "    1. 编辑 $INSTALL_DIR/config/cookies.txt 填入百度 Cookie"
echo "       sudo nano $INSTALL_DIR/config/cookies.txt"
echo "    2. 添加关键词："
echo "       curl -X POST http://localhost:8000/api/keywords \\"
echo "         -H 'Authorization: Bearer <你上面的Token>' \\"
echo "         -H 'Content-Type: application/json' \\"
echo "         -d '{\"keywords\":[\"python\",\"golang\"]}'"
echo "    3. 测试一次抓取："
echo "       sudo -u $SERVICE_USER $INSTALL_DIR/venv/bin/python $INSTALL_DIR/scripts/run_daily.py"
echo ""
