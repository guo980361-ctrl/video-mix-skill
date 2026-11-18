# 免费后端实测记录

所有结论均为 **2026-11-18 在本机实测**，不是从文档推断的。

## 测试环境

- macOS 15.5，Intel Core i5-8257U，8GB RAM，Intel Iris Plus 645（1.5GB 显存）
- Python 3.13.12，ffmpeg 9.0.2（pip 静态二进制）
- 无 brew，无 Homebrew，无本地 GPU

---

## 一、可用（实测通过）

### 文生图 — Pollinations 免 Key

```
GET https://image.pollinations.ai/prompt/<urlencoded prompt>
    ?width=1024&height=1024&model=flux&nologo=true
```

| 项 | 实测结果 |
|---|---|
| HTTP | 200 |
| 耗时 | 1.6 秒（512×512）～ 9.3 秒（1024×1024） |
| 免 Key | 是 |
| 画质 | 高，flux 模型细节丰富 |
| 输出 | JPEG |

**硬限制**：每 IP 每 45 秒 1 张。超限返回：

```json
{"status":402,"success":false,"error":{"code":"FREE_LIMIT",
 "message":"Free limit reached: 1 image every 45 seconds per IP. Retry after 5s, or get unlimited images with a pollen key at https://enter.pollinations.ai/?ref=classic"}}
```

**水印**：即使传 `nologo=true`，实测右下角仍有 `pollinations.ai` 水印。该参数在免费层不生效。

可用模型：`flux`、`sana`、`zimage`、`turbo`（实测 `flux` 与 `zimage` 正常）。

### ⚠️ 中文提示词还原度差（实测对比）

| 提示词 | 生成结果 |
|---|---|
| `一只橘猫坐在窗台上看雨，暖黄室内光，胶片感` | ❌ 空房间，**完全没有猫** |
| `a fluffy orange tabby cat sitting on a windowsill watching rain, warm indoor light` | ✅ 精准还原橘猫+窗台+雨+暖光 |

`清晨的海岸，浪花拍打礁石` → 生成荷花水面；`秋日林间小路` → 生成动漫女孩。

**结论：生图提示词一律用英文。** 中文只用于和用户确认分镜意图。

### 生图水印

即使传 `nologo=true`，实测右下角仍有 `pollinations.ai` 水印。该参数在免费层不生效。

### 图生视频 — HF Space `Lightricks/ltx-video-distilled`

| 项 | 实测结果 |
|---|---|
| 端点 | `/image_to_video` |
| 硬件 | `zero-a10g`（ZeroGPU） |
| 耗时 | 17.3–19.8 秒 |
| 输出 | 704×512, 30fps, H.264, 1.9 秒 |
| 免 Key | 是（gradio_client 匿名访问） |

必需参数已带默认值：`prompt` `negative_prompt` `input_image_filepath` `mode` `duration_ui` `ui_frames_to_use` `randomize_seed` `ui_guidance_scale` `improve_texture_flag` `height_ui` `width_ui`。

实测画面：人物身份保持一致，出现眨眼、转头、微笑等自然微动，无形变。

### 图片对口型 — HF Space `pragnakalp/Wav2lip-ZeroGPU`

| 项 | 实测结果 |
|---|---|
| 端点 | `/run_infrence(input_image, input_audio)` |
| 硬件 | `cpu-basic`（常驻，不排队） |
| 耗时 | 31.1 秒（4 秒音频）～ 58.0 秒（7 秒音频） |
| 输出 | 768×768, 25fps, H.264 + AAC |
| 免 Key | 是 |

实测画面：抽帧确认嘴型确实随音节变化（张嘴 / 闭嘴 / 露齿），面部边缘干净，无明显瑕疵。

### 视频对口型 — HF Space `fffiloni/LatentSync`

- 端点：`/generate_lip_sync_video(input_video_path, input_audio_path)`
- 硬件：`zero-a10g`，**需长超时**——冷启动时 gradio_client 默认超时会抛 `ReadTimeout`，用 900s 超时 + 重试可过
- 画质优于 Wav2Lip，速度更慢

### 视频对口型 — HF Space `manavisrani07/gradio-lipsync-wav2lip`

- 端点：`/generate(video, audio, checkpoint, no_smooth, resize_factor, pad_top, pad_bottom, pad_left)`
- 硬件：`cpu-basic`
- `checkpoint` 取值 `wav2lip` 或 `wav2lip_gan`

### 配音 — Microsoft Edge TTS

```bash
pip install edge-tts
edge-tts --voice zh-CN-YunxiNeural --text "..." --write-media out.mp3
```

| 项 | 实测结果 |
|---|---|
| 免 Key | 是 |
| 中文音色 | 6 个（见下） |
| 耗时 | 2–5 秒 |
| 附带 | 句子级时间戳元数据 |

可用中文音色：`zh-CN-XiaoxiaoNeural`（女·温暖）、`zh-CN-XiaoyiNeural`（女·活泼）、`zh-CN-YunxiNeural`（男·阳光）、`zh-CN-YunjianNeural`（男·激昂）、`zh-CN-YunyangNeural`（男·播音）、`zh-CN-YunxiaNeural`（男·卡通）。

**关键格式细节**：edge-tts 7.x 的 `Communicate.save()` 写出的是 **JSONL**，每行形如：

```json
{"type": "SentenceBoundary", "offset": 1000000, "duration": 25625000, "text": "你好"}
```

`offset` / `duration` 单位是 **100 纳秒**（10,000,000 = 1 秒），不是微秒。实测：4.63 秒音频的末条字幕结束于 45750000 → 4.575 秒，吻合。按微秒换算会得到 45 秒的错误时间轴。

---

## 二、已失效（不要使用）

| 服务 | 实测结果 | 说明 |
|---|---|---|
| `gen.pollinations.ai/video/` | **401 UNAUTHORIZED** | 视频接口已全面要求 API Key |
| `gen.pollinations.ai/image/` 免 Key | 返回图片二进制但需 Key | 无 Key 时 401 |
| `video.pollinations.ai/video/` | **连接失败（HTTP 000）** | 域名已不可达 |
| `image.pollinations.ai/prompt/` 带 `nologo=true` | 水印仍在 | 参数免费层不生效 |
| Pollinations 旧免 Key 批量生图 | **402 FREE_LIMIT** | 45 秒配额硬限制 |

---

## 三、其他候选（未实测或未启用）

### HF ZeroGPU 每日额度

| 账户类型 | 每日 GPU 配额 |
|---|---|
| 未认证 | 2 分钟 |
| 免费账户 | 5 分钟 |
| PRO | 40 分钟 |

**实测耗尽记录**：匿名状态下跑完 3 个图生视频请求后，第 4 个开始返回：

```
AppError: You have exceeded your ZeroGPU quota (120s requested vs. -60s left).
Try again in 0:00:00. Authenticate with a Hugging Face token for more quota
```

以及：

```
AppError: You have exceeded your ZeroGPU runs limit.
Authenticate with a Hugging Face token for more quota
```

**排期建议**：做 N 个镜头，先估算 `(N × 25秒 + 对口型镜头数 × 45秒) ≤ 120秒`（匿名）或 `≤ 300秒`（免费账号）。超出要么等第二天，要么用 `still_to_clip.py` 做零额度兜底。

**Token 配置**（免费账号即可）：

```bash
export HF_TOKEN=hf_xxxxxxxx          # https://huggingface.co/settings/tokens
# 或
echo "hf_xxxxxxxx" > ~/.workbuddy/.hf_token
```

脚本的 `hf_token()` 会自动读取这两个位置。

### 已探测可用的 Space 清单

| Space | 硬件 | 用途 | 实测 |
|---|---|---|---|
| `Lightricks/ltx-video-distilled` | zero-a10g | 图生视频（**默认后端**） | ✅ 17–20 秒 |
| `pragnakalp/Wav2lip-ZeroGPU` | cpu-basic | 图片对口型（**默认后端**） | ✅ 31–58 秒 |
| `fffiloni/LatentSync` | zero-a10g | 视频对口型，高画质 | ✅ 端点可用 |
| `manavisrani07/gradio-lipsync-wav2lip` | cpu-basic | 视频对口型 | ✅ 端点可用 |
| `Upsampler/wan-2-2-14b-image-to-video` | zero-a10g | 图生视频备选（14B，支持首尾帧） | ✅ 端点可用 |
| `waloneai/SDXT-Image-To-Video` | cpu-basic | 图生视频兜底，**不占额度** | ⚠️ 18 分钟未出片 |
| `DeepRat/LTX-Video-ZeroGPU-Optimized` | — | LTX 优化版 | 未实测 |

**空间不足时的最优解**：`still_to_clip.py` 本地做 Ken Burns 运镜，1–2 秒出片，零额度、零网络依赖。

### 本地部署路线（需要 NVIDIA 显卡）

若用户机器有独显，可完全离线运行：

| 环节 | 模型 | 显存 | 许可 |
|---|---|---|---|
| 图生视频 | Wan2.2 TI2V-5B | 8–13GB（GGUF 量化 6–10GB） | Apache 2.0 |
| 视频生成 | Wan2.2 T2V-A14B / I2V-A14B | 24–30GB | Apache 2.0 |
| 对口型 | MuseTalk 1.5 | 8GB+ | MIT |
| 对口型 | LatentSync | 12GB+ | Apache 2.0 |
| 低显存图生视频 | FramePack | 6GB | 开放权重 |
| 长视频 | LongCat-Video | — | MIT |

细节见 `local-gpu.md`。

### 付费但有免费额度的备选

- **阿里云百炼**：新用户 7000 万 tokens，90 天有效。万相 3.0 视频生成有 30 秒免费额度。
- **Comfy Cloud**：免费层每月 400 积分，另有 5 次免费 GPU 运行。
- **ComfyICU**：新账号 5k 免费积分，按 GPU 秒计费。
