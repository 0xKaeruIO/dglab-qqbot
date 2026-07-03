# DGLAB-qqbot · AstrBot 插件

在 QQ 群中通过命令连接 [DG-LAB](https://www.dungeon-lab.com/) App，实现多用户独立连接、强度控制与波形切换。

源仓库：https://github.com/Blathroat/DGLAB-qqbot

---

## 主要功能

- 每位 QQ 用户独立 WebSocket 端口与 App 连接
- 群聊内控制所有已连接用户的强度、波形
- 随机 / 全体随机增减强度
- 可配置命令前缀（默认 `dgkab`）

---

## 安装

### 1. 部署 AstrBot + NapCat

推荐使用 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 作为机器人框架，[NapCat](https://github.com/NapNeko/NapCatQQ) 作为 QQ 协议端（OneBot v11）。

### 2. 安装本插件

将本仓库克隆到 AstrBot 的插件目录：

```bash
cd AstrBot/data/plugins
git clone https://github.com/0xKaeruIO/dglab-qqbot.git astrbot_plugin_dglab
```

在 AstrBot WebUI → **插件管理** 中安装依赖并重载插件。

### 3. 配置插件

在 WebUI 的插件配置中填写：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `command_prefix` | 命令前缀 | `dgkab` |
| `ip_addr` | App 扫码连接的 WebSocket 地址（不含端口） | `ws://127.0.0.1` |
| `base_port` | 起始端口，每用户递增 | `5678` |
| `qr_image_url` | 二维码图床 URL（可选） | 空 |

`ip_addr` 必须填写 **手机能访问到的** 公网 IP 或局域网 IP，否则 App 无法连接。

---

## 命令用法

消息格式：`{前缀}{命令} [参数]`，前缀与命令之间可以有空格。

示例（默认前缀 `dgkab`）：

```
dgkab帮助
dgkab 新建连接
dgkab 设置强度 100
dgkab 增加强度 A 20
dgkab 改变波形 潮汐
dgkab 当前状态
dgkab 设置名称 呱呱糕
```

### 命令列表

| 命令 | 说明 |
|------|------|
| 新建连接 | 生成二维码，绑定 DG-LAB App |
| 断开连接 | 主动断开当前用户的 App 连接 |
| 设置强度 / 增加强度 / 降低强度 | 控制所有已连接用户，支持 `A`/`B` 通道，如 `设置强度 A 100` |
| 改变波形 | 更改波形，如 `改变波形 A 潮汐` |
| 随机增加 / 随机降低 | 随机选取一名已连接用户操作 |
| 全体随机增加 / 全体随机降低 | 对所有已连接用户随机分配档位 |
| 当前状态 | 查看所有用户连接与强度信息 |
| 用户列表 | 查看在线用户 |
| 设置名称 | 设置显示名称，如 `设置名称 昵称` |
| 帮助 | 查看命令说明 |

波形名称：呼吸、潮汐、连击、快速按捏、按捏渐强、心跳节奏、压缩、节奏步伐、颗粒摩擦、渐变弹跳、波浪涟漪、雨水冲刷、变速敲击、信号灯、挑逗1、挑逗2

---

## 目录结构

```
├── main.py           # AstrBot 插件入口
├── dglab_core.py     # DGLAB 控制逻辑
├── Pulses.py         # 波形数据
├── metadata.yaml     # 插件元数据
├── _conf_schema.json # 插件配置 Schema
├── requirements.txt  # 插件依赖
└── data/             # 运行时数据（用户名称映射、二维码等）
```

---

## 旧版 QQ 官方 Bot

v1.x 基于 QQ 官方开放平台（`qq-botpy`）的实现已移除。如需参考，请查看 Git 历史中的 `main.py`。

---

## 开源协议

Apache-2.0
