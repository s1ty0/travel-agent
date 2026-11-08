"""Offline intake CLI, using the same contract as the HTTP API."""

import argparse
import sys

from pydantic import ValidationError

from .models import IntakeInput
from .parsing import intake


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="离线旅游需求解析（尚未接入实时来源）")
    parser.add_argument("--destination", required=True)
    parser.add_argument("--budget", required=True)
    args = parser.parse_args()
    try:
        result = intake(IntakeInput(destination=args.destination, budget=args.budget))
    except (ValueError, ValidationError):
        print("输入无效；请检查目的地和预算格式。", file=sys.stderr)
        return 2
    print(result.model_dump_json(indent=2))
    return 0
