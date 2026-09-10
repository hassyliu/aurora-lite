# Aurora Lite · 极光中转

面向个人使用的独立重构版本。只支持 **iptables 与 GOST v3 的 TCP/UDP 端口转发**，支持系统 Python 虚拟环境和 Linux 单文件可执行程序两种部署方式，业务数据与后台任务分别保存在 SQLite 文件中。

**没有 Docker、PostgreSQL、Redis、Node.js 常驻进程，也没有外部 CDN 依赖。** 前端静态文件由 Python 服务直接提供。此版本重新实现已约定的自用功能，不是上游所有功能的兼容替换，不直接读取上游数据库。

## 开始使用

- [部署、升级、备份与卸载说明](DEPLOY.md)
- [Linux AMD64 二进制下载（Debian 13 已验证）](https://github.com/liuzipeng/aurora-lite/releases/tag/v0.2.1)
- [二进制构建说明](BINARY.md)
- [验证记录与适用范围](VALIDATION.md)

## 已实现

- 橙红与米白色的中文亮色界面：总览、服务器内转发管理、落地管理、任务日志、设置；手机底部导航与单列表单。
- 单管理员登录、密码修改、登录频率限制、会话过期与注销。
- SSH 密码 / 私钥连接，主机指纹固定校验；凭据加密落盘。
- 服务器新增、编辑、删除、连接检查与中转组件安装。
- iptables / GOST，TCP / UDP / TCP+UDP，规则保存、编辑、预览、下发、停止、删除。
- iptables 支持同族 IPv4 / IPv6；GOST 支持域名目标及跨 IPv4 / IPv6 转发。
- 独立的 systemd 规则服务及开机启动；iptables 使用专属链，不清空整个防火墙。
- SQLite 持久化任务、失败记录、中断状态恢复、远程服务日志。
- 按规则累计入站 / 出站流量、24 小时图表、30 天采样记录。
- 配置清单导出、完整备份、管理员密码重置。
- 落地地址库：IP / 域名与端口、备注、拖动排序、上下移动；创建或编辑转发时快速填入。
- 服务器连接检查直接显示进度、结果、耗时和组件状态。
- 逐条转发状态检测：服务、配置、内核规则 / GOST 监听、运行指标、TCP 目标与入口连通性。

当前范围不包含多租户计费、套餐/配额、带宽整形、GOST 加密隧道链与上游数据库迁移。GOST v3 在本版用于端口转发；这些扩展可以后续按实际需要添加。

## 运行要求

主控：Debian 12+ 或 Ubuntu 22.04+，Python 3.10+、python3-venv。单个主控进程使用一个数据目录；禁止对同一目录启动多个 Uvicorn workers。

被控中转机：Debian / Ubuntu、systemd、SSH、Python 3。连接账号为 root 或具备免密 sudo 的管理账号。自动安装 GOST 支持 AMD64 和 ARM64，固定使用 v3.3.0 并校验官方 SHA-256 清单。被控机访问 GitHub 需要网络可达。

Windows 可用于开发与界面/API 测试，实际 iptables / systemd 操作运行在 Linux 中转机上。主控与被控可以在同一台 Linux 机器，通过 SSH 添加本机可达地址即可。

## 安装与启动

也可使用免安装 Python 的 Linux 可执行文件，打包、启动及替换现有服务的说明见 [BINARY.md](BINARY.md)。两种方式使用相同的 SQLite 数据格式。以下是源码运行方式。

以下命令在 **Debian / Ubuntu** 上执行，不在 Windows PowerShell 执行。

先安装系统 Python 的虚拟环境组件：

```text
sudo apt-get update
sudo apt-get install -y python3 python3-venv
```

进入解压后的项目目录：

```text
python3 install.py
python3 start.py
```

首次安装会创建 `.venv`、安装固定版本依赖，并提示设置管理员密码（至少 12 位）。没有默认管理员密码。后续只运行 `python3 start.py` 即可，不需要手动激活虚拟环境。

默认监听 `127.0.0.1:8000`。可在自己电脑上建立 SSH 隧道后访问 `http://127.0.0.1:8000`：

```text
ssh -L 8000:127.0.0.1:8000 用户名@主控服务器
```

### 开机启动

把项目放到 `/opt/aurora-lite-panel` 等服务账号可以读取的目录，进入该目录执行：

```text
sudo python3 install.py --service
sudo systemctl status aurora-lite-panel
```

安装脚本创建 `aurora-panel` 系统账号，主控使用该普通账号运行；中转机的管理权限来自你配置的 SSH 账号。不会更改你的 SSH 服务配置。

更新代码后可重跑安装脚本；它保留现有管理员和 `settings.json`，并重启服务。

```text
sudo systemctl restart aurora-lite-panel
sudo journalctl -u aurora-lite-panel -f
```

### 访问地址与 HTTPS

编辑安装生成的 `settings.json`，例如在受信任局域网直接监听：

```json
{"host":"0.0.0.0","port":8000,"public_origin":"","secure_cookie":false}
```

公网访问应使用 HTTPS 反向代理。`scripts/nginx.example.conf` 提供可选 Nginx 配置；配置证书后将设置改为：

```json
{
  "host":"127.0.0.1",
  "port":8000,
  "public_origin":"https://panel.example.com",
  "secure_cookie":true
}
```

然后重启主控。`public_origin` 必须与浏览器实际地址完全一致，不带结尾斜杠。公网开放端口和云厂商安全组由你按实际用途配置；安装脚本不会自动开放主控入站端口。

## 添加中转规则

1. 登录后添加服务器，填写 SSH 地址、端口、账号和认证信息。
2. 点击“读取指纹”，与服务器控制台的 SSH 公钥指纹核对，再保存。指纹变化时连接会被拒绝。
3. 点击“检查连接”。若成功，点击“安装中转组件”，等待任务完成。
4. 在服务器卡片点击“转发管理”，进入该服务器的规则页面。点击“添加转发”，选择 iptables 或 GOST，填写监听 IP/端口和目标地址/端口；规则自动归属当前服务器。
5. 保存后可以先查看“配置”，再点击“下发”。在“任务日志”确认结果。
6. 编辑运行中的规则前先“停止”。删除会先清理远程服务，成功后删除本地记录。

### 落地管理

在侧栏“落地管理”保存名称、IP / 域名、目标端口与备注。拖动左侧手柄调整顺序，也可使用上移 / 下移按钮，排序自动保存到 SQLite。转发编辑窗口中的“快速选择落地”按此顺序显示，选择后填入目标地址和端口。选用域名目标时应使用 GOST，iptables 仍要求固定 IP。

已有转发规则保存目标地址的独立副本；修改或删除落地条目不会自动改动已保存的规则。完整数据库备份与配置清单导出包含落地地址。

### 直接查看检查结果

服务器卡片和转发管理页的“检查连接”会直接展示检查中、成功或失败，以及时间、SSH 检查耗时、组件状态。这里显示的是检查耗时，不是网络 Ping 延迟。

每条转发规则增加“状态检测”列，点击“检测”可查看分项结果；结果保留在列表中，详情窗口会自动更新。检测只读取配置、服务和内核状态，并按需建立 TCP 连接，不会启动、停止或修复规则。

- TCP：检查中转机到目标的连接，以及主控到入口的连接；通过并不代表应用层响应或任意公网来源已验证。
- UDP：核对服务 / 规则是否就绪，业务连通性标记为待验证；UDP 没有通用握手，不能仅凭端口监听判断数据已转发成功。
- 主控与 iptables 中转机同机时，本机 TCP 连接不经过外部入站的 PREROUTING 路径，入口验证会提示从另一台设备测试。
- 目标不可达、配置不一致、规则缺失会显示异常；服务未运行会显示未启用；SSH 检查中断或失败会显示未确认。
- 编辑连接、编辑规则或执行启停后，旧的检测结果失效，需要重新检测。

v0.2.0 首次启动时自动添加落地表和检测结果字段，不清空旧节点、规则、流量或凭据。更新前建议按下文备份完整数据目录。

页面按“服务器列表 → 单台服务器 → 转发管理”组织。每台服务器的规则、筛选与流量合计独立显示；侧栏“服务器”是统一入口。总览中的最近规则可点击进入所属服务器，详情页可返回服务器列表。详情页地址包含服务器 ID，直接打开或刷新后仍停留在同一台服务器；旧的 `#rules` 地址会转到服务器列表。

监听 `0.0.0.0` 表示全部 IPv4 地址，`::` 表示 IPv6 通配地址。相同端口的 IPv4/IPv6 通配监听保守地视为冲突，避免不同系统上的双栈监听差异。规则不能占用其服务器的 SSH 端口。

iptables 使用 DNAT + MASQUERADE，后端目标看到的来源通常为中转机。GOST 也建立到目标的独立连接。iptables 的 NAT 数据路径针对外部入站流量；在中转机自身连接本机监听端口不等于从外部测试转发。GOST 监听端口需要被控机现有防火墙/云安全组允许访问，本版不会改写其他管理器的规则。

## 流量、任务与故障行为

- 默认每 60 秒轮询，可通过服务环境变量 `AURORA_POLL_SECONDS` 调整，最小 15 秒。一个主控工作线程串行管理任务，适用于个人使用及适中采样规模。
- iptables 读取专属 FORWARD 链的 IP 层字节计数；GOST 读取进程的转发数据计数，两者统计口径不同。
- 累计值保存在 `aurora.db`。利用 systemd InvocationID 判断服务重启后的新计数周期；主控重启不会清空已经入库的累计值。
- 主控离线后，如果中转服务未重启，恢复采集可补上计数差额；服务重启或清理前尚未采集的数据可能丢失。本版不是精确计费系统。
- SSH 失败显示未知/异常状态，不显示虚假的运行成功。服务器“连接正常”表示最近一次 SSH 操作成功，时间见最近检查。
- 中断时正在执行的任务标记为“已中断”，避免自动重放可能已部分执行的操作；等待中的任务仍保留。下发和清理按规则设计为可重复执行。
- 关闭主控不会关闭被控机的转发服务。主动停止/删除规则才会关闭对应转发。
- 删除规则会删除该规则的流量记录，因此总览显示的是当前保留规则的累计值。

## 数据目录及备份

```text
data/
  aurora.db      业务配置、加密凭据、会话、流量数据
  tasks.db       后台任务与日志
  master.key     SSH 凭据加密密钥（必须与数据库一起备份）
  process.lock   防止重复启动
```

SQLite 运行时可能产生 `-wal`、`-shm` 附属文件，这是正常现象。不要在运行中只复制主 `.db` 文件。

从项目目录备份：

```text
sudo systemctl stop aurora-lite-panel
sudo -u aurora-panel .venv/bin/python -m aurora backup /opt/aurora-lite-panel/data/backup-2026-09-10.zip
sudo systemctl start aurora-lite-panel
```

命令使用 SQLite 备份接口并打包密钥，不覆盖已有同名备份。备份包包含解密所需材料，应保存在你控制的位置。非 systemd 启动时先停止终端中的主控，再使用同一账号运行备份命令。

恢复：停止主控，把备份解压到**新的数据目录**，保留 `aurora.db`、`tasks.db`、`master.key` 三者对应关系；将目录所有权赋予主控账号，再通过 `AURORA_DATA_DIR` 指定该目录启动。不要覆盖正在使用的数据，也不要用新密钥替代丢失的密钥。Web 中的“导出配置清单”不包含 SSH 凭据，不能替代完整备份。

重置管理员密码（项目目录中执行）：

```text
sudo systemctl stop aurora-lite-panel
sudo -u aurora-panel .venv/bin/python -m aurora init --reset --username admin
sudo systemctl start aurora-lite-panel
```

本地检查：

```text
.venv/bin/python -m aurora doctor
```

## 文件与服务归属

被控机上本项目仅写入以下专属位置：

- `/opt/aurora-lite/agent.py`：Python 标准库管理脚本，非驻留进程。
- `/opt/aurora-lite/bin/gost`：本项目使用的 GOST 二进制。
- `/etc/aurora-lite/rules/`、`/etc/aurora-lite/gost/`：规则配置。
- `/etc/systemd/system/aurora-lite-<规则ID>.service`：每条规则的服务。
- `AL<规则ID>N/M/F`：对应 iptables 私有链。
- `/run/aurora-lite-<规则ID>/metrics.sock`：GOST 流量接口，仅 Unix socket。

iptables 启动会开启对应地址族的内核 forwarding；不会修改全局防火墙默认策略。停止时不会自动关闭全局 forwarding，以免影响服务器其他转发用途。

停用整套系统时，先在页面删除规则、确认远程清理成功，再停止主控。直接删除主控数据不会停止被控机现有规则。

## 开发与测试

```text
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

`tests/test_gost_live.py` 可选测试需要将环境变量 `AURORA_TEST_GOST` 指向已校验的 GOST v3 二进制。测试只使用本地回环临时端口，不连接你的服务器，也不修改防火墙。

后端入口 `aurora/app.py`；数据层 `storage.py`；SSH 传输 `remote.py`；被控机执行逻辑 `remote_agent.py`；后台任务 `worker.py`；界面 `aurora/static/`。前端直接修改 HTML/CSS/JavaScript 即可，无须 npm 构建。

## 参考资料

- 原项目功能背景：https://github.com/Aurora-Admin-Panel/deploy
- GOST 转发配置：https://gost.run/en/tutorials/port-forwarding/
- GOST 流量指标：https://gost.run/en/tutorials/metrics/
- iptables 扩展：https://manpages.debian.org/bookworm/iptables/iptables-extensions.8.en.html
- SQLite 使用场景：https://www.sqlite.org/whentouse.html

本目录为独立实现，不是 Aurora-Admin-Panel 官方发行版。Windows 本地验证、Debian 13 实机部署与转发验收结果见 `VALIDATION.md`。
