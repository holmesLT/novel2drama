#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
storyboard —— 第二步：把剧本（episode.json）拆解为分镜脚本（storyboard.json）

输入是第一步 novel2drama.py 的输出；输出是第三步视频生成的输入。
只依赖 Python 标准库。

用法:
    python3 storyboard.py output/小说.episode.json
    python3 storyboard.py output/小说.episode.json --demo     # 不调用 API 看流程
"""

import argparse
import json
import os
import sys

import novel2drama as n2d  # 复用 LLM 调用、JSON 提取、配置加载

# ---------------------------------------------------------------------------
# 提示词
# ---------------------------------------------------------------------------

STORYBOARD_SYSTEM_PROMPT = """你是一位专业的短剧分镜师。你的任务是把这个场景改编为分镜列表，供文生视频模型逐镜头生成画面。

你必须只输出一个 JSON 对象，不要输出任何解释或 markdown 标记。JSON 结构如下：

{
  "shots": [
    {
      "shot_id": "S1-01",
      "duration_sec": 5,
      "shot_type": "特写",
      "description": "中文画面描述（给人看的）",
      "video_prompt": "自包含的文生视频提示词（给模型用的）",
      "dialogue": [
        {"character": "角色名", "text": "台词", "emotion": "情绪"}
      ]
    }
  ]
}

分镜要求：
1. shot_id 格式为 S{场景号}-{两位序号}，例如 S3-01、S3-02。
2. 每个镜头 duration_sec 在 3 到 8 秒之间；台词按正常语速估算时长，一行短台词约 2-4 秒。
3. shot_type 使用景别（远景/全景/中景/近景/特写）或运镜（推、拉、摇、跟）。
4. video_prompt 必须自包含：把景别、主体及其外貌特征、动作、环境、光线、氛围整合成一段完整描述，不依赖其他镜头的上下文；风格关键词参考全片视觉风格。
5. 场景 beats 中所有台词必须分配到某个镜头的 dialogue 里，顺序不变；不要新增剧本中不存在的台词，也不要改动台词原文。
6. 画面描述必须符合物理逻辑和空间常识（人物站在地板上而不是家具上、物体位置前后一致）；同一场景中角色的外貌特征要保持一致。
7. 只输出 JSON，第一个字符必须是 { ，最后一个字符必须是 } 。"""


def build_scene_prompt(scene, episode, scene_no):
    style = episode.get("style") or (
        "电影感，与题材「%s」相符的视觉风格" % episode.get("genre", "剧情")
    )
    scene_data = {
        "scene_id": scene_no,
        "heading": scene.get("heading", ""),
        "summary": scene.get("summary", ""),
        "beats": scene.get("beats", []),
    }
    return (
        "全片视觉风格：%s\n\n角色表：%s\n\n场景数据：\n%s\n\n"
        "请为场景 %d 生成分镜 JSON。"
        % (style,
           json.dumps(episode.get("characters", []), ensure_ascii=False),
           json.dumps(scene_data, ensure_ascii=False, indent=1),
           scene_no)
    )


# ---------------------------------------------------------------------------
# 校验与渲染
# ---------------------------------------------------------------------------

def validate_shots(shots, scene_no, character_names):
    problems = []
    seen_ids = set()
    for shot in shots:
        sid = shot.get("shot_id") or ""
        if sid in seen_ids:
            problems.append(f"镜头编号重复：{sid}")
        seen_ids.add(sid)
        dur = shot.get("duration_sec")
        if not isinstance(dur, (int, float)) or not 1 <= dur <= 30:
            problems.append(f"镜头 {sid} 时长异常：{dur}")
            shot["duration_sec"] = 5
        for d in shot.get("dialogue", []):
            if d.get("character") not in character_names:
                problems.append(f"镜头 {sid} 台词角色「{d.get('character')}」不在角色表里")
        if not shot.get("video_prompt"):
            problems.append(f"镜头 {sid} 缺少 video_prompt")
    return shots, problems


def render_markdown(storyboard):
    lines = [f"# {storyboard.get('title', '未命名')}　分镜表", ""]
    total = 0
    shot_count = 0
    for scene in storyboard.get("scenes", []):
        lines += [f"## 场景 {scene.get('scene_id', '?')}　{scene.get('heading', '')}", ""]
        for shot in scene.get("shots", []):
            shot_count += 1
            total += shot.get("duration_sec", 0)
            lines.append(
                f"### {shot.get('shot_id', '?')}　"
                f"{shot.get('shot_type', '')}　{shot.get('duration_sec', '?')} 秒"
            )
            lines.append("")
            lines.append(f"- **画面**：{shot.get('description', '')}")
            lines.append(f"- **视频提示词**：`{shot.get('video_prompt', '')}`")
            for d in shot.get("dialogue", []):
                emotion = f"（{d['emotion']}）" if d.get("emotion") else ""
                lines.append(f"- **{d.get('character', '?')}**{emotion}：{d.get('text', '')}")
            lines.append("")

    lines.append("---")
    lines.append(f"共 {shot_count} 个镜头，预计片长约 {total // 60} 分 {total % 60} 秒。")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Demo 模式
# ---------------------------------------------------------------------------

DEMO_SCENE_SHOTS = {
    "shots": [
        {
            "shot_id": "S1-01", "duration_sec": 4, "shot_type": "特写",
            "description": "雨点砸在书店褪色的招牌上，风铃在门口轻晃",
            "video_prompt": "特写镜头，雨夜，雨点密集砸在老旧书店的木质招牌上，招牌上『墨香书屋』字样褪色，门口风铃轻晃，地面积水反射暖黄灯光，电影感，浅景深",
            "dialogue": [],
        },
        {
            "shot_id": "S1-02", "duration_sec": 6, "shot_type": "中景",
            "description": "林晚推门进店，抖落风衣上的雨水，目光扫过书架",
            "video_prompt": "中景，雨夜旧书店内，一位短发年轻女侦探穿风衣推门而入，抖落肩上的雨水，目光警惕地扫过落灰的木质书架，暖黄灯光，电影感",
            "dialogue": [{"character": "林晚", "text": "我要找一本书。它不在任何书目里。", "emotion": "冷静"}],
        },
        {
            "shot_id": "S1-03", "duration_sec": 5, "shot_type": "近景",
            "description": "陈默从书堆后抬头，神情疲惫，敷衍地回应",
            "video_prompt": "近景，旧书店柜台后，一位戴圆框眼镜的中年男子从书堆后抬起头，神情疲惫，眼神敷衍，身后是满墙旧书，暖黄台灯光，电影感",
            "dialogue": [{"character": "陈默", "text": "小姐，我们十分钟后打烊。", "emotion": "敷衍"}],
        },
    ],
}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def episode_to_storyboard(episode):
    character_names = {c.get("name") for c in episode.get("characters", [])}
    storyboard = {
        "title": episode.get("title", ""),
        "genre": episode.get("genre", ""),
        "style": episode.get("style", ""),
        "characters": episode.get("characters", []),
        "scenes": [],
    }
    all_problems = []
    scenes = episode.get("scenes", [])
    for scene in scenes:
        scene_no = scene.get("scene_id", len(storyboard["scenes"]) + 1)
        print(f"[信息] 正在为场景 {scene_no} 生成分镜…")
        content = n2d.call_llm(
            CONFIG,
            build_scene_prompt(scene, episode, scene_no),
            system_prompt=STORYBOARD_SYSTEM_PROMPT,
        )
        shots = n2d.extract_json(content).get("shots", [])
        shots, problems = validate_shots(shots, scene_no, character_names)
        for p in problems:
            print(f"[警告] {p}", file=sys.stderr)
        all_problems += problems
        storyboard["scenes"].append({**scene, "shots": shots})

    if all_problems:
        print(f"[提示] 共 {len(all_problems)} 处需要人工复核，已在上面列出", file=sys.stderr)
    return storyboard


def main():
    parser = argparse.ArgumentParser(
        description="storyboard —— 把剧本（episode.json）拆解为分镜脚本（storyboard.json）",
        epilog="示例:\n  python3 storyboard.py output/小说.episode.json\n  python3 storyboard.py output/小说.episode.json --demo",
    )
    parser.add_argument("input", help="剧本 JSON 文件（第一步的输出）")
    parser.add_argument("-o", "--output-dir", default="output", help="输出目录（默认 output/）")
    parser.add_argument("--config", help="配置文件路径")
    parser.add_argument("--demo", action="store_true", help="演示模式：不调用 API")
    args = parser.parse_args()

    global CONFIG
    CONFIG = n2d.load_config(args.config)

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            episode = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[错误] 无法读取剧本文件 {args.input}：{exc}", file=sys.stderr)
        sys.exit(1)
    if "scenes" not in episode:
        print("[错误] 这不是一个剧本 JSON（缺少 scenes 字段）", file=sys.stderr)
        sys.exit(1)

    if args.demo:
        storyboard = {
            "title": episode.get("title", ""),
            "genre": episode.get("genre", ""),
            "style": episode.get("style", ""),
            "characters": episode.get("characters", []),
            "scenes": [{**scene, "shots": DEMO_SCENE_SHOTS["shots"]} for scene in episode["scenes"][:1]],
        }
        print("[演示] 仅对第一个场景使用内置示例分镜，未调用 API。")
    else:
        if not CONFIG["api_key"]:
            print(
                "[错误] 尚未配置 API Key：复制 config.example.json 为 config.json 填入 api_key，"
                "或设置环境变量 NOVEL2DRAMA_API_KEY。也可以先试 --demo。",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            storyboard = episode_to_storyboard(episode)
        except RuntimeError as exc:
            print(f"[错误] {exc}", file=sys.stderr)
            sys.exit(1)
        except ValueError as exc:
            print(f"[错误] 模型输出解析失败：{exc}", file=sys.stderr)
            sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.input))[0].replace(".episode", "")
    json_path = os.path.join(args.output_dir, stem + ".storyboard.json")
    md_path = os.path.join(args.output_dir, stem + ".分镜表.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(storyboard, f, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(storyboard))

    shot_total = sum(len(s.get("shots", [])) for s in storyboard.get("scenes", []))
    print(f"[完成] 共 {len(storyboard['scenes'])} 个场景、{shot_total} 个镜头")
    print(f"  分镜数据: {json_path}")
    print(f"  可读分镜表: {md_path}")
    print("\n下一步：人工审读分镜表，重点核对 video_prompt 的画面质量；JSON 是第三步「分镜 → 成片」的输入。")


if __name__ == "__main__":
    main()
