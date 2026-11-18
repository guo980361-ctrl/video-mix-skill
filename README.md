# video-mix — 混剪视频制作 Skill

用**全免费、免 API Key** 的接口完成「文生图 → 图生视频 → 对口型 → 配音字幕 → 混剪成片」全流程。

为 **WorkBuddy / Claude Code 等 Agent 运行时**设计的技能包，也可作为独立命令行工具使用。

---

## 为什么是这个架构

大多数"免费 AI 视频"教程都在讲本地跑 ComfyUI / Wan2.2 / Stable Diffusion——**前提是你有 NVIDIA 独显 8GB+ 显存**。

本 skill 不做这个假设。它的默认运行环境是**没有独立显卡的机器**（Intel Mac、集显、云主机），因此：

- 所有重型生成（生图、图生视频、对口型）走**云端免费 Space / 免 Key API**
- 本地只做 **ffmpeg 后期**（拼接、转场、字幕、混音）
- 零 GPU 依赖、零 brew 依赖，`pip install` 即可运行

---

## 实测数据

全部为 2026-11-18 在 Intel Mac（8GB RAM / 1.5GB 显存）上的真实测量，不是文档推断。

| 能力 | 后端 | 耗时 | 免 Key |
|---|---|---|---|
| 文生图 | Pollinations `image.pollinations.ai/prompt/` | 1.6–9.3 秒 | ✅ |
| 图生视频 | HF `Lightricks/ltx-video-distilled` | 17–20 秒 | ✅ |
| 图生视频（备选） | HF `Upsampler/wan-2-2-14b-image-to-video` | 60–120 秒 | ✅ |
| 图片对口型 | HF `pragnakalp/Wav2lip-ZeroGPU` | 31–58 秒 | ✅ |
| 视频对口型 | HF `fffiloni/LatentSync` | 视排队情况 | ✅ |
| 配音 + 字幕 | Microsoft Edge TTS | 2–5 秒 | ✅ |
| 混剪合成 | 本地 ffmpeg | 5–20 秒 | ✅ |

---

## 安装

```bash
git clone https://github.com/guo980361-ctrl/video-mix-skill.git
cd video-mix-skill/video-mix
```

把 `video-mix/` 整个目录放进 Agent 的 skills 目录即可被自动发现：

| 运行时 | 安装位置 |
|---|---|
| WorkBuddy | `~/.workbuddy/skills/video-mix/` |
| Claude Code | `~/.claude/skills/video-mix/` |

依赖（脚本会自动引导安装，也可手动装）：

```bash
pip install edge-tts imageio-ffmpeg requests gradio_client
```

无需 ffmpeg、brew、GPU。

---

## 快速开始

以下命令均在 `video-mix/` 目录内执行（即 `cd video-mix-skill/video-mix`）。

```bash
pip install edge-tts imageio-ffmpeg requests gradio_client
```

### 1. 写分镜表（提示词用**英文**）

```
a fluffy orange tabby cat sitting on a windowsill watching rain, warm indoor light | shot01-cat
a rocky coastline with waves crashing against rocks at dawn, cool blue tones | shot02-coast
```

### 2. 批量生图

```bash
python scripts/gen_shots.py --shots shots.txt --outdir ./shots
```

### 3. 图生视频

```bash
python scripts/gen_video.py --image shots/shot01-cat.jpg \
  --prompt "the cat blinks, rain streaks the window" --out clips/clip01.mp4
```

### 4. 配音 + 自动同步字幕

```bash
python scripts/tts.py --script script.txt --out voice.mp3 --srt voice.srt --voice yunxi
```

### 5. 图片对口型

```bash
python scripts/lipsync.py --image shots/shot01-cat.jpg --audio voice.mp3 --out clips/lip01.mp4
```

### 6. 合成成片

```bash
python scripts/mix.py --clips clips/clip01.mp4 clips/clip02.mp4 \
  --out final.mp4 --width 1080 --height 1920 --transition fade \
  --subtitles voice.srt --voice voice.mp3 --bgm bgm.mp3
```

---

## 脚本

| 脚本 | 作用 |
|---|---|
| `scripts/gen_image.py` | 单张文生图 |
| `scripts/gen_shots.py` | 按分镜表批量生图（含 45s 限速节流与 402 自动重试） |
| `scripts/gen_video.py` | 图生视频（ltx → wan → sdxt 三级自动回退） |
| `scripts/still_to_clip.py` | 静态图 → 运镜片段（零额度消耗的兜底） |
| `scripts/lipsync.py` | 图片 / 视频对口型 |
| `scripts/tts.py` | 配音 + 时间戳精确对齐的 SRT 字幕 |
| `scripts/mix.py` | 混剪合成：转场 + 字幕烧录 + BGM 自动闪避 |
| `scripts/vmix_common.py` | 公共工具（ffmpeg 定位、依赖安装、重试、额度提示） |

---

## 踩坑清单

这些是真实撞过的坑，README 里都有实测对比：

1. **Pollinations 视频接口要 Key**。`gen.pollinations.ai/video/` 无 Key 返回 401；`video.pollinations.ai` 域名已不可达。
2. **生图限速每 45 秒 1 张**（不是并发限制，是串行配额）。超限返回 HTTP 402 `FREE_LIMIT`。
3. **edge-tts 7.x 时间戳单位是 100 纳秒**（1 秒 = 10,000,000），不是微秒。按微秒算会让 4.6 秒音频变成 45 秒字幕。
4. **libass 对 SRT 默认 PlayResX=384**，字号被放大约 `width/384` 倍。必须先转 ASS 并显式声明 PlayRes。
5. **ZeroGPU 额度按天计**：匿名 2 分钟/天，免费账号 5 分钟/天。约 3–4 个镜头就见底。
6. **ffmpeg 滤镜链标签必须严格串联**，`[v1][vout]` 会直接报 filterchain 解析错误。
7. **中文提示词还原度极差**。`一只橘猫坐在窗台上看雨` → 空房间（无猫）；英文则精准还原。
8. **语音长于画面时 `-shortest` 会砍掉旁白尾句**，需用 `tpad` 克隆末帧补齐。

---

## 免费额度的真实边界

| 服务 | 额度 | 够用吗 |
|---|---|---|
| Pollinations 生图 | 每 IP 每 45 秒 1 张 | 单条够，批量要等 |
| HF ZeroGPU（匿名） | 2 分钟 GPU/天 | 约 3–4 个镜头 |
| HF ZeroGPU（免费账号 + token） | 5 分钟 GPU/天 | 约 8–10 个镜头 |
| Edge TTS | 无明确限制 | 充足 |
| ffmpeg | 本地 | 无限制 |

批量生产请配置 token：

```bash
export HF_TOKEN=hf_xxxxxxxx   # https://huggingface.co/settings/tokens
```

额度耗尽时，`still_to_clip.py` 提供完全零消耗的本地兜底。

---

## 商用授权

**免费 ≠ 无限制商用。** 详见 `references/licensing.md`，摘要：

- Pollinations 输出**带水印**（`nologo=true` 无效），商用授权不明确
- LTX-Video 为社区许可（年营收 <1000 万美元免费）
- Wav2Lip 原始仓库偏个人非商用；MuseTalk（MIT）、LatentSync（Apache 2.0）更友好
- Edge TTS 属非授权访问，商用建议换讯飞或本地 CosyVoice（MIT）
- **给别人的脸做对口型必须获得肖像权授权**

---

## 目录结构

仓库内的 `video-mix/` 即 skill 包本体：

```
video-mix-skill/
├── README.md
├── LICENSE
└── video-mix/
    ├── SKILL.md                      # Agent 使用说明（含完整工作流与踩坑清单）
    ├── scripts/                      # 7 个可执行脚本 + 公共模块
    ├── references/
    │   ├── free-backends.md          # 所有后端的实测记录、失效清单、Space 清单
    │   ├── local-gpu.md              # 有独显时的本地部署路线
    │   └── licensing.md              # 商用授权与合规清单
    └── assets/
        └── shots_template.txt        # 分镜表模板
```

---

## 相关开源项目

本 skill 是对以下项目的编排封装（均不 fork，直接调用其公开 API）：

- [Lightricks/LTX-Video](https://github.com/Lightricks/LTX-Video) — 图生视频
- [pragnakalp/Wav2lip-ZeroGPU](https://github.com/pragnakalp/Wav2lip-ZeroGPU) — 对口型
- [bytedance/LatentSync](https://github.com/bytedance/LatentSync) — 对口型（高画质）
- [TMElyralab/MuseTalk](https://github.com/TMElyralab/MuseTalk) — 对口型（实时）
- [rany2/edge-tts](https://github.com/rany2/edge-tts) — TTS
- [FFmpeg/FFmpeg](https://github.com/FFmpeg/FFmpeg) — 后期合成

## License

MIT
