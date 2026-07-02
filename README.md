# zhishu

一个轻量的指数查询小工具，自用。

每天定时拉一批关键词的指数走势存进 SQLite，留着自己看。
带个简单的网页后台，可以管理关键词、看走势、刷新凭证。

---

## 这是什么

我自己想监控几十个关键词的热度变化，懒得每天点开网页手动查，就写了这个。

- 一个 FastAPI 后端 + 一个 SQLite 数据库 + 一个浏览器后台页面
- cron 定时跑，每天下午抓一次（百度指数一般 14-16 点才更新前一天的数据），并滚动清理过期数据
- 网页后台一栏粘贴 Cookie + Cipher-Text，验证通过就保存
- 后台会显示 Cipher-Text 的生成时间和已用多久，抓取开始失败时重新复制一次即可
- HTTP API 想给别的脚本调也行
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

Cipher-Text 本身不带过期时间（约每天轮换一次），后台显示它的生成时间和已经用了多久；
等到定时任务或实时抓取开始失败，再回浏览器重新复制一次即可。

### 加关键词

「关键词管理」面板里输入逗号分隔的词，点 + 添加。

### 代理（可选）

「代理设置」卡片里填 `http://...` 或 `socks5://...`，保存后定时抓取和实时抓取都会走它；
留空就用服务器本机 IP 直连。也可以用 `.env` 里的 `ZHISHU_HTTP_PROXY` 设默认值（后台保存的优先）。

### 看数据

下拉选关键词 + 选时间范围 → 显示走势折线图 + 数据表。

也能点「实时抓取」立即拉一次（不必等定时任务）。

---

## 自动化

装的时候自动加好 cron，每天 15:05 跑一次（百度指数一般在下午 14-16 点更新前一天的数据，凌晨抓只能拿到前天的）。改时间：

```
sudo nano /etc/cron.d/zhishu-daily
```

时间按服务器本地时区算；海外服务器建议先把时区设成北京时间：

```
sudo timedatectl set-timezone Asia/Shanghai
```

手动跑一次：

```
sudo -u zhishu /opt/zhishu/venv/bin/python /opt/zhishu/scripts/run_daily.py
```

### 自动清理

每天任务跑完会顺手滚动清理：历史指数和运行记录只留最近 45 天（`ZHISHU_RETENTION_DAYS` 可改）。
关键词添加满 60 天自动删除、不再抓取（`ZHISHU_KEYWORD_TTL_DAYS` 可改，0 表示不自动删）——一部剧一般盯一两个月就够了，不用手动清列表。
日志（`api.log` / `cron.log` / `daily.log`）由 logrotate 每天滚动、保留 45 天并压缩归档，不会无限堆积。
凭证文件（Cookie / Cipher-Text）每次保存都是覆盖写，旧值不留存，也不会写进任何日志。

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
sudo rm /etc/systemd/system/zhishu-api.service /etc/cron.d/zhishu-daily /etc/logrotate.d/zhishu
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
GET  /api/proxy                     查看代理设置
POST /api/proxy                     设置代理 {"proxy":"http://..."}（留空清除）
GET  /api/runs                      任务运行记录
```

---

## 自用 / 免责

这是自己用的小工具，仅做个人学习和数据观察。

不要拿去爬量大、不要拿去倒卖数据、不要拿去做商业用途，那是你自己的事，与作者无关。

代码按原样提供，不保证持续可用（目标站点可能随时调整接口或访问策略）。MIT 协议。

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
