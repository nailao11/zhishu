# zhishu

一个轻量的指数查询小工具，自用。

跑在小服务器上，每天定时拉一批关键词的指数走势存进 SQLite，留着自己看。
带个简单的网页后台，可以管理关键词、看走势、刷新凭证。

---

## 这是什么

我自己想监控几十个关键词的热度变化，懒得每天点开网页手动查，就写了这个。

- 一个 FastAPI 后端 + 一个 SQLite 数据库 + 一个浏览器后台页面
- cron 定时跑，每天凌晨抓一次
- 网页后台一栏粘贴 Cookie + Cipher-Text，验证通过就保存
- 凭证过期前后台会显示剩余时间，到点了重新去浏览器复制一次粘上去
- HTTP API 想给别的脚本调也行

资源占用很轻，1 核 1G 都能跑，不需要 MySQL 不需要 Redis。

---

## 装

需要 Debian / Ubuntu，root 或 sudo 跑这一条：

```
curl -fsSL https://raw.githubusercontent.com/nailao11/zhishu/main/scripts/install.sh | sudo bash
```

完事会在屏幕打一段 Token，**自己保存好**，后台登录要用。

如果忘了：
```
sudo grep ZHISHU_API_TOKEN /opt/zhishu/.env
```

---

## 用

浏览器开 `http://<服务器IP>:8000/admin`，第一次进去输 Token。

### 配凭证（核心）

浏览器登录目标站点 → F12 → Network → 找一个返回 200 的 API 请求 → Request Headers 里**同时复制两个值**：

- `Cookie`（整行）
- `Cipher-Text`（整行，格式 `数字_数字_一串编码`）

回 `/admin` 的「凭证管理」卡片，两个框分别粘进去，点保存。会先用一次测试请求验证。

后台会显示 Cipher-Text 还剩多久过期（一般几小时），到点重新去浏览器复制就行。

### 加关键词

「关键词管理」面板里输入逗号分隔的词，点 + 添加。

### 看数据

下拉选关键词 + 选时间范围 → 显示走势折线图 + 数据表。

也能点「实时抓取」立即拉一次（绕过定时任务）。

---

## 自动化

装的时候自动加好 cron，每天 03:00 跑一次。改时间：

```
sudo nano /etc/cron.d/zhishu-daily
```

手动跑一次：

```
sudo -u zhishu /opt/zhishu/venv/bin/python /opt/zhishu/scripts/run_daily.py
```

---

## 升级 / 维护

升级到最新：
```
curl -fsSL https://raw.githubusercontent.com/nailao11/zhishu/main/scripts/update.sh | sudo bash
```

服务状态：
```
sudo systemctl status zhishu-api
```

实时日志：
```
sudo tail -f /opt/zhishu/logs/api.log
```

卸载：
```
sudo systemctl stop zhishu-api && sudo systemctl disable zhishu-api
sudo rm /etc/systemd/system/zhishu-api.service /etc/cron.d/zhishu-daily
sudo userdel zhishu
sudo rm -rf /opt/zhishu
```

---

## HTTP API

完整列表 + 在线调试在 `http://<服务器IP>:8000/docs`，所有接口都需要 `Authorization: Bearer <TOKEN>` 头。

常用：

```
GET  /api/keywords                  列出关键词
POST /api/keywords                  添加 {"keywords":["a","b"]}
GET  /api/index/{keyword}           历史数据
GET  /api/latest/{keyword}          最新一天
POST /api/query                     实时抓取
POST /api/cookie                    更新凭证 {"cookie":"...","cipher_text":"..."}
GET  /api/credentials/status        凭证状态
GET  /api/runs                      任务运行记录
```

---

## 自用 / 免责

这是自己用的小工具，仅做个人学习和数据观察。所有访问行为都使用用户**自己提供的、自己账号的凭证**，与本仓库无关。

不要拿去爬量大、不要拿去倒卖数据、不要拿去做商业用途，那是你自己的事，与作者无关。

代码按原样提供，不保证持续可用（目标站点可能随时调整接口或反爬策略）。MIT 协议。

---

## 项目结构

```
src/
  crawler.py    爬虫核心
  db.py         SQLite 存储
  config.py     配置加载
  api.py        HTTP 接口
  static/       管理后台前端
scripts/
  install.sh    一键安装
  update.sh     更新
  run_daily.py  定时任务入口
systemd/
  zhishu-api.service
config/
  cookies.txt        Cookie（运行时生成）
  cipher_text.txt    Cipher-Text（运行时生成）
```
