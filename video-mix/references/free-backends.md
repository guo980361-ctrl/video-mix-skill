# 免费后端实测记录

所有结论均为 **2026-11-18 在本机实测**，不是从文档推断的。

## 测试环境

- macOS 15.5，Intel Core i5-8257U，8GB RAM，Intel Iris Plus 645（1.5GB 显存）
- Python 3.13.12，ffmpeg 9.0.2（pip 静态二进制）
- 无 brew，无 Homebrew，无本地 GPU

---

## 零、结论速查：选哪一层

| 层 | 后端 | key | 天花板 | 水印 |
|---|---|---|---|---|
| ① 长期 | `gen.pollinations.ai` | 需要（免费注册） | 账户余额，可靠 quest 补 | **无** |
| ② 每日 | HF ZeroGPU Space | 可选 | 2–40 分钟 GPU/天 | 无 |
| ③ 零消耗 | 本地 ffmpeg 运镜 | 不需要 | 无限制 | 无 |

**想长期稳定出片 → 配 ① 的 key。** 一次注册，之后 75 个图片模型 + 25 个视频模型可用，余额可通过 quest 免费补充。

---

## 一、可用（实测通过）

### ⭐ 文生图（长期层）— Pollinations Key 端点

```
GET https://gen.pollinations.ai/image/<urlencoded prompt>
    ?model=tongyi-mai/z-image-turbo&width=1024&height=1024&nologo=true
Authorization: Bearer <pk_xxx>
```

| 项 | 实测结果 |
|---|---|
| HTTP（带 key） | 200 |
| HTTP（不带 key） | **401**，`A valid API key is required` |
| 水印 | **无**（这是相对匿名端点最大的优势） |
| 限速 | 无 45 秒限制 |
| 计费 | 扣账户余额 |

**绕过 key 的尝试全部失败**（实测）：`key=free`、`referrer=workflow`、`private=true`、不带任何参数——一律 401。不要再试参数组合，直接注册。

注册地址：https://enter.pollinations.ai/keys （新账号自动到账 0.25 USD）

**可用图片模型 75 个**，实测确认可用的一批：

```
tongyi-mai/z-image-turbo
black-forest-labs/flux.1-schnell
black-forest-labs/flux-3-image-klein
qwen/qwen-image-2.1-turbo
bytedance/seedream-5.0-flash
google/gemini-nano-banana-2.1
```

完整清单：`GET https://gen.pollinations.ai/image/models?reliability=reliable&source=official`

### ⭐ 图生视频（长期层）— Pollinations Key 端点

```
GET https://gen.pollinations.ai/video/<urlencoded prompt>
    ?model=bytedance/seedance-2.0-fast&duration=4&image=data:image/jpeg;base64,...
Authorization: Bearer <pk_xxx>
```

异步模式：首次响应返回 job id，轮询 `GET /v1/jobs/{id}/result` 取结果。

**25 个视频模型，全部支持 `start_frame`（图生视频）**：

```
google/veo-3.1-fast          bytedance/seedance-2.5
bytedance/seedance-2.0       bytedance/seedance-2.0-fast
bytedance/seedance-2.0-mini  kwaivgi/kling-v3.0-std
alibaba/wan-3.0              alibaba/wan-2.7
alibaba/wan-2.2-fast         black-forest-labs/flux-3-max
x-ai/grok-imagine-video-1.5   x-ai/grok-imagine-video-1.5-lite
mirelo/sfx-2.0               ...
```

完整清单：`GET https://gen.pollinations.ai/video/models?reliability=reliable&source=official`

**这一层不消耗任何 HF ZeroGPU 额度**，是解决每日额度瓶颈的关键。

### ⭐ 免费额度补充机制 — Quest

`GET https://gen.pollinations.ai/quests/catalog`（公开端点，免 key 可读）

实测：**26 个零成本 quest 合计 132.75 USD**，另有 4 个需先充值/付费的（已单独标记，不要误当成免费）。

零成本 quest 包括：Discord 等级（+1/+2/+5）、提交 PR（+5）、做一个 agent skill（+10）、邀请好友（+2）、创建 key（+0.25）、用一次视频模型（+1）……

**这是真正意义上"长期免费"的机制**：余额用完了，去 https://pollinations.ai/community 做几个 quest 就补回来了。

### 文生图（零门槛层）— Pollinations 匿名端点

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
| `video.pollinations.ai/video/` | **连接失败（HTTP 000）** | 域名已不可达 |
| `image.pollinations.ai/prompt/` 带 `nologo=true` | 水印仍在 | 参数免费层不生效 |
| Pollinations 旧免 Key 批量生图 | **402 FREE_LIMIT** | 45 秒配额硬限制 |
| `gen.pollinations.ai` 免 Key | **401 UNAUTHORIZED** | 必须带 key（这是设计如此，不是 bug） |
| `gen.pollinations.ai` + `key=free` / `referrer=` | **401** | 实测各种绕过参数组合全部失败 |
| `image.pollinations.ai` 的 `kontext` / `gptimage` 模型 | **403 / 402** | 这些是付费档模型 |

> **更正**：早期记录里写「`gen.pollinations.ai/video/` 401」被理解为"视频接口已废弃"。实际是**整个 gen 端点都需要 key**，配了 key 后视频功能完全正常，而且有 25 个模型。这是本 skill 最重要的更新。

---

## 三、死路（实测排除，不要再试）

### ❌ Intel Mac 上本地跑扩散模型

| 尝试 | 结果 |
|---|---|
| `pip install torch` | `ERROR: Could not find a version that satisfies the requirement torch (from versions: none)` |
| `pip install torch==2.2.2 --index-url https://pypi.org/simple` | 同样报 "from versions: none" |
| 查询 PyPI JSON API | torch 2.2.2 只有 cp38/39/310/311/312 的 macos x86_64 wheel，**没有 cp313** |

**根因**：PyTorch 从 2.3 起停止发布 x86_64 macOS wheel（Apple Silicon 走 arm64）。本机 Python 是 3.13，唯一能用的是 torch 2.2.2 + Python 3.12，而 WorkBuddy 内置 Python 是 3.13。

即使装上，8GB 内存跑 FLUX 1.2B 也需要 5–15 分钟/张，且只有 36GB 磁盘。**这条路在 Intel Mac 上是死的。**

### ❌ HF 上的免费常驻 GPU

扫描 123 个 i2v 相关 Space，结果：

- `cpu-basic`（CPU 常驻，不占 ZeroGPU 额度）：`waloneai/SDXT-Image-To-Video`、`pm6six/Image_to_video`、`abonline/stable-video-diffusion-img2vid-xt`、`AiGuaratuba/Stable-Video-Diffusion-Img2Vid-10seconds`
- `zero-*`（占 ZeroGPU 额度）：其余全部
- **常驻付费 GPU（l4x1 / a10g-small / t4-small）：0 个**

**实测性能**：CPU 后端跑扩散模型慢到不可用——`SDXT` 跑了 18 分钟仍未出片，已终止；`stable-video-diffusion-img2vid-xt` 同样超时。

**结论：真动态只有两条路**——Pollinations key 层（消耗账户余额）或 HF ZeroGPU（消耗每日 GPU 额度）。CPU Space 只能应急验证。

---

## 四、其他候选（未实测或未启用）

### HF ZeroGPU 每日额度

| 账户类型 | 每日 GPU 配额 |
|---|---|
| 未认证 | 2 分钟 |
| 免费账户 | 5 分钟 |
| PRO | 40 分钟 |

**额度重置机制**（官方文档）：**在首次使用 GPU 后整 24 小时重置**，不是自然日零点了。所以"今天用完了"要等到"昨天那个时刻"才恢复，不必非等到午夜。

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

**排期建议**：做 N 个镜头，先估算 `(N × 25秒 + 对口型镜头数 × 45秒) ≤ 120秒`（匿名）或 `≤ 300秒`（免费账号）。超出要么等重置，要么用 `still_to_clip.py` 做零额度兜底。

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
