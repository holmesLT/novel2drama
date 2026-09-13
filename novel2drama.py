#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
novel2drama —— 把小说文本改编为结构化短剧剧本（第一步：小说 → 剧本）

只依赖 Python 标准库，无需安装任何第三方包。

用法:
    python3 novel2drama.py 小说.txt
    python3 novel2drama.py 小说.txt -o 输出目录
    python3 novel2drama.py --demo          # 不配置 API Key 也能看到完整流程
"""

import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

# 默认使用智谱 GLM 的 glm-4-flash 模型（免费），接口与 OpenAI 兼容；
# 换成 DeepSeek / 通义 / OpenAI 等只需改 config.json 里的三个字段。
DEFAULT_CONFIG = {
    "api_base": "https://open.bigmodel.cn/api/paas/v4",
    "api_key": "",
    "model": "glm-4-flash",
}

CONFIG_FILENAMES = ("config.json",)
MAX_CHARS_PER_CHUNK = 6000


def load_config(config_path=None):
    """加载配置：config 文件 < 环境变量 < 内置默认值。"""
    config = dict(DEFAULT_CONFIG)

    candidates = []
    if config_path:
        candidates.append(config_path)
    candidates.append(os.path.join(os.getcwd(), *CONFIG_FILENAMES))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), *CONFIG_FILENAMES))

    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    user_config = json.load(f)
                config.update({k: v for k, v in user_config.items() if v})
                print(f"[配置] 已加载 {path}")
            except (json.JSONDecodeError, OSError) as exc:
                print(f"[警告] 配置文件 {path} 读取失败：{exc}", file=sys.stderr)
            break

    # 环境变量优先级最高，方便不想把 Key 写进文件的用户
    config["api_key"] = os.environ.get("NOVEL2DRAMA_API_KEY", config["api_key"])
    config["model"] = os.environ.get("NOVEL2DRAMA_MODEL", config["model"])
    config["api_base"] = os.environ.get("NOVEL2DRAMA_API_BASE", config["api_base"])
    return config


# ---------------------------------------------------------------------------
# 提示词与剧本格式
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是一位经验丰富的短剧编剧。你的任务是把小说文本改编为适合拍摄 AI 短剧的结构化剧本。

你必须只输出一个 JSON 对象，不要输出任何解释、注释或 markdown 代码块标记。JSON 结构如下：

{
  "title": "本集标题",
  "genre": "题材类型，如：都市、玄幻、悬疑",
  "characters": [
    {"name": "角色名", "desc": "一句话外貌/性格描述，供后续生图使用",
     "appearance": "角色锁定外貌：国籍/族群、年龄段、发型发色、五官特征、主要服装，40字以内"}
  ],
  "scenes": [
    {
      "scene_id": 1,
      "heading": "内景/外景 - 地点 - 日/夜",
      "summary": "本场景剧情概要（一句话）",
      "beats": [
        {"type": "action", "text": "动作/画面描述，用镜头感的语言书写"},
        {"type": "dialogue", "character": "角色名", "text": "台词内容", "emotion": "情绪提示"}
      ]
    }
  ]
}

改编要求：
1. 忠实于原作情节，但要把心理描写转化为可见的动作、表情或台词。
2. 每个场景控制在 1-3 分钟的剧情量，动作描述要具体、可视化，适合作为文生视频的提示。
3. 台词要口语化、简短有力，符合短剧快节奏的特点。
4. 角色名必须出现在 characters 列表中。
5. appearance（锁定外貌）必须符合小说的时代与地域设定，例如中国现代都市故事的角色使用中国面孔；全剧所有场景共用同一份外貌描述，措辞不得变化，这是保证跨镜头人物形象一致的关键。
6. 只输出 JSON，第一个字符必须是 { ，最后一个字符必须是 } 。"""

CONTINUATION_PROMPT = """\n\n【续写说明】这是同一部小说的后续片段，前面已改编的场景请勿重复输出。
scene_id 从 %d 继续编号，characters 列表仍然要完整输出（包含新角色）。"""


def build_user_prompt(text, next_scene_id):
    prompt = "请把下面的小说文本改编为剧本 JSON：\n\n" + text.strip()
    if next_scene_id > 1:
        prompt += CONTINUATION_PROMPT % next_scene_id
    return prompt


# ---------------------------------------------------------------------------
# LLM 调用（OpenAI 兼容接口）
# ---------------------------------------------------------------------------

def urlopen_with_retry(request, timeout=180):
    """打开 URL；遇到 SSL 证书问题时尝试用 certifi 重试并给出修复指引。"""
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        # macOS 上 python.org 版 Python 常见问题：SSL 根证书未安装。
        if isinstance(reason, ssl.SSLError) and "CERTIFICATE_VERIFY_FAILED" in str(reason):
            try:
                import certifi
                context = ssl.create_default_context(cafile=certifi.where())
                return urllib.request.urlopen(request, timeout=timeout, context=context)
            except ImportError:
                raise RuntimeError(
                    "SSL 证书校验失败。修复方法（macOS）：\n"
                    "  打开文件夹 /Applications/Python 3.12，双击运行"
                    "「Install Certificates.command」"
                ) from exc
        raise


def call_llm(config, user_prompt, timeout=180, system_prompt=SYSTEM_PROMPT):
    url = config["api_base"].rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + config["api_key"],
        },
        method="POST",
    )

    try:
        with urlopen_with_retry(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"API 请求失败（HTTP {exc.code}）：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 API（{config['api_base']}）：{exc.reason}") from exc

    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"API 返回了意外结构：{json.dumps(body, ensure_ascii=False)[:500]}") from exc


def extract_json(text):
    """从模型输出中稳健地提取 JSON 对象（容忍代码块标记和前后缀文本）。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    start = text.find("{")
    if start == -1:
        raise ValueError("模型输出中没有找到 JSON")

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    return json.loads(candidate)
    raise ValueError("JSON 不完整（输出可能被截断）")


# ---------------------------------------------------------------------------
# 长文本分块
# ---------------------------------------------------------------------------

def chunk_text(text, max_chars=MAX_CHARS_PER_CHUNK):
    """按空行（段落边界）把长文本切成若干块。"""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    paragraphs = re.split(r"\n\s*\n", text)
    chunks, current = [], ""
    for para in paragraphs:
        if current and len(current) + len(para) + 2 > max_chars:
            chunks.append(current.strip())
            current = para
        else:
            current = (current + "\n\n" + para) if current else para
    if current.strip():
        chunks.append(current.strip())
    return chunks


# ---------------------------------------------------------------------------
# 校验与渲染
# ---------------------------------------------------------------------------

def validate_episode(episode):
    if not isinstance(episode, dict) or "scenes" not in episode:
        raise ValueError("剧本缺少 scenes 字段")
    names = {c.get("name", "") for c in episode.get("characters", [])}
    for scene in episode["scenes"]:
        for beat in scene.get("beats", []):
            if beat.get("type") == "dialogue" and beat.get("character") not in names:
                print(f"[警告] 场景 {scene.get('scene_id')} 中台词角色"
                      f"「{beat.get('character')}」不在角色表里", file=sys.stderr)
    return episode


def render_markdown(episode):
    lines = [f"# {episode.get('title', '未命名剧本')}", ""]
    if episode.get("genre"):
        lines += [f"**题材**：{episode['genre']}", ""]

    characters = episode.get("characters", [])
    if characters:
        lines += ["## 角色表", ""]
        for c in characters:
            lines.append(f"- **{c.get('name', '?')}**：{c.get('desc', '')}")
        lines.append("")

    for scene in episode.get("scenes", []):
        lines += [f"## 场景 {scene.get('scene_id', '?')}　{scene.get('heading', '')}", ""]
        if scene.get("summary"):
            lines += [f"> {scene['summary']}", ""]
        for beat in scene.get("beats", []):
            if beat.get("type") == "dialogue":
                emotion = f"（{beat['emotion']}）" if beat.get("emotion") else ""
                lines.append(f"**{beat.get('character', '?')}**{emotion}：{beat.get('text', '')}")
            else:
                lines.append(f"*{beat.get('text', '')}*")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Demo 模式：不调用 API，输出一份内置的示例结果
# ---------------------------------------------------------------------------

DEMO_EPISODE = {
    "title": "雨夜来客",
    "genre": "悬疑",
    "characters": [
        {"name": "林晚", "desc": "三十岁女侦探，短发，眼神锐利，风衣",
         "appearance": "中国女性，三十岁左右，齐耳黑色短发，眉眼锐利，穿深灰色风衣，身形干练"},
        {"name": "陈默", "desc": "四十岁男，旧书店老板，戴圆框眼镜，神情疲惫",
         "appearance": "中国男性，四十岁上下，微乱的黑色短发，戴圆框金属眼镜，穿深棕色旧毛衣，面容清瘦疲惫"},
    ],
    "scenes": [
        {
            "scene_id": 1,
            "heading": "内景 - 旧书店 - 夜",
            "summary": "暴雨夜，林晚走进打烊前的旧书店，向陈默打听一本禁书。",
            "beats": [
                {"type": "action", "text": "特写：雨点砸在书店褪色的招牌上，玻璃门被推开，风铃乱响。"},
                {"type": "action", "text": "林晚抖落风衣上的雨水，目光扫过一排排落灰的书架。"},
                {"type": "dialogue", "character": "林晚", "text": "我要找一本书。它不在任何书目里。", "emotion": "冷静"},
                {"type": "dialogue", "character": "陈默", "text": "小姐，我们十分钟后打烊。", "emotion": "敷衍"},
                {"type": "action", "text": "林晚把一张泛黄的照片拍在柜台上。陈默的脸色瞬间变了，眼镜片后的眼睛眯了起来。"},
                {"type": "dialogue", "character": "陈默", "text": "……你从哪里拿到这个的？", "emotion": "紧张压低声音"},
            ],
        }
    ],
}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def novel_to_episode(text, config):
    """把小说文本改编为剧本 dict。超过长度上限会自动分块、逐块改编再合并。"""
    chunks = chunk_text(text)
    if len(chunks) > 1:
        print(f"[信息] 文本较长，已分为 {len(chunks)} 块逐段改编")

    episodes = []
    next_scene_id = 1
    for i, chunk in enumerate(chunks, 1):
        print(f"[信息] 正在改编第 {i}/{len(chunks)} 块…")
        content = call_llm(config, build_user_prompt(chunk, next_scene_id))
        episode = extract_json(content)
        episode = validate_episode(episode)
        scenes = episode.get("scenes", [])
        for scene in scenes:
            scene["scene_id"] = next_scene_id
            next_scene_id += 1
        episodes.append(episode)

    if not episodes:
        raise RuntimeError("没有生成任何场景")

    merged = episodes[0]
    for ep in episodes[1:]:
        merged["scenes"].extend(ep.get("scenes", []))
        existing = {c.get("name") for c in merged.get("characters", [])}
        for c in ep.get("characters", []):
            if c.get("name") not in existing:
                merged.setdefault("characters", []).append(c)
    return merged


def main():
    parser = argparse.ArgumentParser(
        description="novel2drama —— 把小说文本改编为结构化短剧剧本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n  python3 novel2drama.py 小说.txt\n  python3 novel2drama.py --demo",
    )
    parser.add_argument("input", nargs="?", help="小说文本文件（.txt）")
    parser.add_argument("-o", "--output-dir", default="output", help="输出目录（默认 output/）")
    parser.add_argument("--config", help="配置文件路径（默认在当前目录或脚本目录找 config.json）")
    parser.add_argument("--demo", action="store_true", help="演示模式：不调用 API，输出内置示例")
    args = parser.parse_args()

    if args.demo:
        if not args.input:
            parser.error("演示模式也需要一个输入文件，例如: python3 novel2drama.py examples/sample_novel.txt --demo")
        with open(args.input, "r", encoding="utf-8") as f:
            text = f.read()
        episode = dict(DEMO_EPISODE)
        print("[演示] 使用内置示例剧本，未调用 API。")
    else:
        if not args.input:
            parser.error("请提供小说文本文件，或使用 --demo 查看演示")
        config = load_config(args.config)
        if not config["api_key"]:
            print(
                "[错误] 尚未配置 API Key。\n"
                "  方法一：复制 config.example.json 为 config.json，填入你的 api_key；\n"
                "  方法二：设置环境变量 NOVEL2DRAMA_API_KEY。\n"
                "  推荐免费的智谱 glm-4-flash，详见 README。也可以先试: python3 novel2drama.py {} --demo".format(args.input),
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            with open(args.input, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as exc:
            print(f"[错误] 无法读取 {args.input}：{exc}", file=sys.stderr)
            sys.exit(1)
        if not text.strip():
            print("[错误] 输入文件是空的", file=sys.stderr)
            sys.exit(1)

        try:
            episode = novel_to_episode(text, config)
        except RuntimeError as exc:
            print(f"[错误] {exc}", file=sys.stderr)
            sys.exit(1)
        except ValueError as exc:
            print(f"[错误] 模型输出解析失败：{exc}\n可以重试一次，或换一个更强的模型。", file=sys.stderr)
            sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.input))[0]
    json_path = os.path.join(args.output_dir, stem + ".episode.json")
    md_path = os.path.join(args.output_dir, stem + ".剧本.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(episode, f, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(episode))

    scene_count = len(episode.get("scenes", []))
    print(f"[完成] 共 {scene_count} 个场景")
    print(f"  剧本数据: {json_path}")
    print(f"  可读剧本: {md_path}")
    print("\n下一步：人工审读并修改 md 剧本；JSON 文件是第二步「剧本 → 分镜」的输入。")


if __name__ == "__main__":
    main()
