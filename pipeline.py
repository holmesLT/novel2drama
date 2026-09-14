#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline —— 一键流水线：小说文本 → 剧本 → 分镜 → 成片

把三步工具串成一条命令，适合不想记命令的新手：

    python3 pipeline.py 我的小说.txt
    python3 pipeline.py 我的小说.txt --aspect 9:16     # 竖屏短剧
    python3 pipeline.py 我的小说.txt --skip-video      # 只重做字幕/配音/拼接

前置条件：config.json 已填好 API Key；已安装 ffmpeg-full（macOS: brew install ffmpeg-full）。
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run_step(name, cmd):
    print(f"\n========== {name} ==========", flush=True)
    result = subprocess.run(cmd, cwd=HERE)
    if result.returncode != 0:
        print(f"[错误] {name} 失败（exit {result.returncode}），流水线中止。", file=sys.stderr)
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser(
        description="pipeline —— 一键把小说变成成片",
        epilog="示例:\n  python3 pipeline.py 我的小说.txt\n  python3 pipeline.py 我的小说.txt --aspect 9:16",
    )
    parser.add_argument("input", help="小说文本文件（.txt）")
    parser.add_argument("--aspect", choices=("16:9", "9:16"), help="画面比例，默认读 config")
    parser.add_argument("--force", action="store_true", help="忽略缓存全部重做")
    parser.add_argument("--skip-video", action="store_true", help="跳过视频生成（需要已有片段缓存）")
    parser.add_argument("--config", help="配置文件路径")
    args = parser.parse_args()

    stem = os.path.splitext(os.path.basename(args.input))[0]
    episode = os.path.join("output", stem + ".episode.json")
    storyboard = os.path.join("output", stem + ".storyboard.json")

    pass_through = []
    if args.config:
        pass_through += ["--config", args.config]
    if args.force:
        pass_through.append("--force")

    run_step("第一步：小说 → 剧本", ["python3", "novel2drama.py", args.input, *pass_through])
    run_step("第二步：剧本 → 分镜", ["python3", "storyboard.py", episode, *pass_through])

    render_args = ["python3", "render.py", storyboard, *pass_through]
    if args.aspect:
        render_args += ["--aspect", args.aspect]
    if args.skip_video:
        render_args.append("--skip-video")
    run_step("第三步：分镜 → 成片", render_args)

    print(f"\n[全部完成] 成片在 output/render_{stem}/{stem}.成片.mp4")


if __name__ == "__main__":
    main()
