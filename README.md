# 自动送球

洛克王国：世界的 Windows 自动赠送咕噜球工具。通过画面识别确认状态，使用 Interception 发送键鼠输入。当前源码版本：`2026.09.12-gift8-portable`。

## 功能

- 设置赠送次数，`0` 表示不限次数；球种和每轮数量沿用游戏当前选择。
- 自动打开动作页，全画面查找“赠送条件球”文字，再识别文字附近的图标和下方 F1–F10 快捷键。
- 赠送图标为橙色时无限等待，确认恢复普通色及准备2界面后才计入完成次数。
- 窗口可自由移动、收起、暂停、继续和停止。运行中为绿色，中断后变为橙色并显示原因。
- 收藏动作页只识别 Esc 与选中的收藏分类。模板相似度门槛统一为 **大于 0.65**，同时保留颜色、亮度和连续帧确认规则。

完整的状态、转移条件和完成判定见 [STATE_MACHINE.md](STATE_MACHINE.md)。

## 从源码构建可执行文件

### 1. 准备构建环境

在 **Windows 10/11 x64** 上安装 **64 位 Python 3.13**，保留安装选项中的 Tcl/Tk（`tkinter`）及 pip。以下命令在仓库根目录的 PowerShell 中运行。

```powershell
git clone git@github.com:suyiiis/rock-auto-send-guluqiu.git
cd rock-auto-send-guluqiu
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

不需要激活虚拟环境。未安装 `py` 启动器时，将 `py -3.13` 替换为本机 Python 3.13 的完整路径。

依赖版本固定在 [requirements.txt](requirements.txt) 和 [requirements-build.txt](requirements-build.txt)。当前构建基线为 Python 3.13.2、NumPy 2.5.3、OpenCV Headless 4.14.0.94、MSS 10.2.0 和 PyInstaller 6.22.2。构建需要在 Windows 上进行。

### 2. 一条命令构建

```powershell
.\.venv\Scripts\python.exe -X utf8 release\build.py
```

构建脚本会：

1. 从 [Interception 官方 v1.0.1 发布页](https://github.com/oblitum/Interception/releases/tag/v1.0.1) 下载构建所需文件，验证压缩包及每个文件的 SHA-256；验证通过的本地文件可重复使用。
2. 生成默认配置，收集运行时、识别模板、原生组件及许可证。
3. 使用 [PyInstaller 的目录打包模式](https://pyinstaller.org/en/stable/usage.html) 生成可直接运行的程序，不生成软件安装器或压缩包。

构建只准备文件，**不会安装驱动**，也不会操作游戏。

输出目录：

```text
dist/
└─ 自动送球/
   ├─ 自动送球.exe
   ├─ _internal/       # 随程序携带的运行库与资源
   ├─ 使用说明.txt
   └─ data/            # 首次运行时生成：配置和日志
```

分发时复制 **整个 `dist/自动送球` 文件夹**，不能只复制 EXE。用户无需另装 Python。软件本身免安装；新电脑缺少 Interception 驱动时，首次打开会提示初始化，Windows 可能要求管理员授权和重启。

重新构建会重建 `dist/自动送球`。如果已经运行过该目录中的程序，请先退出，并将需要保留的 `data` 文件夹移到其他位置备份。

### 下载受限时

从上面的官方发布页手动下载 `Interception.zip`，然后执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 release\prepare_vendor.py --archive "C:\Downloads\Interception.zip"
.\.venv\Scripts\python.exe -X utf8 release\build.py
```

手动下载的文件同样必须通过版本和哈希校验。文件不匹配时构建会停止，校验清单位于 [release/interception-source.json](release/interception-source.json)。

## 从源码运行

完成依赖安装后，先准备原生文件，再启动应用：

```powershell
.\.venv\Scripts\python.exe -X utf8 release\prepare_vendor.py
$env:ROCK_GIFT_DATA_DIR = Join-Path $PWD "data"
.\.venv\Scripts\python.exe -X utf8 internal\app.py
```

设置 `ROCK_GIFT_DATA_DIR` 会把运行配置和日志放到忽略上传的 `data/`，避免修改仓库的默认 `config.json`。程序首次打开会检查运行组件；点击“开始赠送”后切回游戏并保持前台，倒计时结束后运行。暂停、继续和停止使用应用窗口按钮。

可选的只读自检（不发送游戏输入、不安装驱动）：

```powershell
.\.venv\Scripts\python.exe -X utf8 internal\app.py --probe "$PWD\data\health.json"
```

自检结果写入 JSON；只有运行组件和键鼠驱动均就绪时退出码为 `0`。缺少驱动不代表编译失败。

## 源码结构

| 路径 | 用途 |
| --- | --- |
| `internal/app.py` | 应用入口，以及执行、自检等子进程入口 |
| `internal/sidebar.py`、`first_run.py`、`panel_*.py` | 界面、启动检查和运行状态 |
| `internal/bot.py`、`controller.py` | 执行循环和状态机 |
| `internal/vision.py` | 模板匹配、动态快捷键定位与颜色识别 |
| `internal/interception_io.py`、`windows_io.py` 等 | 输入、窗口、进程监督和停止控制 |
| `assets/` | 必需的局部识别模板、模板元数据及应用图标 |
| `config.json` | 默认配置，设备编号为空时自动选择 |
| `release/build.py`、`RockGift.spec` | Windows 可执行文件构建 |
| `release/prepare_vendor.py`、`interception-source.json` | 第三方原生组件的下载与校验 |

仓库只保存源码、必要识别资产、依赖清单和构建说明。虚拟环境、第三方库/驱动二进制、构建产物、运行数据、日志与完整游戏截图均不提交。第三方组件在本机构建时获取，其许可证随产物保留在 `_internal/licenses` 和 `_internal/vendor/interception` 中；相关组件及游戏图像资产的权利归各自权利人。
