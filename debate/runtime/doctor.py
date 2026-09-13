from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.json"

print("=== Dual AI v5.1 Doctor ===")
config = json.loads(CONFIG.read_text(encoding="utf-8"))
print("默认模式:", config.get("provider", "ccswitch"))
if config.get("provider") == "ccswitch":
    print("提示: CCSwitch 模式下优先使用 CCSwitch 当前激活模型，不强制传入 --model。")
print("项目:", ROOT)

claude = shutil.which("claude")
print("Claude Code:", claude or "NOT FOUND")
if claude:
    try:
        r = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=20)
        print("Claude 版本:", (r.stdout or r.stderr).strip())
    except Exception as e:
        print("Claude 版本检查失败:", e)

lm_url = config.get("local", {}).get("lmstudio_base_url", "http://127.0.0.1:1234")
print("LM Studio URL:", lm_url)
try:
    with urllib.request.urlopen(lm_url.rstrip("/") + "/v1/models", timeout=5) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    ids = [x.get("id") for x in payload.get("data", []) if x.get("id")]
    print("LM Studio 状态: OK")
    print("本地模型:", ", ".join(ids[:10]) if ids else "无模型")
except Exception as e:
    print("LM Studio 状态: 未连接（离线 Mock 不受影响）")

print("ANTHROPIC_BASE_URL:", os.environ.get("ANTHROPIC_BASE_URL", "<unset>"))
print("ANTHROPIC_MODEL:", os.environ.get("ANTHROPIC_MODEL", "<unset>"))
print("ANTHROPIC_AUTH_TOKEN:", "<set>" if os.environ.get("ANTHROPIC_AUTH_TOKEN") else "<unset>")

print("\n可用测试模式:")
print("  mock       = 完全离线，不需要任何模型")
print("  lmstudio   = 本机 LM Studio 模型")
print("  ccswitch   = 当前 CCSwitch/Claude Code 配置")

print("子进程输出编码：UTF-8（errors=replace，兼容 Windows CMD）")
