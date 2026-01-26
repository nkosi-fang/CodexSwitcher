[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$ErrorActionPreference = "Stop"

Write-Host "[1/2] 安装依赖（已安装可跳过）"
Write-Host "py -m pip install pyinstaller requests"

py -m pip install -U pyinstaller requests PySide6 qt-material pillow

Write-Host "[1.5/2] 生成 ico"
@'
from pathlib import Path
try:
    from PIL import Image
except Exception as exc:
    raise SystemExit(f"Pillow 不可用: {exc}")
src = Path("icon_app.png")
dst = Path("icon_app.ico")
if src.exists():
    img = Image.open(src)
    img.save(dst, sizes=[(256,256), (128,128), (64,64), (48,48), (32,32), (16,16)])
'@ | python -

Write-Host "[2/2] 打包"
py -m PyInstaller --clean --noconfirm codex_switcher.spec
if ($LASTEXITCODE -ne 0) {
  Write-Host "打包失败，退出码：$LASTEXITCODE"
  exit $LASTEXITCODE
}

Write-Host "完成：dist/CodexSwitcher.exe"
