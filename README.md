# 知数 zhishu - 百度指数轻量查询服务

一个**专为小服务器（2C2G就够）**设计的百度指数查询和定时抓取工具。
带 HTTP API、自动定时抓取、SQLite 存储、Web 界面更新 Cookie。

---

## 这是什么

如果你需要：
- 🎯 长期监控**一批关键词**的百度指数走势
- 🤖 给别的程序提供 **HTTP API** 调用
- ⏰ **每天定时**自动抓取存档
- 💻 部署在**2C2G 小服务器**上不卡顿
- 🍪 用浏览器复制 Cookie 就能用，电脑**不需要装任何东西**

这个项目就是为你做的。

---

## 整体架构

```
                ┌─────────────────────────────────┐
                │       你的 Debian 服务器          │
                │                                  │
   你的浏览器 ──┼──→ HTTP API (端口 8000)         │
                │      ↓                           │
                │   爬虫核心 (curl_cffi 模拟浏览器) │
                │      ↓                           │
                │   SQLite 数据库 (data/zhishu.db) │
                │      ↑                           │
                │   Cron 定时任务 (每天凌晨 3 点)   │
                └─────────────────────────────────┘
```

资源占用预估：内存 100-200 MB，CPU 几乎为零（除了抓取那几分钟）。

---

## 系统要求

- **操作系统**：Debian 10+ / Ubuntu 20.04+（其他 Linux 也行，但安装脚本只测过 Debian/Ubuntu）
- **硬件**：1核1G 都能跑，2核2G 富富有余
- **网络**：需要能访问 `index.baidu.com`
- **百度账号**：至少一个能登录百度指数的账号（推荐用专门注册的小号，不要用主号）

---

## 一键安装（推荐）

SSH 登录你的服务器后，执行：

```bash
curl -fsSL https://raw.githubusercontent.com/nailao11/zhishu/claude/baidu-index-tool-comparison-tsml69/scripts/install.sh | sudo bash
```

脚本会自动：
- 安装 Python 和系统依赖
- 创建专用运行用户 `zhishu`
- 下载代码到 `/opt/zhishu`
- 创建虚拟环境并装好依赖
- 生成 API Token（**请务必记住屏幕显示的 Token！**）
- 注册 systemd 服务（开机自启 + 挂了自动重启）
- 添加 cron 任务（每天 03:00 抓取）

安装完成后你会看到类似输出：

```
[INFO] 安装完成！
访问地址:   http://<服务器IP>:8000
API 文档:   http://<服务器IP>:8000/docs
```

---

## 配置 Cookie（必做）

百度指数需要登录才能查，所以必须配置你的百度账号 Cookie。

### 1. 从浏览器获取 Cookie

1. 打开 Chrome / Edge / Firefox 浏览器
2. 访问 https://index.baidu.com **并登录你的百度账号**（建议用小号）
3. 按 **F12** 打开开发者工具
4. 切到 **Network**（网络）选项卡
5. 刷新一下页面（按 F5）
6. 在请求列表里随便点一个发往 `index.baidu.com` 的请求
7. 在右侧找到 **Request Headers**，找到 **Cookie** 这一行
8. **完整复制** Cookie 后面的全部内容（一般几百字符）

### 2. 上传 Cookie 到服务器

有两种方式，二选一：

**方式 A：通过 API 上传**（推荐，不用 SSH）

直接用浏览器访问 `http://<你的服务器IP>:8000/docs`，找到 `POST /api/cookie` 接口，点 "Try it out"，把 Token 填到 Authorize 按钮里，然后 Body 里填：

```json
{"cookie": "把刚才复制的 Cookie 完整粘贴到这里"}
```

点 Execute，看到 `{"status":"ok"}` 就成功了。

**方式 B：SSH 编辑文件**

```bash
sudo nano /opt/zhishu/config/cookies.txt
```

把里面所有内容删掉，粘贴你的 Cookie，按 `Ctrl+O` 保存、`Ctrl+X` 退出。

---

## 添加关键词

```bash
# 替换 <TOKEN> 为你的真实 Token
curl -X POST http://localhost:8000/api/keywords \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"keywords":["python","golang","人工智能"]}'
```

返回 `{"added":3,"skipped":0}` 就是加成功了。

查看当前所有关键词：
```bash
curl http://localhost:8000/api/keywords \
  -H "Authorization: Bearer <TOKEN>"
```

删除某个关键词：
```bash
curl -X DELETE http://localhost:8000/api/keywords/python \
  -H "Authorization: Bearer <TOKEN>"
```

---

## API 接口一览

完整文档：浏览器访问 `http://<你的服务器IP>:8000/docs` 有交互式文档。

| 方法 | 路径 | 作用 |
|------|------|------|
| GET  | `/api/health` | 健康检查（不需要 Token） |
| POST | `/api/query` | **实时**查询关键词（会触发爬取） |
| GET  | `/api/index/{keyword}` | 从数据库读历史数据 |
| GET  | `/api/latest/{keyword}` | 关键词最新一天指数 |
| GET  | `/api/keywords` | 列出所有关键词 |
| POST | `/api/keywords` | 添加关键词 |
| DELETE | `/api/keywords/{keyword}` | 删除关键词 |
| POST | `/api/cookie` | 更新 Cookie |
| GET  | `/api/runs` | 查看定时任务运行记录 |

### 常用调用示例

**实时查询一批关键词最近 30 天数据：**
```bash
curl -X POST http://localhost:8000/api/query \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"keywords":["python","golang"],"days":30}'
```

**读取已抓取的历史数据：**
```bash
curl "http://localhost:8000/api/index/python?start_date=2026-06-01&end_date=2026-06-28" \
  -H "Authorization: Bearer <TOKEN>"
```

**查看最近的定时任务执行情况：**
```bash
curl http://localhost:8000/api/runs -H "Authorization: Bearer <TOKEN>"
```

---

## 定时任务

安装脚本已经自动创建了 cron 任务：**每天凌晨 03:00 自动抓取所有已启用的关键词。**

查看定时配置：
```bash
cat /etc/cron.d/zhishu-daily
```

修改时间（比如改到凌晨 4 点）：
```bash
sudo sed -i 's|^0 3 |0 4 |' /etc/cron.d/zhishu-daily
```

手动立即执行一次抓取（测试用）：
```bash
sudo -u zhishu /opt/zhishu/venv/bin/python /opt/zhishu/scripts/run_daily.py
```

---

## 常用维护命令

```bash
# 服务状态
sudo systemctl status zhishu-api

# 重启服务
sudo systemctl restart zhishu-api

# 查看 API 实时日志
sudo tail -f /opt/zhishu/logs/api.log

# 查看 cron 日志
sudo tail -f /opt/zhishu/logs/cron.log

# 查看每月抓取日志
ls /opt/zhishu/logs/

# 查看数据库内容（需要 sqlite3）
sudo apt install sqlite3
sudo -u zhishu sqlite3 /opt/zhishu/data/zhishu.db "SELECT * FROM run_log ORDER BY id DESC LIMIT 5"
```

---

## 常见问题

### Q1：Cookie 多久失效？
A：通常 **几天到几周** 不等。失效后调用 API 会返回 401，定时任务的运行日志里也能看到 `Cookie 失效` 报错。重新按上面"配置 Cookie"的步骤更新一次即可。

### Q2：被百度限流了怎么办？
A：本工具已经做了以下保护：
- 模拟真实 Chrome 的 TLS 指纹
- 单次最多 5 个关键词一起请求
- 批次之间随机等待 2-4 秒
- 触发限流后会自动等 60 秒重试一次

如果你的关键词非常多（200+），可以：
- 改大 `sleep_between_batch` 参数
- 准备多个百度小号轮换（需要二次开发）

### Q3：100 个关键词每天抓取大概要多久？
A：100 个关键词 / 每批 5 个 = 20 批，每批约 3-5 秒（包含延迟），**总耗时大约 1-2 分钟**。

### Q4：怎么知道关键词指数太低没数据？
A：百度指数对**搜索量极低**的词不收录。这种关键词查询时百度会返回空数据，本工具会记录为 0。

### Q5：要不要开通百度指数会员？
A：**基础查询不需要**。会员主要解锁的是：
- 更长的历史数据（普通号一般能看近 6 个月）
- 行业排行榜、需求图谱等高级数据
- 数据导出 Excel 功能

只是查每日指数值，普通账号完全够用。

### Q6：能不能查多个城市/地区？
A：能，调用 API 时传 `area` 参数。常用代码：
- `0` = 全国（默认）
- `911` = 北京
- `912` = 上海
- `913` = 广州
- 完整代码表请搜索"百度指数地区代码"

### Q7：数据安全吗？
A：
- Cookie 文件权限设为 600（仅 zhishu 用户可读）
- 所有 API 都用 Bearer Token 鉴权（除了 health 接口）
- 强烈建议给服务器加防火墙，只开放必要端口
- 如果服务器有公网 IP，**不要把 8000 端口对全网开放**，最好套个 Nginx + HTTPS

---

## 升级 / 卸载

**升级到最新代码：**
```bash
cd /opt/zhishu
sudo git pull
sudo /opt/zhishu/venv/bin/pip install -r requirements.txt
sudo systemctl restart zhishu-api
```

**完全卸载：**
```bash
sudo systemctl stop zhishu-api
sudo systemctl disable zhishu-api
sudo rm /etc/systemd/system/zhishu-api.service
sudo rm /etc/cron.d/zhishu-daily
sudo systemctl daemon-reload
sudo userdel zhishu
sudo rm -rf /opt/zhishu
```

---

## 项目结构

```
zhishu/
├── README.md                  # 本文档
├── requirements.txt           # Python 依赖
├── .env.example               # 配置模板
├── .gitignore
├── config/
│   ├── cookies.txt.example    # Cookie 模板
│   └── keywords.txt.example   # 关键词模板
├── src/                       # 核心代码
│   ├── crawler.py             # 百度指数爬虫
│   ├── db.py                  # SQLite 数据库
│   ├── config.py              # 配置加载
│   └── api.py                 # FastAPI 服务
├── scripts/
│   ├── install.sh             # 一键安装
│   └── run_daily.py           # 定时任务入口
└── systemd/
    └── zhishu-api.service     # systemd 服务定义
```

---

## 鸣谢

爬虫核心思路参考自 [feiys22/baidu-index-crawler-tutorial-2.0](https://github.com/feiys22/baidu-index-crawler-tutorial-2.0)，
使用 curl_cffi 进行 TLS 指纹模拟。

## License

MIT
