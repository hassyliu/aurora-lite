#!/usr/bin/env bash
# Debian 13 AMD64: upgrade the standard v0.2.1 binary deployment to v0.3.0.
# Run as root: bash /root/upgrade-aurora-lite.sh
set -Eeuo pipefail
umask 077

PANEL=/opt/aurora-lite-panel
SERVICE=aurora-lite-panel.service
BACKUP_ROOT=/var/backups/aurora-lite
VERSION=0.3.0
PACKAGE=aurora-lite-0.3.0-linux-amd64-debian13
BASE_URL=https://github.com/hassyliu/aurora-lite/releases/download/v0.3.0
SHA256=65ba0fbe44a531163adee77456607b775f9f66fae1a448839ccdd875979cbf31
HEALTH_URL=${AURORA_HEALTH_URL:-http://127.0.0.1:8000/api/status}
work_dir=''
backup_dir=''
staged_binary=''
service_touched=0
replace_attempted=0
data_backed_up=0
finished=0

say() { printf '\n[极光升级] %s\n' "$*"; }
die() { printf '\n[极光升级] 错误：%s\n' "$*" >&2; exit 1; }

health_matches() {
    local response expected=$1
    response=$(curl --noproxy '*' -fsS --connect-timeout 2 --max-time 3 "$HEALTH_URL" 2>/dev/null) || return 1
    printf '%s' "$response" | grep -Eq '"version"[[:space:]]*:[[:space:]]*"'"${expected//./\.}"'"' &&
        printf '%s' "$response" | grep -Eq '"initialized"[[:space:]]*:[[:space:]]*true'
}

check_environment() {
    [[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || die '只支持 Linux AMD64 / x86_64。'
    [[ $EUID -eq 0 ]] || die '请先执行 sudo -i，再运行本脚本。'
    # /etc/os-release is OS-owned configuration, not downloaded shell input.
    (. /etc/os-release; [[ $ID == debian && $VERSION_ID == 13 ]]) || die '此安装包只用于 Debian 13。'
    local command
    for command in curl tar sha256sum systemctl runuser install mktemp flock readlink grep cp mv; do
        command -v "$command" >/dev/null || die "缺少命令 $command，请先安装对应系统工具。"
    done
    [[ -d $PANEL && ! -L $PANEL && $(readlink -f "$PANEL") == "$PANEL" ]] || die "安装目录必须是 $PANEL，且不能是符号链接。"
    [[ -x $PANEL/aurora-lite && ! -L $PANEL/aurora-lite ]] || die '没有找到现有二进制程序。'
    [[ -f $PANEL/settings.json && -d $PANEL/data && ! -L $PANEL/data ]] || die '缺少配置或数据目录，或数据目录是符号链接。'
    local file
    for file in aurora.db tasks.db master.key; do
        [[ -f $PANEL/data/$file && ! -L $PANEL/data/$file ]] || die "缺少数据文件或发现符号链接：$file"
    done
    [[ $(systemctl show "$SERVICE" -p User --value) == aurora-panel ]] || die '服务运行账号不是 aurora-panel，请按自定义部署方式升级。'
    local start
    start=$(systemctl show "$SERVICE" -p ExecStart --value)
    [[ $start == *"argv[]=$PANEL/aurora-lite --data-dir $PANEL/data serve"* ]] || die '服务启动路径或数据目录与标准二进制部署不同。'
    systemctl is-active --quiet "$SERVICE" || die '现有面板未运行，请先检查并启动原服务。'
}

rollback() {
    say '升级未完成，正在恢复原服务…'
    if (( replace_attempted )); then
        (( data_backed_up )) || return 1
        systemctl stop "$SERVICE" || return 1
        systemctl is-active --quiet "$SERVICE" && return 1
        local restore_dir failed_data old_binary
        restore_dir=$(mktemp -d "$PANEL/.restore.XXXXXX") || return 1
        tar -xzf "$backup_dir/data.tar.gz" -C "$restore_dir" --same-owner || return 1
        [[ -d $restore_dir/data ]] || return 1
        # Keep data produced during the failed start for investigation; never erase it.
        failed_data="$PANEL/data.failed-${backup_dir##*/}"
        [[ ! -e $failed_data ]] || return 1
        mv -T -- "$PANEL/data" "$failed_data" || return 1
        if ! mv -T -- "$restore_dir/data" "$PANEL/data"; then
            mv -T -- "$failed_data" "$PANEL/data" || true
            return 1
        fi
        rmdir "$restore_dir" || true
        old_binary=$(mktemp "$PANEL/.aurora-lite.rollback.XXXXXX") || return 1
        cp --preserve=mode,ownership -- "$backup_dir/aurora-lite" "$old_binary" || return 1
        mv -fT -- "$old_binary" "$PANEL/aurora-lite" || return 1
        say "失败启动期间的数据保留在 $failed_data"
    fi
    systemctl start "$SERVICE" || return 1
    local _
    for _ in {1..20}; do
        if systemctl is-active --quiet "$SERVICE" && health_matches "$old_version"; then
            say "已恢复原版本 $old_version，原数据和配置已保留。"
            return 0
        fi
        sleep 1
    done
    return 1
}

on_exit() {
    local status=$?
    trap - EXIT INT TERM HUP
    set +e
    if (( ! finished && service_touched )); then
        if ! rollback; then
            printf '\n自动恢复未通过检查，请保留文件并查看：journalctl -u %s -n 80 --no-pager\n备份位置：%s\n' "$SERVICE" "$backup_dir" >&2
        fi
        status=1
    fi
    [[ -z $staged_binary || ! -f $staged_binary ]] || rm -f -- "$staged_binary"
    if [[ -n $work_dir && $work_dir == /tmp/aurora-lite-upgrade.* && -d $work_dir && ! -L $work_dir ]]; then
        rm -rf -- "$work_dir"
    fi
    exit "$status"
}

download_and_check() {
    work_dir=$(mktemp -d /tmp/aurora-lite-upgrade.XXXXXX)
    say "下载并校验 v$VERSION…"
    curl --proto '=https' --proto-redir '=https' -fL --retry 3 --connect-timeout 15 --max-time 300 \
        "$BASE_URL/$PACKAGE.tar.gz" -o "$work_dir/$PACKAGE.tar.gz"
    (cd "$work_dir"; printf '%s  %s\n' "$SHA256" "$PACKAGE.tar.gz" | sha256sum -c -)
    tar -xzf "$work_dir/$PACKAGE.tar.gz" -C "$work_dir" --no-same-owner
    (cd "$work_dir/$PACKAGE"; sha256sum -c SHA256SUMS >/dev/null)
    staged_binary=$(mktemp "$PANEL/.aurora-lite.upgrade.XXXXXX")
    install -m 755 "$work_dir/$PACKAGE/aurora-lite" "$staged_binary"
    [[ $("$staged_binary" --version) == "Aurora Lite $VERSION" ]] || die '下载的程序版本不正确或无法运行。'
}

upgrade() {
    mkdir -p "$BACKUP_ROOT"
    backup_dir=$(mktemp -d "$BACKUP_ROOT/before-v$VERSION-$(date +%Y%m%d-%H%M%S).XXXXXX")
    cp -a -- "$PANEL/aurora-lite" "$backup_dir/aurora-lite"
    cp -a -- "$PANEL/settings.json" "$backup_dir/settings.json"
    systemctl cat "$SERVICE" > "$backup_dir/service.txt"
    say "停止主控并备份。已下发的中转服务继续运行。备份：$backup_dir"
    service_touched=1
    systemctl stop "$SERVICE"
    systemctl is-active --quiet "$SERVICE" && die '主控仍在运行，已停止升级。'
    [[ $(systemctl show "$SERVICE" -p Result --value) == success ]] || die '主控停止超时或异常，请检查任务日志。'
    tar -czf "$backup_dir/data.tar.gz" -C "$PANEL" data
    tar -tzf "$backup_dir/data.tar.gz" >/dev/null
    data_backed_up=1
    say '备份完成，替换程序。'
    replace_attempted=1
    mv -fT -- "$staged_binary" "$PANEL/aurora-lite"
    staged_binary=''
    # doctor opens the database and performs the additive schema migration.
    runuser -u aurora-panel -- "$PANEL/aurora-lite" --data-dir "$PANEL/data" doctor
    systemctl start "$SERVICE"
    local _ good=0
    for _ in {1..30}; do
        if systemctl is-active --quiet "$SERVICE" && health_matches "$VERSION"; then
            good=$((good+1))
            if (( good >= 3 )); then
                finished=1
                say "升级成功：v$VERSION。密码、服务器、规则、数据和 Nginx 配置均保留。"
                say "备份目录：$backup_dir"
                say '请使用原域名登录，按 Ctrl+F5 刷新页面。'
                return 0
            fi
        else
            good=0
        fi
        sleep 1
    done
    die '新版本启动检查未通过，将自动回退。'
}

main() {
    [[ $# -eq 0 ]] || die '无需参数；自定义端口可设置 AURORA_HEALTH_URL。'
    check_environment
    mkdir -p /run/lock
    exec 9>/run/lock/aurora-lite-upgrade.lock
    flock -n 9 || die '另一个升级脚本正在运行。'
    trap on_exit EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    trap 'exit 129' HUP
    local version_line
    version_line=$("$PANEL/aurora-lite" --version)
    old_version=${version_line#Aurora Lite }
    health_matches "$old_version" || die "无法验证现有面板的本机接口：$HEALTH_URL。非 8000 端口请设置 AURORA_HEALTH_URL。"
    if [[ $old_version == "$VERSION" ]]; then
        finished=1
        say "当前已是 v$VERSION，服务正常，无需重复升级。"
        return 0
    fi
    [[ $old_version == 0.2.1 ]] || die "当前版本为 $old_version，本脚本仅用于 v0.2.1 → v0.3.0。"
    say "准备升级 $old_version → $VERSION，请等待已提交的面板任务结束。"
    download_and_check
    upgrade
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
