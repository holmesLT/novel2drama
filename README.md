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
