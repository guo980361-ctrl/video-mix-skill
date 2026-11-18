---
name: video-mix
description: 混剪视频制作的端到端流水线skill，基于全免费免Key云端接口实现文生图、图生视频、图片对口型、配音与字幕生成，最终用ffmpeg本地合成为带转场和字幕的成片。当用户需要"做混剪视频""AI生成视频""图片转视频""让图片说话""对口型""AI配音""批量生成分镜素材""短视频批量生产"时使用本skill。This skill should be used when creating montage/compilation videos, generating images or video from text, animating still images, lip-syncing photos to audio, or batch-producing short-form video content.
agent_created: true
---

# 混剪视频制作 Skill

用**全免费、免 API Key** 的接口完成「文生图 → 图生视频 → 对口型 → 配音字幕 → 混剪成片」全流程。

## 核心前提：算力决定架构

本 skill 面向**没有独立显卡的机器**（Intel Mac / 集显 / 无 GPU 云主机）。所有重型生成走**云端免费 Space**，本地只做 ffmpeg 后期。

**不要尝试本地跑 ComfyUI / Wan2.2 / Stable Diffusion**——需要 NVIDIA 8GB+ 显存，集显机器必然失败。若用户有独显机器，再考虑本地化（见 `references/local-gpu.md`）。

## ⚠️ 第一个必须知道的事：HF 每日 GPU 额度

Hugging Face ZeroGPU Space 的免费额度是**按天计的，不是按次计的**：

| 账户状态 | 每日 GPU 时长 | 够跑几个镜头 |
|---|---|---|
| 匿名（无 token） | 2 分钟 | 约 3–4 个镜头 |
| 免费账号（绑 token） | 5 分钟 | 约 8–10 个镜头 |
| PRO | 40 分钟 | 足够批量生产 |

**一次图生视频约消耗 20 秒 GPU 时间，一次对口型约 30–60 秒。** 所以匿名状态下做完 3 个镜头基本就见底了。

额度耗尽时报错 `You have exceeded your ZeroGPU quota`，此时：

1. 脚本会自动回退到下一个后端（`ltx` → `wan` → `sdxt`）
2. `sdxt` 是 CPU 常驻后端，**不占 GPU 额度**，画质较低但能保住流水线
3. **要批量生产，必须先配 token**：

```bash
export HF_TOKEN=hf_xxxxxxxx     # https://huggingface.co/settings/tokens 免费创建
```

脚本会自动读取环境变量 `HF_TOKEN`，或 `~/.workbuddy/.hf_token` 文件。

**给用户排任务前，先确认分镜数量 × 每镜耗时是否在额度内。** 超了就是白等。

## 工作流程

### 第 0 步：确认需求

必须明确这四件事，缺一就会做出错误的东西：

| 项 | 默认值 | 说明 |
|---|---|---|
| 画幅 | 1080×1920（竖屏 9:16） | 短视频默认竖屏；横屏用 1920×1080 |
| 分镜数量 | 3–8 个 | 混剪的核心是节奏，不是数量 |
| 主题/文案 | 用户提供 | 没有就先用一句话概括 |
| 是否需要人声 | 是 | 涉及出镜人物则必须做对口型 |

**若用户只给了一个模糊需求**（如"做个混剪视频"），先产出分镜表让用户确认，不要直接开跑——每张图都要等 45 秒限速，返工代价高。

### 第 1 步：写分镜表

用 `assets/shots_template.txt` 的格式，每行一个镜头：

```
a young woman walking down a rainy neon street | shot01-neon-street
close-up of steam rising from a coffee cup | shot02-coffee
```

竖线后是文件名（可选）。生成目录与最终合成的顺序由这个顺序决定。

**⚠️ 生图提示词必须用英文。** 实测 Pollinations 对中文提示词的还原度很差：

| 提示词 | 实际生成 |
|---|---|
| `一只橘猫坐在窗台上看雨，暖黄室内光` | ❌ 空房间，**没有猫** |
| `fluffy orange tabby cat sitting on a windowsill watching rain, warm indoor light` | ✅ 精准还原 |

所以：**用中文跟用户确认分镜内容，但生成时用英文**。需要时先自行把中文分镜译成英文再执行。

### 第 2 步：生成图片

```bash
python scripts/gen_shots.py --shots shots.txt --outdir ./shots --width 1024 --height 1024
```

**关键约束**：Pollinations 免 Key 免费层**每 IP 每 45 秒只能生成 1 张图**，超过就返回 HTTP 402（`FREE_LIMIT`）。脚本已内置 46 秒节流和 402 自动重试。批量生成 8 张约需 6 分钟，**不要并发跑**。

单张补图用 `scripts/gen_image.py`（参数见 `--help`）。

### 第 3 步：图片转视频

```bash
python scripts/gen_video.py --image shots/shot01.jpg \
  --prompt "the woman walks forward, neon reflections ripple in puddles" \
  --out clips/clip01.mp4
```

默认后端 `ltx`（Lightricks/ltx-video-distilled），实测 **17–20 秒出片**。

后端按 `ltx` → `wan` → `sdxt` 顺序自动回退：

| 后端 | 硬件 | 画质 | 耗时 | 占 GPU 额度 |
|---|---|---|---|---|
| `ltx` | zero-a10g | 高 | 17–20 秒 | 是 |
| `wan` | zero-a10g | 高（14B） | 60–120 秒 | 是 |
| `sdxt` | cpu-basic | 中 | **8 分钟以上** | 否 |

**`sdxt` 极慢**（CPU 跑扩散模型），只适合额度彻底耗尽时救急。真正的兜底是用静态图做运镜：

```bash
python scripts/still_to_clip.py --image shots/shot01.jpg \
  --out clips/clip01.mp4 --seconds 4 --motion zoom-in
```

运镜方向可选 `zoom-in`、`zoom-out`、`zoom-in-left`、`zoom-in-right`、`still`。这条路径**完全不消耗任何云端额度**，且本地渲染只要 1–2 秒。

运动幅度要**小**：LTX 对大幅动作容易形变。写 "slight head turn"、"gentle hair movement" 这类微动，不要写 "running toward camera"。

### 第 4 步：对口型（人物出镜时必做）

```bash
python scripts/lipsync.py --image shots/shot01.jpg \
  --audio voice.mp3 --out clips/talking01.mp4
```

- 默认后端 `pragnakalp/Wav2lip-ZeroGPU`，实测 **31–58 秒出片**
- 给已有视频配音加 `--video clip.mp4`
- 更高画质用 `--backend latentsync`（慢，ZeroGPU 排队）
- **注意**：图片对口型只换嘴部，不会生成头动。真人素材建议先生成动态视频再对口型

### 第 5 步：配音与字幕

```bash
python scripts/tts.py --script script.txt --out voice.mp3 --srt voice.srt --voice yunxi
```

一次调用同时产出音频和**时间戳精确对齐的 SRT 字幕**，无需额外 ASR。

音色速查：`xiaoxiao`（女声旁白）、`xiaoyi`（女声活泼）、`yunxi`（男声阳光）、`yunjian`（男声激昂）、`yunyang`（男声播音腔）、`yunxia`（男声卡通）。

### 第 6 步：合成成片

```bash
python scripts/mix.py --clips clips/c1.mp4 clips/c2.mp4 clips/c3.mp4 \
  --out final.mp4 --width 1080 --height 1920 \
  --transition fade --duration 0.5 \
  --subtitles voice.srt --voice voice.mp3 --bgm bgm.mp3
```

- `--transition` 支持 `fade` `fadeblack` `dissolve` `slideleft` `circleopen` 等 9 种，也支持 `cut`（硬切，节奏感强）
- `--bgm` 会用 `sidechaincompress` 自动在有人声时压低 BGM（`--no-duck` 关闭）
- 字幕字号默认按输出宽度 4.5% 自适应；用 `--subtitle-size` 手动覆盖

## 完整流水线示例

```bash
# 1. 分镜表（提示词用英文）
cat > shots.txt <<'EOF'
a young woman walking down a rainy neon street | shot01
close-up of steam rising from a coffee cup | shot02
a woman reading a book by an apartment window | shot03
EOF

# 2. 生图（约 2.5 分钟，45s/张限速）
python scripts/gen_shots.py --shots shots.txt --outdir ./shots

# 3. 图生视频（额度不足时用 still_to_clip 兜底）
for i in 01 02 03; do
  python scripts/gen_video.py --image shots/shot$i.jpg \
    --prompt "subtle natural motion, slight camera push in" \
    --out clips/clip$i.mp4
done
# 兜底方案（零额度消耗）：
# python scripts/still_to_clip.py --image shots/shot01.jpg \
#   --out clips/clip01.mp4 --seconds 4 --motion zoom-in

# 4. 配音 + 字幕
python scripts/tts.py --script script.txt --out voice.mp3 --srt voice.srt --voice yunxi

# 5. 对口型（仅人物镜头需要）
python scripts/lipsync.py --image shots/shot01.jpg --audio voice.mp3 --out clips/lip01.mp4

# 6. 合成
python scripts/mix.py --clips clips/clip01.mp4 clips/clip02.mp4 clips/clip03.mp4 \
  --out final.mp4 --width 1080 --height 1920 --transition fade \
  --subtitles voice.srt --voice voice.mp3
```

## 踩坑清单（已实测确认）

这些坑都真实撞过，不要重复踩：

1. **Pollinations 视频接口需要 API Key**。`gen.pollinations.ai/video/` 无 Key 返回 401，`image.pollinations.ai` 的旧免 Key 路径在部分网络下会返回 402。本 skill 的生图走 `image.pollinations.ai/prompt/`（实测可用），视频走 HF Space，不走 Pollinations。
2. **生图限速是每 45 秒一张**（不是并发限制，是串行配额）。批量任务务必串行。
3. **edge-tts 7.x 的时间戳单位是 100 纳秒**（1 秒 = 10,000,000），不是微秒。按微秒换算会让 4.6 秒的音频变成 45 秒字幕。
4. **libass 对 SRT 默认 PlayResX=384**，字号会被放大约 `width/384` 倍。`mix.py` 已改为先转 ASS 并显式声明 PlayResX/PlayResY，字号才是真实像素值。
5. **ZeroGPU Space 冷启动会超时**。gradio_client 默认超时太短，脚本已加长超时 + 指数退避重试（3 次）。
6. **ffmpeg 滤镜链的标签必须严格串联**。`[v1][vout]` 这种写法会报 "Error parsing filterchain"，必须由 `;` 分隔成独立 filter。
7. **本机无 brew**。ffmpeg 用 `pip install imageio-ffmpeg` 的静态二进制，脚本会自动定位，不要要求用户装 brew。
8. **相对输出路径按 CWD 解析**。`--out video-mix-out/demo/a.mp4` 落在 CWD 下，不会变成 `video-mix-out/video-mix-out/...`。
9. **中文提示词还原度差**。生图一律用英文提示词（见第 1 步）。
10. **语音比画面长时会截断配音**。`mix.py` 已用 `tpad` 克隆末帧补齐视频时长，并按目标时长裁剪输出，避免 `-shortest` 砍掉旁白尾句。
11. **ZeroGPU 额度是按天计的**。同一天内跑满约 3–4 个镜头就会耗尽，必须提前配 `HF_TOKEN`。

## 资源消耗预期

| 步骤 | 耗时 | 外部依赖 |
|---|---|---|
| 单张生图 | 9–45 秒 | Pollinations（45s 配额） |
| 图生视频 | 17–25 秒 | HF LTX Space |
| 图片对口型 | 31–60 秒 | HF Wav2Lip Space |
| 配音 | 2–5 秒 | Edge TTS |
| 混剪合成 | 5–20 秒 | 本地 ffmpeg |

## 扩展与替代

- **本地 GPU 机器**：见 `references/local-gpu.md`，可改用 ComfyUI + Wan2.2 + MuseTalk 完全离线
- **更多免费后端与实测记录**：见 `references/free-backends.md`
- **商用授权注意事项**：见 `references/licensing.md`

## 脚本清单

| 脚本 | 作用 |
|---|---|
| `scripts/gen_image.py` | 单张文生图 |
| `scripts/gen_shots.py` | 按分镜表批量生图（含限速节流） |
| `scripts/gen_video.py` | 图生视频（ltx / wan / sdxt 三级回退） |
| `scripts/still_to_clip.py` | 静态图 → 运镜片段（零额度消耗的兜底） |
| `scripts/lipsync.py` | 图片或视频对口型 |
| `scripts/tts.py` | 配音 + 同步字幕 |
| `scripts/mix.py` | 混剪合成：转场 + 字幕 + BGM 闪避 |
| `scripts/vmix_common.py` | 公共工具（ffmpeg 定位、依赖安装、重试、额度提示） |
