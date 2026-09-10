# Aurora Lite 二进制部署与卸载

适用版本：**0.2.1**。编写日期：2026-09-11。

当前二进制已实测 **Debian 13 / AMD64 / glibc 2.41**。其他 Debian、Ubuntu 版本和 ARM64 需要重新构建或单独验证。

主控内置 Python 运行时和依赖，无需 Docker、pip、PostgreSQL 或 Redis。业务和任务数据使用 SQLite 文件。**被控中转机仍需要系统 Python 3、SSH 和 systemd**；主控兼作中转机时也要保留这些组件。

如果已有 v0.2.0 源码版，请执行“1. 准备安装包”后直接进入“3. 从现有源码版切换”，不要重复首次安装。

除特别标出的 PowerShell 上传命令外，本文命令均在 **Linux SSH 终端，以 root 身份**执行。普通管理账号先运行 `sudo -i`。每个括号包围的代码块需完整执行，出错会停止该块。

## 1. 准备安装包

需要这两个文件：

- `aurora-lite-0.2.1-linux-amd64-debian13.tar.gz`
- `aurora-lite-0.2.1-linux-amd64-debian13.tar.gz.sha256`

从 [v0.2.1 发行页](https://github.com/liuzipeng/aurora-lite/releases/tag/v0.2.1) 下载以上文件。

在 Windows PowerShell 中进入存放这两个文件的目录，上传到服务器。将示例地址 `203.0.113.10` 和私钥路径替换成自己的：

```powershell
scp -i "C:\path\to\your-private-key" .\aurora-lite-0.2.1-linux-amd64-debian13.tar.gz .\aurora-lite-0.2.1-linux-amd64-debian13.tar.gz.sha256 root@203.0.113.10:/root/
ssh -i "C:\path\to\your-private-key" root@203.0.113.10
```

Linux 上检查环境、校验并解压：

```bash
(
set -eu
uname -m
getconf GNU_LIBC_VERSION
cd /root
sha256sum -c aurora-lite-0.2.1-linux-amd64-debian13.tar.gz.sha256
tar -xzf aurora-lite-0.2.1-linux-amd64-debian13.tar.gz
cd /root/aurora-lite-0.2.1-linux-amd64-debian13
sha256sum -c SHA256SUMS
./aurora-lite --version
)
```

本次发行包的 SHA-256：

```text
7cb6a956419d5320cf33a2e5cfe19321f9be3131d9f344e29dd45ea65da35d98
```

版本输出应为 `Aurora Lite 0.2.1`。若出现架构错误或 `GLIBC_* not found`，先为目标系统重新构建，停止后续部署。

## 2. 首次安装到新服务器

本节只用于没有已有面板数据的机器。

```bash
(
set -eu
if [ -e /opt/aurora-lite-panel ]; then
    echo '安装目录已存在，请使用第 3 节切换或第 4 节升级。'
    exit 1
fi
cd /root/aurora-lite-0.2.1-linux-amd64-debian13
id aurora-panel >/dev/null 2>&1 || useradd --system --user-group --home-dir /opt/aurora-lite-panel --shell /usr/sbin/nologin aurora-panel
install -d -m 755 /opt/aurora-lite-panel
install -m 755 aurora-lite /opt/aurora-lite-panel/aurora-lite
install -d -m 700 -o aurora-panel -g aurora-panel /opt/aurora-lite-panel/data
cat > /opt/aurora-lite-panel/settings.json <<'JSON'
{
  "host": "0.0.0.0",
  "port": 8000,
  "public_origin": "",
  "secure_cookie": false
}
JSON
chmod 644 /opt/aurora-lite-panel/settings.json
cd /opt/aurora-lite-panel
runuser -u aurora-panel -- /opt/aurora-lite-panel/aurora-lite init
install -m 644 /root/aurora-lite-0.2.1-linux-amd64-debian13/aurora-lite-binary.service /etc/systemd/system/aurora-lite-panel.service
systemctl daemon-reload
systemctl enable --now aurora-lite-panel
systemctl status aurora-lite-panel --no-pager
)
```

初始化时交互输入管理员密码，至少 12 位；账号默认 `admin`，没有默认密码。完成后访问 `http://服务器IP:8000`。

上述配置监听所有 IPv4 网卡。若只希望内网访问，可把 `host` 改成服务器的内网 IP；若只通过本机反向代理访问，可设为 `127.0.0.1`。修改 `settings.json` 后执行 `systemctl restart aurora-lite-panel`。HTTP 配置保持 `secure_cookie: false`；通过 HTTPS 反向代理访问时再设置对应 `public_origin` 和安全 Cookie。

## 3. 从现有源码版切换

适用于安装在 `/opt/aurora-lite-panel` 的源码版。沿用现有账号、密码、服务器、规则、落地地址和 `settings.json`。v0.2.0 与 v0.2.1 数据格式相同，**不要重新初始化或删除 `data`**。

以下操作会短暂停止主控。已下发的中转由独立 systemd 服务运行，停止主控不会停止它们。先等页面中的部署、停止和删除任务执行完毕。

```bash
(
set -eu
test -x /opt/aurora-lite-panel/.venv/bin/python
test -f /opt/aurora-lite-panel/data/master.key
test -f /etc/systemd/system/aurora-lite-panel.service
test -x /root/aurora-lite-0.2.1-linux-amd64-debian13/aurora-lite
backup_dir="/var/backups/aurora-lite/$(date +%Y%m%d-%H%M%S)"
install -d -m 755 /var/backups/aurora-lite
install -d -m 700 -o aurora-panel -g aurora-panel "$backup_dir"
printf '本次备份目录：%s\n' "$backup_dir"
install -m 600 /etc/systemd/system/aurora-lite-panel.service "$backup_dir/panel.service"
install -m 600 /opt/aurora-lite-panel/settings.json "$backup_dir/settings.json"
systemctl stop aurora-lite-panel
cd /opt/aurora-lite-panel
runuser -u aurora-panel -- .venv/bin/python -m aurora backup "$backup_dir/data.zip"
install -m 755 /root/aurora-lite-0.2.1-linux-amd64-debian13/aurora-lite /opt/aurora-lite-panel/aurora-lite
install -m 644 /root/aurora-lite-0.2.1-linux-amd64-debian13/aurora-lite-binary.service /etc/systemd/system/aurora-lite-panel.service
systemctl daemon-reload
systemctl enable --now aurora-lite-panel
systemctl status aurora-lite-panel --no-pager
)
```

原源码与 `.venv` 会保留，便于回退。若备份或切换步骤报错，先处理错误；服务文件尚未替换时可用 `systemctl start aurora-lite-panel` 启动原服务，已替换时使用下方回退步骤。

### 检查切换结果

```bash
systemctl show aurora-lite-panel -p ExecStart -p User
curl --fail http://127.0.0.1:8000/api/status
journalctl -u aurora-lite-panel -n 50 --no-pager
```

如果设置只监听 `203.0.113.10`，将上述 curl 地址改成 `http://203.0.113.10:8000/api/status`。`ExecStart` 应指向 `aurora-lite` 可执行文件，API 版本应为 `0.2.1`。

浏览器登录后，进入“服务器 → 检查连接”，再进入该服务器的“转发管理”执行条目“状态检测”。UDP 与同机 iptables 入口显示“待验证”时，需从另一台设备用实际业务检查。

### 回退到原源码服务

将下面的备份目录改为切换时输出的真实路径。此操作只恢复启动方式，保留切换后产生的数据库记录。

```bash
(
set -eu
backup_dir='/var/backups/aurora-lite/替换为备份时间目录'
test -f "$backup_dir/panel.service"
test -x /opt/aurora-lite-panel/.venv/bin/python
systemctl stop aurora-lite-panel
install -m 644 "$backup_dir/panel.service" /etc/systemd/system/aurora-lite-panel.service
systemctl daemon-reload
systemctl start aurora-lite-panel
systemctl status aurora-lite-panel --no-pager
)
```

## 4. 日常维护、备份与升级

| 内容 | 位置 |
|---|---|
| 主控程序 | `/opt/aurora-lite-panel/aurora-lite` |
| 配置 | `/opt/aurora-lite-panel/settings.json` |
| 业务数据库 | `/opt/aurora-lite-panel/data/aurora.db` |
| 任务数据库 | `/opt/aurora-lite-panel/data/tasks.db` |
| 凭据解密密钥 | `/opt/aurora-lite-panel/data/master.key` |
| 主控服务 | `aurora-lite-panel.service` |
| 本文创建的备份 | `/var/backups/aurora-lite/<时间>/` |

常用命令：

```bash
systemctl status aurora-lite-panel --no-pager
systemctl restart aurora-lite-panel
journalctl -u aurora-lite-panel -n 100 --no-pager
runuser -u aurora-panel -- /opt/aurora-lite-panel/aurora-lite doctor
```

### 完整备份：卸载前也执行一次

下方兼容已部署的二进制版和原源码版。备份保存在程序目录之外，删除程序目录后仍能保留。

```bash
(
set -eu
backup_dir="/var/backups/aurora-lite/$(date +%Y%m%d-%H%M%S)"
install -d -m 755 /var/backups/aurora-lite
install -d -m 700 -o aurora-panel -g aurora-panel "$backup_dir"
printf '本次备份目录：%s\n' "$backup_dir"
install -m 600 /opt/aurora-lite-panel/settings.json "$backup_dir/settings.json"
install -m 600 /etc/systemd/system/aurora-lite-panel.service "$backup_dir/panel.service"
systemctl stop aurora-lite-panel
cd /opt/aurora-lite-panel
if [ -x ./aurora-lite ]; then
    runuser -u aurora-panel -- ./aurora-lite backup "$backup_dir/data.zip"
else
    runuser -u aurora-panel -- .venv/bin/python -m aurora backup "$backup_dir/data.zip"
fi
systemctl start aurora-lite-panel
)
```

`data.zip` 包含两个数据库和 `master.key`，设置与服务文件另行保存。网页中的“导出配置清单”不包含 SSH 凭据，不能代替这个完整备份。可将备份目录另外复制到自己的电脑保存。

恢复数据时，先停止主控，将 ZIP 解压到**新的空目录**，恢复对应的三个文件，再替换原 `data` 目录；旧目录整体移到备份位置，不把备份覆盖到带有旧 `-wal`、`-shm` 文件的目录。将新目录所有者设为 `aurora-panel:aurora-panel`、目录权限设为 `700`、三个文件权限设为 `600`，运行 `doctor` 后启动服务。

后续升级二进制时：先按本节备份并另存旧程序，停止服务，将已校验的新程序以 `755` 权限替换 `/opt/aurora-lite-panel/aurora-lite`，然后启动服务。沿用原来的 `settings.json` 和 `data`；不要在程序仍运行时覆盖可执行文件。

## 5. 卸载

先选择要达到的结果：

| 目的 | 执行顺序 |
|---|---|
| 卸载主控，保留数据和现有中转 | 第 4 节备份 → 5.2 |
| 删除主控及其数据，继续保留现有中转 | 第 4 节备份 → 5.2 → 5.3；以后需自行管理这些中转 |
| 卸载整套系统并停止所有中转 | 第 4 节备份 → 5.1 → 5.2 → 5.3 |

### 5.1 清理全部中转：在卸载主控之前做

1. 在面板中逐台进入“服务器 → 转发管理”，**删除**该节点的全部规则。
2. 等待删除任务成功；删除会停止对应服务，清理本项目的内核规则和配置。仅“停止”会保留规则文件。
3. 对每台中转机执行下面的组件清理，包括兼作中转机的主控服务器。

下面的命令仅在确认该机器的规则已全部删除后执行。它会检查残留的规则配置、服务文件和内核链，发现残留便停止，不清空系统防火墙。

```bash
(
set -eu
for folder in /opt/aurora-lite /etc/aurora-lite; do
    if [ -e "$folder" ] || [ -L "$folder" ]; then
        test "$(readlink -f -- "$folder")" = "$folder"
    fi
done
if [ -d /etc/aurora-lite/rules ] && [ -n "$(find /etc/aurora-lite/rules -maxdepth 1 -type f -name '*.json' -print -quit)" ]; then
    echo '仍有转发规则配置，请先在面板删除并确认任务成功。'
    exit 1
fi
unit_files=$(find /etc/systemd/system -maxdepth 1 -name 'aurora-lite-*.service' -printf '%f\n')
if printf '%s\n' "$unit_files" | grep -Eq '^aurora-lite-[0-9a-f]{12}\.service$'; then
    echo '仍有本项目的转发服务文件，请先完成规则清理。'
    exit 1
fi
running_units=$(systemctl list-units --type=service --state=active,activating,reloading,deactivating --plain --no-legend --no-pager)
if printf '%s\n' "$running_units" | grep -Eq '^aurora-lite-[0-9a-f]{12}\.service[[:space:]]'; then
    echo '仍有本项目的转发服务运行或正在停止，请先完成规则清理。'
    exit 1
fi
for tool in iptables ip6tables; do
    if command -v "$tool" >/dev/null 2>&1; then
        for table in nat filter; do
            rules=$("$tool" -w 2 -t "$table" -S)
            if printf '%s\n' "$rules" | grep -Eq 'AL[0-9a-f]{12}[NMF]'; then
                echo '仍有本项目的内核链或引用，请先完成规则清理。'
                exit 1
            fi
        done
    fi
done
rm -rf -- /opt/aurora-lite /etc/aurora-lite
systemctl daemon-reload
)
```

如果面板已经不可用，应先恢复主控及数据，再删除规则；不要先删远程 `agent.py` 和 JSON 配置，它们用于准确清理 iptables 链与连接跟踪。

这一步保留系统 Python、OpenSSH、iptables 和 conntrack 软件包，它们可能供其他程序使用。全局 IPv4/IPv6 forwarding 不自动关闭；只有确认该机器已无其他路由、VPN 或转发用途时，再按自己的系统配置恢复原值。

### 5.2 卸载主控服务，保留数据

在主控服务器执行。完成后面板停止、开机不再启动；`data`、`settings.json` 及原源码仍保留，二进制文件移除。

```bash
(
set -eu
systemctl disable --now aurora-lite-panel
rm -f -- /etc/systemd/system/aurora-lite-panel.service
systemctl daemon-reload
systemctl reset-failed aurora-lite-panel.service 2>/dev/null || true
test "$(readlink -f -- /opt/aurora-lite-panel)" = /opt/aurora-lite-panel
rm -f -- /opt/aurora-lite-panel/aurora-lite
)
```

重新启用时，重新安装二进制和服务文件，再执行 `systemctl daemon-reload`、`systemctl enable --now aurora-lite-panel`。已有数据无需再次 `init`。

### 5.3 删除主控程序和全部本地数据

**此步骤会删除管理员、服务器凭据、规则记录、落地地址、流量记录、项目内备份、源码和虚拟环境。** 请先确认第 4 节的外部备份已保存，并完成 5.2。该操作不会替你停止其他中转机上的规则。

```bash
(
set -eu
if systemctl is-active --quiet aurora-lite-panel; then
    echo '主控仍在运行，请先执行 5.2。'
    exit 1
fi
test ! -e /etc/systemd/system/aurora-lite-panel.service
test "$(readlink -f -- /opt/aurora-lite-panel)" = /opt/aurora-lite-panel
rm -rf -- /opt/aurora-lite-panel
if id aurora-panel >/dev/null 2>&1; then
    userdel aurora-panel
fi
)
```

`/var/backups/aurora-lite/` 中的外部备份会保留。此前下载的发行包和 `/opt/aurora-lite-build-...` 构建目录也不属于运行服务，可按需另行删除。若自己添加过该服务的 systemd drop-in，卸载时也应清理对应 `/etc/systemd/system/aurora-lite-panel.service.d/`，避免日后重装继承旧配置。

如果曾为本机节点添加注释为 `aurora-lite-local` 的专用 SSH 公钥，也应在彻底卸载时清理。整套系统卸载后，可先备份 `/root/.ssh/authorized_keys`，再编辑该文件，删除行尾注释为 **`aurora-lite-local`** 的那一整行；保留你原来的 root 登录公钥。仅卸载主控并准备恢复使用时，保留这条专用公钥。

## 6. 常见问题

| 现象 | 处理 |
|---|---|
| `GLIBC_* not found` / `Exec format error` | 包与系统或架构不匹配，在目标 Linux 上重新构建 |
| 无法加载临时目录中的共享库 | 检查 `/tmp` 是否 `noexec`；为服务配置可写且允许加载库的专用 `TMPDIR`，并在服务沙箱中允许写入该目录 |
| 8000 端口访问不到 | 查看 `systemctl status` 和日志，确认 `settings.json` 的监听 IP、端口以及已有防火墙/安全组放行情况 |
| 忘记密码 | 停止主控，以 `aurora-panel` 运行 `aurora-lite init --reset --username admin`，再启动服务 |
| `master.key` 丢失 | 从同一份完整备份恢复数据库和密钥，不能新建密钥替代 |
| 卸载主控后端口仍能转发 | 中转服务独立运行；按 5.1 逐节点删除规则 |

本文部署路径、配置、备份接口和规则清理顺序已按当前实现核对。本文生成过程没有执行部署、停服或卸载操作。
