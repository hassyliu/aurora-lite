# Aurora Lite 0.3.0 更新与升级

本版发布流程在 GitHub Actions 中构建 Debian 13 AMD64 二进制包，并在测试通过后上传 Release。现有 v0.2.1 二进制包含旧版网页与后端，仅修改旁边的源码或刷新浏览器不会升级程序，需要替换可执行文件。

## 本次变更

- 服务器使用紧凑的单行列表，支持名称 / IP / 备注搜索、自定义拖动与上下移动排序，以及名称、地址、规则数量排序。自定义顺序保存到数据库，窄屏可横向滚动。
- 新建规则保存后自动排队下发。运行中的规则可直接编辑，保存后自动清理旧配置并下发新配置；该条转发会短暂中断。手动“终止”的规则编辑后保持停止，可点击“启用”恢复。
- 下发、终止、连接检查、组件安装均可在弹窗内查看执行进度，任务日志每秒刷新，完成后停止刷新。服务日志每 3 秒读取一次最近 80 条记录，关闭弹窗后停止新请求。状态检测详情随任务更新；查看日志不再反复创建后台任务。
- 编辑规则时回显上次选择的落地。手动改目标时解除选择，删除落地保留规则中的目标地址。老规则仅在目标地址和端口唯一匹配某个落地时自动补全关联。
- 连接检查增加 conntrack 安装状态。

同一台中转机有任务执行时，需等任务结束再编辑或启停。下发失败会保留错误与执行日志，可修复原因后重新下发；不会无限自动重试失败的操作。

数据库升级仅新增字段，保留管理员、服务器、规则、流量、密钥及手动停止状态。原有规则不会在升级时自动启用。

## 直接下载二进制包（推荐）

以下命令在 Debian 13 AMD64 VPS 的 Linux SSH 终端中以 root 执行：

```bash
(
set -eu
cd /root
base='https://github.com/hassyliu/aurora-lite/releases/download/v0.3.0'
pkg='aurora-lite-0.3.0-linux-amd64-debian13'
curl -fL --retry 3 "$base/$pkg.tar.gz" -o "$pkg.tar.gz"
curl -fL --retry 3 "$base/$pkg.tar.gz.sha256" -o "$pkg.tar.gz.sha256"
sha256sum -c "$pkg.tar.gz.sha256"
tar -xzf "$pkg.tar.gz"
cd "$pkg"
sha256sum -c SHA256SUMS
./aurora-lite --version
)
```

输出 `Aurora Lite 0.3.0` 后跳到下方“备份并替换程序”。二进制路径使用 `/root/aurora-lite-0.3.0-linux-amd64-debian13/aurora-lite`。

## 可选：将源码包上传到 VPS 自行构建

在 Windows PowerShell 中执行，替换 SSH 地址 / 端口 / 用户；示例使用 root 和默认 22 端口：

```powershell
scp .\dist\aurora-lite-0.3.0-source.tar.gz .\dist\aurora-lite-0.3.0-source.tar.gz.sha256 root@你的VPS公网IP:/root/
```

下面所有命令在 VPS 的 Linux SSH 终端中以 root 执行。沿用现有 `/opt/aurora-lite-panel` 二进制部署路径。

## 先构建，现有面板继续运行

```bash
(
set -eu
cd /root
sha256sum -c aurora-lite-0.3.0-source.tar.gz.sha256
test ! -e /root/aurora-lite-0.3.0-source
tar -xzf aurora-lite-0.3.0-source.tar.gz
apt-get update
apt-get install -y python3 python3-venv libpython3-dev binutils
cd /root/aurora-lite-0.3.0-source
python3 scripts/build_binary.py
./dist/aurora-lite --version
)
```

最后应显示 `Aurora Lite 0.3.0`。构建失败时先处理错误，不继续替换。已解压过时可直接进入源码目录重新执行构建命令。

## 备份并替换程序

先等面板中所有下发、终止、删除、组件安装任务完成，再执行：

```bash
(
set -eu
panel=/opt/aurora-lite-panel
binary=/root/aurora-lite-0.3.0-linux-amd64-debian13/aurora-lite
# 自行构建时，将上一行改为 /root/aurora-lite-0.3.0-source/dist/aurora-lite
test -x "$binary"
test -x "$panel/aurora-lite"
test -f "$panel/data/master.key"
test "$("$binary" --version)" = 'Aurora Lite 0.3.0'
backup_dir="/var/backups/aurora-lite/$(date +%Y%m%d-%H%M%S)-before-0.3.0"
install -d -m 755 /var/backups/aurora-lite
install -d -m 700 -o aurora-panel -g aurora-panel "$backup_dir"
install -m 700 "$panel/aurora-lite" "$backup_dir/aurora-lite"
install -m 600 "$panel/settings.json" "$backup_dir/settings.json"
install -m 600 /etc/systemd/system/aurora-lite-panel.service "$backup_dir/panel.service"
printf '备份目录：%s\n' "$backup_dir"
systemctl stop aurora-lite-panel
trap 'systemctl start aurora-lite-panel' EXIT
runuser -u aurora-panel -- "$panel/aurora-lite" --data-dir "$panel/data" backup "$backup_dir/data.zip"
install -m 755 "$binary" "$panel/aurora-lite.next"
mv -f "$panel/aurora-lite.next" "$panel/aurora-lite"
runuser -u aurora-panel -- "$panel/aurora-lite" --data-dir "$panel/data" doctor
systemctl start aurora-lite-panel
trap - EXIT
systemctl status aurora-lite-panel --no-pager
)
```

保留现有 `settings.json`、数据目录及 systemd 配置，包括之前为 Nginx 配置的 `public_origin` 与 `secure_cookie`。不重新执行 `init`。

通过原域名登录并按 Ctrl+F5 刷新，在“设置”确认版本 0.3.0。服务器排序、编辑时落地回显和新建规则自动执行均使用新接口，页面与主控程序需要一起更新。

如果启动失败，查看 `journalctl -u aurora-lite-panel -n 100 --no-pager`。如需恢复旧程序，停止面板，将上面备份目录中的 `aurora-lite` 安装回 `/opt/aurora-lite-panel/aurora-lite`（权限 755）后启动；本版新增数据库字段可由旧版保留。若需要完整恢复升级前数据，按 DEPLOY.md 的新目录恢复流程使用配套的 `data.zip`，不要覆盖运行中的数据库。

## 验证范围

本地 Windows 验证涵盖 API 生命周期、旧规则清理顺序、跨转发方式修改、失败状态、旧库迁移、排序、落地关联、SSH 传输、日志实时输出与脱敏；浏览器使用模拟中转执行器检查列表、搜索、排序、自动下发、直接编辑、停止状态和弹窗刷新。

GitHub Actions 构建流程另外验证 Linux GOST TCP / UDP 回环转发，以及打包后二进制的初始化、登录、API、静态资源、备份和 doctor。systemd / iptables 及公网实际转发仍需在 VPS 上验证；Windows 本地不构建或运行 Linux 二进制。
