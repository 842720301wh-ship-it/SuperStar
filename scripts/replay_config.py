"""从原任务提交读取账号和课程配置，使用当前代码重新执行。"""
import argparse
import configparser
import re
import subprocess
import textwrap
from pathlib import Path


def parse_workflow_config(workflow_text):
    opening = re.search(r"cat\s*>\s*config\.ini\s*<<['\"](?P<marker>\w+)['\"]\r?\n", workflow_text)
    if opening is None:
        raise ValueError("原工作流未包含支持的 config.ini 配置")
    tail = workflow_text[opening.end():]
    closing = re.search(rf"(?m)^\s*{re.escape(opening['marker'])}\s*$", tail)
    if closing is None:
        raise ValueError("原配置结束标记缺失")
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(textwrap.dedent(tail[:closing.start()]))
    if not config.has_section("common") or not config.has_section("tiku"):
        raise ValueError("原配置缺少 common 或 tiku")
    if not config.get("common", "course_list", fallback="").strip():
        raise ValueError("原配置没有课程 ID")
    return config


def write_replay_config(workflow_text, output_path):
    config = parse_workflow_config(workflow_text)
    config.set("tiku", "cover_rate", "0.5")
    with Path(output_path).open("w", encoding="utf8") as output:
        config.write(output)
    Path(output_path).chmod(0o600)
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_sha")
    parser.add_argument("--output", default="config.ini")
    args = parser.parse_args()
    if re.fullmatch(r"[0-9a-f]{40}", args.source_sha) is None:
        parser.error("source_sha 必须是完整提交 SHA")
    workflow = subprocess.check_output(
        ["git", "show", f"{args.source_sha}:.github/workflows/main.yml"], text=True
    )
    config = write_replay_config(workflow, args.output)
    for section, name in (("common", "username"), ("common", "password"), ("tiku", "key"), ("tiku", "token")):
        value = config.get(section, name, fallback="")
        if value:
            escaped = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::add-mask::{escaped}")
    print(f"已读取原任务配置，覆盖率=0.5，课程={config.get('common', 'course_list')}")


if __name__ == "__main__":
    main()
