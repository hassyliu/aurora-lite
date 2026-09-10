# Linux 可执行文件

Aurora Lite 0.2.1 提供 Linux AMD64 单文件可执行程序 `aurora-lite`。网页、Python 运行时和应用依赖包含在程序内；主控无需另外安装 Python、pip、Docker、PostgreSQL 或 Redis。

这是 PyInstaller 打包的可执行程序，并非把整个项目改写为机器码，也不提供源码保密保证。[PyInstaller 官方说明](https://pyinstaller.org/en/stable/)。

## 兼容性

当前成品在 Debian 13 AMD64、glibc 2.41 上构建与验证。不要直接假定它兼容 Debian 12、Ubuntu 22.04 或 ARM64；这些环境应按下文在目标系统或兼容的较旧系统上重新构建。[Linux 构建兼容性说明](https://pyinstaller.org/en/stable/usage.html#making-gnu-linux-apps-forward-compatible)。

主控的 TCP 检测调用程序自身，不调用系统 Python。被控中转机仍需系统 Python 3、SSH、systemd 和 iptables/GOST；主控兼作中转机时，同一台机器也要保留系统 Python。

## 启动

以下命令在 Linux 终端执行。解压发行包后进入目录：

```sh
chmod +x aurora-lite
./aurora-lite init
./aurora-lite serve --host 0.0.0.0 --port 8000
```

`init` 交互设置管理员密码，至少 12 位。没有默认密码。运行账号需要有权创建程序旁边的 `data` 目录，也可以自行指定位置：

```sh
./aurora-lite --data-dir /your/writable/data init
./aurora-lite --data-dir /your/writable/data serve --host 0.0.0.0 --port 8000
```

不加参数等同于 `serve`。程序默认监听 `127.0.0.1:8000`；复制 `settings.example.json` 为程序旁的 `settings.json` 后，可设置监听地址、端口、对外域名和安全 Cookie。

配置优先级为命令行参数、`AURORA_*` 环境变量、`settings.json`、默认值。`--data-dir` 为全局参数，放在 `serve` / `init` 等子命令前面。默认数据目录始终在可执行文件旁，不随当前终端目录变化。

`data/aurora.db`、`data/tasks.db`、`data/master.key` 保存持久数据。升级只替换程序；完整保留这三个文件。完整备份命令需先停止主控：

```sh
./aurora-lite doctor
./aurora-lite backup /your/writable/path/backup.zip
./aurora-lite --version
```

单文件程序启动时会解包到临时目录，因此临时目录必须可写并允许加载动态库。如系统把 `/tmp` 挂为 `noexec`，应通过 `TMPDIR` 指定合适的私有临时目录。数据不保存在解包目录。[运行时路径说明](https://pyinstaller.org/en/stable/runtime-information.html)。

## systemd

推荐用普通账号 `aurora-panel` 运行主控。提供的 `aurora-lite-binary.service` 以 `/opt/aurora-lite-panel` 为程序目录，以该目录下 `data` 为可写数据目录。新安装时先创建账号和目录，将程序、配置放好，并使用此账号执行 `init`，然后安装服务文件。

```sh
sudo useradd --system --home-dir /opt/aurora-lite-panel --shell /usr/sbin/nologin aurora-panel
sudo install -d -m 755 /opt/aurora-lite-panel
sudo install -m 755 aurora-lite /opt/aurora-lite-panel/aurora-lite
sudo install -d -m 700 -o aurora-panel -g aurora-panel /opt/aurora-lite-panel/data
sudo -u aurora-panel /opt/aurora-lite-panel/aurora-lite init
sudo install -m 644 aurora-lite-binary.service /etc/systemd/system/aurora-lite-panel.service
sudo systemctl daemon-reload
sudo systemctl enable --now aurora-lite-panel
```

如果已有同名账号，跳过 `useradd`。如果希望局域网直接访问，启动前在 `/opt/aurora-lite-panel/settings.json` 设置 `host`，例如 `0.0.0.0`。

已有源码服务的机器应先停止 `aurora-lite-panel`、备份原服务文件和数据，再替换启动程序及服务文件；沿用原来的 `settings.json` 和 `data`，无需重新 `init`。不要让源码服务和二进制服务同时打开同一个数据目录。需要回退时，恢复原服务文件即可；0.2.0 和 0.2.1 数据格式相同。

## 自行构建

在目标 Linux 系统上解压源码包，安装构建依赖后执行：

```sh
sudo apt-get install -y python3 python3-venv libpython3-dev binutils
python3 scripts/build_binary.py
```

脚本创建独立 `.build-venv`，安装固定版本运行依赖和 PyInstaller，产物为 `dist/aurora-lite`。构建过程不使用 Docker。此处的 Python、venv 和 libpython 是构建工具，成品主控的运行不需要另外安装它们。

脚本同时打包静态页面和发给中转机的独立管理脚本；Uvicorn 的动态导入模块也一并收集。`BUILD-INFO.json` 记录本次成品的构建环境、依赖与校验值。
