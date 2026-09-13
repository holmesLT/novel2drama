# 分镜数据格式（v0.1）

第二步（剧本 → 分镜）的输出格式，也是第三步（分镜 → 成片）的输入。
文件名约定为 `<原名>.storyboard.json`，由 `storyboard.py` 生成。

## 顶层字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `title` | string | 继承自剧本 |
| `genre` | string | 继承自剧本 |
| `style` | string | 全片视觉风格提示，会拼进每个镜头的视频提示词 |
| `characters` | array | 同剧本角色表；`desc` 会作为角色一致性参考 |
| `scenes` | array | 场景列表，每个场景新增 `shots` 字段 |

## scenes 元素

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `scene_id` | number | 场景编号（与剧本一致） |
| `heading` | string | 继承自剧本：`内景/外景 - 地点 - 日/夜` |
| `summary` | string | 继承自剧本 |
| `shots` | array | 镜头列表，按播放顺序排列 |

## shots 元素

```json
{
  "shot_id": "S1-01",
  "duration_sec": 5,
  "shot_type": "特写",
  "description": "雨点砸在书店褪色的招牌上，霓虹灯在积水里晃动",
  "video_prompt": "特写镜头，雨夜，雨点砸在老旧书店的木质招牌上，招牌字体褪色，地面积水反射灯光，电影感，浅景深",
  "dialogue": [
    {"character": "林晚", "text": "我要找一本书。", "emotion": "冷静"}
  ]
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `shot_id` | string | 镜头编号，格式 `S{场景号}-{镜头序号}`，如 `S1-01` |
| `duration_sec` | number | 预计时长（秒），建议 3~8 秒（主流文生视频 API 单次生成的常见上限区间） |
| `shot_type` | string | 景别：远景/全景/中景/近景/特写，或运镜描述（推、拉、摇、跟） |
| `description` | string | 中文画面描述（给人看的） |
| `video_prompt` | string | 自包含的文生视频提示词（给模型用的）：把景别、主体、动作、环境、光线、风格整合成一段话，不依赖上下文 |
| `dialogue` | array | 本镜头内发生的台词（给 TTS 配音用），结构同剧本的台词节拍；无台词则为空数组 |

## 设计原则

1. **`video_prompt` 自包含**：不依赖其他镜头的上下文，单独喂给任何文生视频模型都能生成。
2. **台词跟着镜头走**：配音时按镜头拼接即可对上画面时长。
3. **时长可控**：`duration_sec` 之和即本集预计片长，第三步据此做成本预估。
