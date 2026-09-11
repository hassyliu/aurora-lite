# Aurora Lite v0.3.1 升级

本版修复服务器名称、备注修改被错误限制的问题。升级后可直接修改名称和备注，不必停止转发；修改真正的 SSH 连接信息仍需先停止规则。

适用环境：Debian 13 AMD64，标准二进制部署 `/opt/aurora-lite-panel`，服务 `aurora-lite-panel.service`、账号 `aurora-panel`。支持从 v0.2.1、v0.3.0 升级。

先等待面板中已提交的中转操作结束，在 VPS 的 root 终端执行：

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/hassyliu/aurora-lite/main/scripts/upgrade.sh -o /root/upgrade-aurora-lite.sh && bash /root/upgrade-aurora-lite.sh
```

脚本校验安装包，自动备份程序和数据，替换后检查服务与本机 API。升级失败时恢复原程序和升级前数据，并另存失败启动期间的数据以便排查。密码、SSH 凭据、服务器、规则、流量、设置和 Nginx 配置保留。

后台端口不是 8000 时，在下载脚本后指定实际本机接口，例如：

```bash
AURORA_HEALTH_URL=http://127.0.0.1:9000/api/status bash /root/upgrade-aurora-lite.sh
```

看到 `升级成功：v0.3.1` 后，用原域名访问，按 Ctrl+F5 刷新；在“设置”确认版本 0.3.1。

手动升级可从 Release 下载 `aurora-lite-0.3.1-linux-amd64-debian13.tar.gz` 及 `.sha256`，按 v0.3.0 升级说明中的备份与替换流程操作。新版本沿用现有数据结构，不需要重新初始化管理员。
