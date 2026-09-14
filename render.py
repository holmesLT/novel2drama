#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render —— 第三步：把分镜（storyboard.json）合成为成片

流程：逐镜头生成视频（智谱 CogVideoX）→ 逐镜头生成配音（智谱 GLM-TTS）
     → FFmpeg 音画对齐并按顺序拼接成片。

只依赖 Python 标准库 + 系统命令 ffmpeg/ffprobe。

用法:
    python3 render.py output/小说.storyboard.json
    python3 render.py output/小说.storyboard.json --shots S1-01,S1-02   # 只渲染部分镜头
    python3 render.py output/小说.storyboard.json --skip-video          # 只重做配音和拼接
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

import novel2drama as n2d  # 复用配置加载和 SSL 自愈逻辑

POLL_INTERVAL = 10          # 秒，视频生成任务轮询间隔
POLL_TIMEOUT = 1200         # 秒，单镜头视频生成超时
PROMPT_MAX = 512            # 视频 API 提示词长度上限
GAP_SEC = 0.4               # 镜头内多句台词之间的停顿

# 没有在 config 中指定音色的角色，按顺序使用这些默认音色
FALLBACK_VOICES = ["tongtong", "xiaochen", "chuichui", "jam"]


# ---------------------------------------------------------------------------
# 智谱异步/二进制接口
# ---------------------------------------------------------------------------

def api_post_json(config, path, payload):
    request = urllib.request.Request(
        config["api_base"].rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + config["api_key"]},
        method="POST",
    )
    with n2d.urlopen_with_retry(request) as response:
        return json.loads(response.read().decode("utf-8"))


def generate_portrait(config, character, out_path):
    """用 CogView 为角色生成定妆照（appearance 锁定外貌 + 统一风格）。"""
    prompt = (
        f"{character.get('appearance', character.get('desc', ''))}，"
        f"单人定妆照，半身像，面向镜头，表情自然，纯浅灰色背景，"
        f"柔和影棚灯光，写实电影风格，高清细节"
    )
    payload = {
        "model": config.get("image_model", "cogview-3-flash"),
        "prompt": prompt[:PROMPT_MAX],
        "size": "1024x1024",
    }
    resp = api_post_json(config, "/images/generations", payload)
    images = resp.get("data") or []
    if not images or not images[0].get("url"):
        raise RuntimeError(f"定妆照生成失败：{json.dumps(resp, ensure_ascii=False)[:300]}")
    with n2d.urlopen_with_retry(urllib.request.Request(images[0]["url"])) as response, \
            open(out_path, "wb") as f:
        f.write(response.read())


def generate_video_clip(config, shot, out_path, portrait_path=None):
    """提交视频生成任务并轮询到完成，下载视频到 out_path。

    portrait_path 非空时使用图生视频：定妆照作为首帧，锁定人物形象。
    """
    aspect = config.get("aspect", "16:9")
    payload = {
        "model": config.get("video_model", "cogvideox-flash"),
        "prompt": shot.get("video_prompt", "")[:PROMPT_MAX],
        "quality": "speed",
        "size": "1080x1920" if aspect == "9:16" else "1920x1080",
    }
    if config.get("video_model", "").startswith("cogvideox-3"):
        payload["duration"] = 5 if shot.get("duration_sec", 5) <= 5 else 10
    if portrait_path:
        with open(portrait_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        payload["image_url"] = "data:image/png;base64," + b64

    print(f"    提交生成任务（{payload['model']}{'，图生视频锁定人物' if portrait_path else ''}）…")
    resp = api_post_json(config, "/videos/generations", payload)
    task_id = resp.get("id")
    if not task_id:
        raise RuntimeError(f"视频任务提交失败：{json.dumps(resp, ensure_ascii=False)[:300]}")

    deadline = time.time() + POLL_TIMEOUT
    while True:
        time.sleep(POLL_INTERVAL)
        req = urllib.request.Request(
            config["api_base"].rstrip("/") + "/async-result/" + task_id,
            headers={"Authorization": "Bearer " + config["api_key"]},
        )
        with n2d.urlopen_with_retry(req) as response:
            result = json.loads(response.read().decode("utf-8"))
        status = result.get("task_status")
        if status == "SUCCESS":
            videos = result.get("video_result") or []
            if not videos or not videos[0].get("url"):
                raise RuntimeError(f"任务 {task_id} 成功但没有视频 URL")
            url = videos[0]["url"]
            print(f"    生成完成，下载中…")
            for attempt in (1, 2, 3):
                try:
                    with n2d.urlopen_with_retry(urllib.request.Request(url), timeout=300) as response, \
                            open(out_path, "wb") as f:
                        f.write(response.read())
                    break
                except (TimeoutError, urllib.error.URLError) as exc:
                    if attempt == 3:
                        raise RuntimeError(f"视频下载连续失败（任务 {task_id}）：{exc}") from exc
                    print(f"    下载超时，重试 {attempt}/3…")
                    time.sleep(5 * attempt)
            return
        if status == "FAIL":
            raise RuntimeError(f"视频生成失败（任务 {task_id}）：{json.dumps(result, ensure_ascii=False)[:300]}")
        if time.time() > deadline:
            raise RuntimeError(f"视频生成超时（任务 {task_id}），稍后可用 --force 重试该镜头")
        print(f"    生成中…（已等待 {int(time.time() - (deadline - POLL_TIMEOUT))} 秒）")


def synthesize_dialogue(config, lines, voice_of, macsay_voice_of, out_path):
    """把一个镜头的多句台词合成为一段配音 WAV。

    两种后端（config 的 tts_backend 字段）：
    - "glm-tts"：智谱 GLM-TTS 接口，音质好，按用量计费；
    - "macsay"：macOS 系统自带 say 命令，免费离线；通过 macsay_voices 配置
      每个角色的 voice 和 pitch（pitch<1 降调，用于没有男声系统时的男角色变声）。
    """
    backend = config.get("tts_backend", "glm-tts")
    parts = []
    for line in lines:
        text = line.get("text", "").strip()
        if not text:
            continue
        part_path = out_path + f".part{len(parts)}.wav"
        if backend == "macsay":
            voice_cfg = macsay_voice_of(line.get("character", ""))
            subprocess.run(
                ["say", "-v", voice_cfg.get("voice", config.get("macsay_voice", "Tingting")),
                 "--data-format=LEI16@24000", "-o", part_path, text],
                check=True,
            )
            pitch = float(voice_cfg.get("pitch", 1.0))
            if abs(pitch - 1.0) > 0.01:
                # asetrate 降低音调，atempo 补偿语速，使变调不变速
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", part_path,
                     "-af", "asetrate=24000*%g,aresample=24000,atempo=%.4f" % (pitch, 1 / pitch),
                     "-c:a", "pcm_s16le", part_path + ".pitched.wav"],
                    check=True,
                )
                os.replace(part_path + ".pitched.wav", part_path)
        else:
            voice = voice_of(line.get("character", ""))
            request = urllib.request.Request(
                config["api_base"].rstrip("/") + "/audio/speech",
                data=json.dumps({
                    "model": config.get("tts_model", "glm-tts"),
                    "input": text,
                    "voice": voice,
                    "response_format": "wav",
                }).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + config["api_key"]},
                method="POST",
            )
            with n2d.urlopen_with_retry(request) as response:
                audio = response.read()
            with open(part_path, "wb") as f:
                f.write(audio)
        parts.append(part_path)

    if not parts:
        return False
    if len(parts) == 1:
        os.replace(parts[0], out_path)
        return True
    # 多句台词：静音间隔后拼接
    inputs, gaps = [], []
    for i, p in enumerate(parts):
        inputs += ["-i", p]
        if i:
            gaps.append("anullsrc=r=24000:cl=mono:d=%g[s%d];[a%d][s%d]" % (GAP_SEC, i, i, i))
    filter_complex = ";".join(gaps) + f";{''.join('[a%d]' % i for i in range(len(parts)))}concat=n={len(parts)}:v=0:a=1[out]"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *inputs,
         "-filter_complex", filter_complex, "-map", "[out]", out_path],
        check=True,
    )
    for p in parts:
        os.remove(p)
    return True


# ---------------------------------------------------------------------------
# FFmpeg 拼接
# ---------------------------------------------------------------------------

def probe_duration(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def extract_last_frame(clip_path, out_path):
    """抽取视频最后一帧，供下一个镜头做首帧接续。"""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-sseof", "-0.2",
         "-i", clip_path, "-frames:v", "1", "-q:v", "2", out_path],
        check=True,
    )


FONT_CANDIDATES = [  # macOS 中文字体候选（不同系统版本路径不同）
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
]


def find_font(config=None):
    """按配置和候选列表找到第一个存在的中文字体；找不到返回 None（不烧字幕）。"""
    candidates = []
    if config and config.get("font"):
        candidates.append(config["font"])
    candidates += FONT_CANDIDATES
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def drawtext_escape(text):
    """转义 drawtext 滤镜的特殊字符。"""
    for ch in ("\\", ":", "'", ",", "%", "[", "]"):
        text = text.replace(ch, "\\" + ch)
    return text


def build_segment(clip_path, audio_path, out_path, dialogues=None, font_file=None):
    """把一个镜头标准化为统一编码参数的片段（音画对齐、静音补足、烧录台词字幕）。"""
    dur = probe_duration(clip_path)
    if audio_path and os.path.isfile(audio_path):
        afilter = f"[1:a]apad=whole_dur={dur + 0.5}[a]"
        ainput = ["-i", audio_path]
        amap = "[a]"
    else:
        afilter = None
        ainput = ["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono"]
        amap = "1:a"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", clip_path, *ainput]
    vf = []
    if dialogues and font_file:
        n = len(dialogues)
        for k, line in enumerate(dialogues):
            text = drawtext_escape(line.get("text", ""))
            start = k * dur / n + 0.15
            end = (k + 1) * dur / n - 0.05
            if end <= start:
                continue
            vf.append(
                f"drawtext=fontfile={font_file}:text='{text}':"
                f"fontsize=40:fontcolor=white:borderw=2:bordercolor=black@0.7:"
                f"x=(w-text_w)/2:y=h-text_h-40:enable='between(t,{start:.2f},{end:.2f})'"
            )
    if vf:
        cmd += ["-vf", ",".join(vf)]
    if afilter:
        cmd += ["-filter_complex", afilter, "-map", "0:v", "-map", amap]
    else:
        cmd += ["-map", "0:v", "-map", amap, "-shortest"]
    cmd += ["-t", f"{dur:.3f}", "-r", "30",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "24000", "-ac", "1",
            "-movflags", "+faststart", out_path]
    subprocess.run(cmd, check=True)


def make_ambience(out_path, duration=120):
    """程序化生成风雪环境音（免版权，作为默认 BGM）。"""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"anoisesrc=color=pink:sample_rate=24000:amplitude=0.6:duration={duration}",
         "-af", "highpass=f=80,lowpass=f=500,tremolo=f=0.25:d=0.8,volume=0.5",
         "-c:a", "pcm_s16le", out_path],
        check=True,
    )


def probe_resolution(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", path],
        capture_output=True, text=True, check=True,
    )
    w, h = result.stdout.strip().split("x")
    return int(w), int(h)


def make_title_card(out_path, title, subtitle, w, h, font, dur=2.5):
    """生成片头/片尾卡（深色底 + 标题文字 + 淡入淡出）。"""
    end = dur - 0.6
    vf = [
        f"drawtext=fontfile={font}:text='{drawtext_escape(title)}':"
        f"fontsize={int(h * 0.06)}:fontcolor=white:borderw=2:bordercolor=black@0.6:"
        f"x=(w-text_w)/2:y=(h-text_h)/2-{int(h * 0.02)}",
        f"drawtext=fontfile={font}:text='{drawtext_escape(subtitle)}':"
        f"fontsize={int(h * 0.025)}:fontcolor=white@0.7:"
        f"x=(w-text_w)/2:y=(h-text_h)/2+{int(h * 0.05)}",
        f"fade=t=in:st=0:d=0.5,fade=t=out:st={end}:d=0.5",
    ]
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=c=0x14141c:s={w}x{h}:r=30:d={dur}",
         "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-vf", ",".join(vf), "-shortest",
         "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-ar", "24000", "-ac", "1", out_path],
        check=True,
    )


def mix_bgm(final_path, bgm_path, volume):
    """把 BGM 循环混入成片（压低音量，首尾淡入淡出）。"""
    dur = probe_duration(final_path)
    fade_start = max(0, dur - 3)
    tmp = final_path + ".bgm.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-i", final_path, "-stream_loop", "-1", "-i", bgm_path,
         "-filter_complex",
         f"[1:a]volume={volume},afade=t=in:d=2,afade=t=out:st={fade_start:.2f}:d=3[b];"
         f"[0:a][b]amix=inputs=2:duration=first:normalize=0[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", tmp],
        check=True,
    )
    os.replace(tmp, final_path)


def concat_segments(segment_paths, out_path, workdir):
    list_path = os.path.join(workdir, "concat.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in segment_paths:
            f.write("file '%s'\n" % os.path.abspath(p).replace("'", "'\\''"))
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", list_path, "-c", "copy", out_path],
        check=True,
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="render —— 把分镜（storyboard.json）合成为成片",
        epilog="示例:\n  python3 render.py output/小说.storyboard.json\n"
               "  python3 render.py output/小说.storyboard.json --shots S1-01,S1-02",
    )
    parser.add_argument("input", help="分镜 JSON 文件（第二步的输出）")
    parser.add_argument("-o", "--workdir", default=None, help="工作目录（默认 output/render_<名称>/）")
    parser.add_argument("--shots", help="只渲染指定镜头，逗号分隔，如 S1-01,S2-03")
    parser.add_argument("--force", action="store_true", help="忽略已生成的片段，全部重做")
    parser.add_argument("--skip-video", action="store_true", help="跳过视频生成，使用已有片段")
    parser.add_argument("--lock", action="store_true", help="定妆照锁定：为角色生成定妆照，人物镜头改用图生视频（也可在 config 里设 lock_characters: true）")
    parser.add_argument("--chain", action="store_true", help="尾帧接续：同一场景内，下一镜头以上一镜头的末帧为首帧（也可在 config 里设 chain_shots: true）")
    parser.add_argument("--aspect", choices=("16:9", "9:16"), help="画面比例：16:9 横屏 / 9:16 竖屏（也可在 config 里设 aspect）")
    parser.add_argument("--config", help="配置文件路径")
    args = parser.parse_args()

    config = n2d.load_config(args.config)
    if args.aspect:
        config["aspect"] = args.aspect

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            storyboard = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[错误] 无法读取分镜文件 {args.input}：{exc}", file=sys.stderr)
        sys.exit(1)
    if "scenes" not in storyboard:
        print("[错误] 这不是分镜 JSON（缺少 scenes 字段）", file=sys.stderr)
        sys.exit(1)

    only = set(s.strip() for s in args.shots.split(",")) if args.shots else None
    scene_shots = []
    for scene in storyboard.get("scenes", []):
        for shot in scene.get("shots", []):
            if only is None or shot.get("shot_id") in only:
                scene_shots.append((scene.get("scene_id"), shot))
    if not scene_shots:
        print("[错误] 没有匹配的镜头", file=sys.stderr)
        sys.exit(1)

    if not config["api_key"] and not args.skip_video:
        print("[错误] 尚未配置 API Key（需要同时用于视频生成和配音）。", file=sys.stderr)
        sys.exit(1)
    for tool in ("ffmpeg", "ffprobe"):
        if not args.skip_video and subprocess.run(["which", tool], capture_output=True).returncode != 0:
            print(f"[错误] 未找到 {tool}，请先安装：brew install ffmpeg", file=sys.stderr)
            sys.exit(1)

    stem = os.path.splitext(os.path.basename(args.input))[0].replace(".storyboard", "")
    workdir = args.workdir or os.path.join("output", "render_" + stem)
    clips_dir = os.path.join(workdir, "clips")
    audio_dir = os.path.join(workdir, "audio")
    build_dir = os.path.join(workdir, "build")
    frames_dir = os.path.join(workdir, "frames")
    for d in (clips_dir, audio_dir, build_dir, frames_dir):
        os.makedirs(d, exist_ok=True)

    # 角色音色分配
    voice_map = dict(config.get("voices", {}))
    macsay_map = {k: dict(v) for k, v in (config.get("macsay_voices") or {}).items()}
    fallback_index = 0

    def voice_of(character):
        nonlocal fallback_index
        if character in voice_map:
            return voice_map[character]
        voice = FALLBACK_VOICES[fallback_index % len(FALLBACK_VOICES)]
        fallback_index += 1
        voice_map[character] = voice
        return voice

    def macsay_voice_of(character):
        if character in macsay_map:
            return macsay_map[character]
        # 未配置的角色：默认系统音色、不变调
        cfg = {"voice": config.get("macsay_voice", "Tingting"), "pitch": 1.0}
        macsay_map[character] = cfg
        return cfg

    # 定妆照锁定：人物镜头用图生视频（定妆照作首帧）
    lock = args.lock or bool(config.get("lock_characters"))
    portraits = {}
    if lock and not args.skip_video:
        portrait_dir = os.path.join(workdir, "characters")
        os.makedirs(portrait_dir, exist_ok=True)
        print(f"[定妆照] 为 {len(storyboard.get('characters', []))} 个角色生成参考图…")
        for c in storyboard.get("characters", []):
            name = c.get("name", "")
            if not name:
                continue
            p = os.path.join(portrait_dir, name + ".png")
            if os.path.isfile(p) and not args.force:
                print(f"    {name}：已有定妆照，跳过")
            else:
                try:
                    generate_portrait(config, c, p)
                    print(f"    {name}：已生成")
                except RuntimeError as exc:
                    print(f"[警告] {name} 定妆照生成失败，该角色回退纯文生视频：{exc}", file=sys.stderr)
                    continue
            portraits[name] = p

    def portrait_for(shot):
        """找出镜头中的人物（台词角色优先），返回其定妆照路径。"""
        names = [d.get("character") for d in shot.get("dialogue", []) if d.get("character")]
        text = shot.get("description", "") + shot.get("video_prompt", "")
        for name in portraits:
            if name in text and name not in names:
                names.append(name)
        for name in names:
            if name in portraits:
                return portraits[name]
        return None

    total_cost_shots = sum(1 for _, s in scene_shots if not os.path.isfile(os.path.join(clips_dir, s["shot_id"] + ".mp4")) or args.force)
    print(f"[计划] 共 {len(scene_shots)} 个镜头，其中 {total_cost_shots} 个需要生成视频"
          f"（模型 {config.get('video_model', 'cogvideox-flash')}"
          f"{'，免费' if config.get('video_model', 'cogvideox-flash') == 'cogvideox-flash' else '，注意计费'}）")

    segments = []
    chain = args.chain or bool(config.get("chain_shots"))
    font_file = find_font(config) if config.get("subtitles", True) else None
    if config.get("subtitles", True) and not font_file:
        print("[警告] 未找到可用中文字体，本次不烧录字幕（可用 config 的 font 字段指定字体文件路径）", file=sys.stderr)
    prev_scene = None
    prev_frame = None
    for i, (scene_id, shot) in enumerate(scene_shots, 1):
        sid = shot.get("shot_id", f"shot{i:02d}")
        print(f"[镜头 {sid}]（{i}/{len(scene_shots)}）{shot.get('description', '')[:40]}…")
        clip_path = os.path.join(clips_dir, sid + ".mp4")
        audio_path = os.path.join(audio_dir, sid + ".wav")

        if not args.skip_video:
            if os.path.isfile(clip_path) and not args.force:
                print("    已有视频片段，跳过生成（--force 可重做）")
            else:
                img_path = None
                if chain and scene_id == prev_scene and prev_frame and os.path.isfile(prev_frame):
                    img_path = prev_frame
                    print("    首帧接续上一镜头末尾画面")
                elif lock and portraits:
                    img_path = portrait_for(shot)
                    if img_path:
                        print(f"    首帧使用角色定妆照")
                try:
                    generate_video_clip(config, shot, clip_path, img_path)
                except RuntimeError as exc:
                    print(f"[错误] {exc}", file=sys.stderr)
                    sys.exit(1)

        if chain:
            frame_path = os.path.join(frames_dir, sid + "_last.png")
            try:
                extract_last_frame(clip_path, frame_path)
                prev_frame = frame_path
            except subprocess.CalledProcessError:
                print(f"[警告] 镜头 {sid} 末帧抽取失败，下一镜头回退定妆照/文生视频", file=sys.stderr)
                prev_frame = None
        prev_scene = scene_id

        if os.path.isfile(audio_path) and not args.force:
            print("    已有配音，跳过")
        elif shot.get("dialogue"):
            print(f"    生成配音（{len(shot['dialogue'])} 句台词）…")
            try:
                has_audio = synthesize_dialogue(config, shot["dialogue"], voice_of, macsay_voice_of, audio_path)
            except (RuntimeError, subprocess.CalledProcessError) as exc:
                print(f"[警告] 镜头 {sid} 配音失败，该镜头将无声音：{exc}", file=sys.stderr)
                has_audio = False
            if not has_audio:
                audio_path = None
        else:
            audio_path = None

        seg_path = os.path.join(build_dir, f"{i:03d}_{sid}.mp4")
        dialogues = shot.get("dialogue") if config.get("subtitles", True) else None
        try:
            build_segment(clip_path, audio_path, seg_path, dialogues, font_file)
        except subprocess.CalledProcessError as exc:
            print(f"[错误] 镜头 {sid} 音画合成失败：{exc}", file=sys.stderr)
            sys.exit(1)
        segments.append(seg_path)

    final_path = os.path.join(workdir, stem + ".成片.mp4")
    print("[拼接] 合成成片…")

    # 片头/片尾卡
    ordered = list(segments)
    if config.get("intro_outro", True) and font_file:
        try:
            w, h = probe_resolution(segments[0])
            intro = os.path.join(build_dir, "000_片头.mp4")
            outro = os.path.join(build_dir, "999_片尾.mp4")
            make_title_card(intro, storyboard.get("title", "未命名"),
                            "novel2drama · AI 短剧工作流", w, h, font_file, dur=2.5)
            make_title_card(outro, "剧终", storyboard.get("title", ""), w, h, font_file, dur=2.2)
            ordered = [intro] + ordered + [outro]
            print("    片头/片尾卡已生成")
        except (subprocess.CalledProcessError, ValueError) as exc:
            print(f"[警告] 片头片尾生成失败，跳过：{exc}", file=sys.stderr)

    concat_segments(ordered, final_path, workdir)

    bgm = config.get("bgm")
    if bgm:
        bgm_path = os.path.join(workdir, "ambience.wav") if bgm == "auto" else bgm
        if bgm == "auto" and not os.path.isfile(bgm_path):
            print("[混音] 生成风雪环境音…")
            make_ambience(bgm_path)
        if os.path.isfile(bgm_path):
            print("[混音] 加入背景音…")
            try:
                mix_bgm(final_path, bgm_path, float(config.get("bgm_volume", 0.18)))
            except subprocess.CalledProcessError as exc:
                print(f"[警告] BGM 混音失败，输出无配乐版本：{exc}", file=sys.stderr)
        else:
            print(f"[警告] BGM 文件不存在：{bgm_path}", file=sys.stderr)

    dur = probe_duration(final_path)
    size_mb = os.path.getsize(final_path) / 1024 / 1024
    print(f"[完成] 成片：{final_path}")
    print(f"  时长 {int(dur // 60)} 分 {dur % 60:.0f} 秒，大小 {size_mb:.1f} MB")
    print("\n提示：片段已缓存在工作目录中，修改某几个镜头后可用 --shots 单独重渲染。")


if __name__ == "__main__":
    main()
