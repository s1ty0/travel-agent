"""Explicit live-call CLI. Reads travel input from stdin, not shell arguments."""

import argparse
import json
import sys

from .deepseek import DeepSeekSettings, ProviderError, extract_intake


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="DeepSeek 候选需求提取；每次最多一次付费请求")
    parser.add_argument("--live", action="store_true", help="显式启用一次真实模型调用")
    args = parser.parse_args()
    if not args.live:
        print("未启用真实调用；需要 --live，且先配置本地环境变量。", file=sys.stderr)
        return 2
    try:
        settings = DeepSeekSettings.from_env()
        text = sys.stdin.read(4001)
        result = extract_intake(text, settings)
    except ProviderError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(result.model_dump_json(indent=2))
    return 0
