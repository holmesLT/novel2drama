# novel2drama 🎬

**从小说到短剧的自动化工作流工具**（小说 → 剧本 → 分镜 → 成片）

把一段小说文本，交给大语言模型，自动改编成结构化的短剧剧本，再自动拆解为带镜头时长和文生视频提示词的分镜脚本，最后自动生成视频、配音并合成为成片。

> 这是一个分三步走的项目，目前三步均已实现，完整流水线可跑通。
> 路线图见下方 [Roadmap](#roadmap)。

## ✨ 特点

- **零依赖**：只用 Python 标准库，装好 Python 3.8+ 即可运行，无需 pip 安装任何东西
- **结构化输出**：剧本是一个定义清晰的 JSON（见 [FORMAT.md](FORMAT.md)），机器可读、人工可改
- **长文自动分块**：小说太长会按段落自动切块、逐段改编、自动合并场景
- **任意模型可换**：任何 OpenAI 兼容接口都行，默认推荐免费的智谱 `glm-4-flash`
- **演示模式**：没有 API Key 也能一键体验完整流程

## 🚀 快速开始

### 1. 准备 API Key

注册 [智谱开放平台](https://open.bigmodel.cn)（或其他 OpenAI 兼容平台），创建一个 API Key。`glm-4-flash` 模型是免费的，适合起步。

### 2. 配置

复制 `config.example.json` 为 `config.json`，填入你的 Key：

```json
{
  "api_base": "https://open.bigmodel.cn/api/paas/v4",
  "api_key": "你的API Key",
  "model": "glm-4-flash"
}
```

也可以不改文件，直接用环境变量：`NOVEL2DRAMA_API_KEY` / `NOVEL2DRAMA_MODEL` / `NOVEL2DRAMA_API_BASE`。

### 3. 运行

```bash
# 真实改编
python3 novel2drama.py examples/sample_novel.txt

# 没有配 Key？先看演示
python3 novel2drama.py examples/sample_novel.txt --demo
```

结果输出在 `output/` 目录：

- `sample_novel.episode.json` —— 结构化剧本数据（下一步的输入）
- `sample_novel.剧本.md` —— 可读剧本，方便人工审读和修改

### 4. 剧本 → 分镜（第二步）

```bash
python3 storyboard.py output/sample_novel.episode.json
```

结果输出在 `output/` 目录：

- `sample_novel.storyboard.json` —— 分镜数据：每个场景拆成 3~8 秒的镜头，带景别、时长、自包含的文生视频提示词和该镜头的台词（见 [STORYBOARD.md](STORYBOARD.md)）
- `sample_novel.分镜表.md` —— 可读分镜表，并汇总预计片长

分镜格式是第三步「分镜 → 成片」的输入。

### 5. 分镜 → 成片（第三步）

需要先安装 [FFmpeg](https://ffmpeg.org)（macOS：`brew install ffmpeg`）。

```bash
python3 render.py output/sample_novel.storyboard.json
```

**或者一条命令跑完整条流水线**（小说 → 剧本 → 分镜 → 成片）：

```bash
python3 pipeline.py examples/sample_novel.txt
python3 pipeline.py examples/sample_novel.txt --aspect 9:16   # 竖屏短剧格式
```

流程：每个镜头调用视频生成模型（默认 `cogvideox-flash`，**免费**）生成视频片段 → 台词配音 → FFmpeg 按顺序合成成片。结果在 `output/render_sample_novel/`：

- `sample_novel.成片.mp4` —— 最终成片
- `clips/`、`audio/`、`build/` —— 各镜头的视频、配音、标准化片段（已缓存，重跑不重复花钱）

常用参数：

```bash
--shots S1-01,S2-03   # 只渲染指定镜头（改完分镜后单独重渲染）
--force               # 忽略缓存全部重做
--skip-video          # 跳过视频生成，只重做配音和拼接
```

**配音后端**（配置文件的 `tts_backend` 字段）：

| 后端 | 说明 |
| --- | --- |
| `glm-tts`（默认） | 智谱 GLM-TTS，音质好、支持多音色，按用量计费；音色按角色在 `voices` 里分配 |
| `macsay` | macOS 系统自带 `say` 命令，**免费离线**，单一音色（`macsay_voice` 字段，默认 `Tingting`） |

**成本提示**：默认模型 `cogvideox-flash` 免费；切换到 `cogvideox-3` 等付费模型前请先了解 [智谱定价](https://open.bigmodel.cn/pricing)。片段缓存意味着只有新镜头才会产生费用。

## 🎭 人物形象一致性与配音

**人物一致性**是 AI 短剧最大的难点：视频模型每次生成都是独立的，同一角色容易在不同镜头里"换脸换人"。novel2drama 的方案：

1. 第一步为每个角色生成一份**锁定外貌**（`appearance` 字段：国籍、年龄、发型、服装），并要求符合小说的时代与地域设定；
2. 第二步把这份外貌**逐字嵌入**每个出现该角色的镜头提示词，漏嵌会被校验器自动补写；
3. **定妆照锁定**（推荐）：开启后（`--lock` 或配置 `lock_characters: true`），渲染前先用图像模型（默认免费的 `cogview-3-flash`）为每个角色生成一张**定妆照**，人物镜头改用**图生视频**——定妆照作为视频首帧，人物形象从第一帧起被锁定。定妆照缓存在 `render_*/characters/`，不满意可删掉后重跑；
4. 你也可以手工修改 `episode.json` 里的 `appearance`（改得更具体、更独特，一致性更好），然后重跑后续步骤。

**配音**与画面匹配：

- `glm-tts` 后端：通过 `voices` 字段给每个角色分配不同音色（`tongtong`/`xiaochen` 等）；
- `macsay` 后端（免费）：macOS 没有男声中文音色，通过 `macsay_voices` 给角色设置 `pitch`（如男角色 `0.78` 降调变声）解决音色单一问题。

**成片完成度**（`config.json` 可配）：

| 功能 | 配置 | 说明 |
| --- | --- | --- |
| 台词字幕 | `subtitles: true` | 台词自动烧录到画面底部（需带字幕滤镜的 ffmpeg，macOS 用 `brew install ffmpeg-full`） |
| 配乐/环境音 | `bgm: auto` | 程序化生成风雪环境音（免版权）；也可填音乐文件路径，`bgm_volume` 控制音量 |
| 画风锁定 | 自动 | 第二步为全片生成统一的 `style` 画风描述，嵌入每个镜头，避免画风漂移 |
| 碎剪节奏 | 自动 | 分镜默认 2~6 秒/镜头，信息量大的对话拆分到多个镜头 |
| 横竖屏 | `aspect: 16:9` / `9:16` | 竖屏用于抖音/快手等短视频平台，支持 `--aspect` 命令行覆盖 |
| 片头片尾 | `intro_outro: true` | 自动生成标题卡和剧终卡，带淡入淡出 |

## 📖 使用自己的小说

```bash
python3 novel2drama.py 我的小说章节.txt -o 我的输出
```

注意：请只使用**你有版权或已获授权**的文本。改编自己的作品没问题；改编他人作品请遵守原作者的授权条款。

## 🗺️ Roadmap

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 第一步 | 小说 → 结构化剧本 | ✅ 已发布 |
| 第二步 | 剧本 → 分镜脚本（镜头描述、时长、画面提示词） | ✅ 已发布 |
| 第三步 | 分镜 → 成片（文生视频 + TTS + 自动剪辑） | ✅ 已发布（免费模型可跑通全流程） |

## 🤝 参与贡献

欢迎 Issue 和 PR！特别是：

- 剧本/分镜格式的改进建议（见 [FORMAT.md](FORMAT.md) 和 [STORYBOARD.md](STORYBOARD.md)）
- 不同模型的提示词调优
- 更多输出格式（Final Draft、Fountain 等）

## 📄 许可证

[MIT](LICENSE)
