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

## 免额度的三层结构

"免费"在这里有三个不同的含义，选错层级会浪费大量时间：

| 层 | 后端 | 天花板 | 需要 key | 水印 |
|---|---|---|---|---|
| **① 长期可用** | `gen.pollinations.ai` | 账户余额（可免费赚取） | ✅ 免费注册 | **无** |
| **② 每日额度** | HF ZeroGPU Space | 2–40 分钟 GPU/天 | ❌ 可选 | 无 |
| **③ 零消耗** | 本地 ffmpeg 运镜 | 无限制 | ❌ | 无 |

### ① 长期可用层（推荐）

注册一次 Pollinations key，长期使用，**无水印、无 45 秒限速、不占 HF 额度**：

```bash
# 1. 免费注册并创建 key：https://enter.pollinations.ai/keys
# 2. 保存（写入 ~/.workbuddy/.pollinations_key，权限 600）
python scripts/polli_account.py key --set pk_xxxxxxxx

# 3. 随时查看余额与用量
python scripts/polli_account.py balance
python scripts/polli_account.py usage
```

新账号自动到账 **0.25 USD**。余额用完可以免费赚回来——quest 系统里有 **26 个零成本任务合计 132.75 USD**：

```bash
python scripts/polli_account.py quests
```

这一层能用 **75 个图片模型** 和 **25 个视频模型**（Veo 3.1、Seedance 2.x、Kling 3.0、Wan 3.0…），全部支持图生视频。

### ② 每日额度层（无 key 兜底）

| 账户状态 | 每日 GPU 时长 | 够跑几个镜头 |
|---|---|---|
| 匿名 | 2 分钟 | 约 3–4 个 |
| 免费账号（绑 token） | 5 分钟 | 约 8–10 个 |
| PRO（$9/月） | 40 分钟 | 批量生产 |

额度在**首次使用 GPU 后整 24 小时重置**，不是自然日零点。

### ③ 零消耗层（永不失败）

`scripts/still_to_clip.py` 本地 Ken Burns 运镜，**零额度、零网络、1–2 秒出片**。

---

## 省额度的三个习惯

1. **配 key**（①层）——否则只能挤在 2 分钟/天的额度里
2. **用缓存**——内置内容寻址缓存，同一 prompt + 参数**只付一次钱**。实测：批量生图首次 62.6 秒，重跑 3.3 秒全部命中缓存
3. **先查余额**——批量任务前跑 `polli_account.py balance`

---

## 实测数据

全部为 2026-11-18 在 Intel Mac（8GB RAM / 1.5GB 显存）上的真实测量，不是文档推断。

| 能力 | 后端 | 耗时 | 需要 key |
|---|---|---|---|
| 文生图 | Pollinations keyed `gen.pollinations.ai` | 2–10 秒 | ✅（无水印） |
| 文生图 | Pollinations 匿名 `image.pollinations.ai` | 1.6–9.3 秒 | ❌（有水印，45s/张） |
| 图生视频 | Pollinations keyed（25 个模型） | 30–120 秒 | ✅（不占 HF 额度） |
| 图生视频 | HF `Lightricks/ltx-video-distilled` | 17–20 秒 | ❌（占每日额度） |
| 图片对口型 | HF `pragnakalp/Wav2lip-ZeroGPU` | 31–58 秒 | ❌ |
| 视频对口型 | HF `fffiloni/LatentSync` | 视排队情况 | ❌ |
| 配音 + 字幕 | Microsoft Edge TTS | 2–5 秒 | ❌ |
| 混剪合成 | 本地 ffmpeg | 5–20 秒 | ❌ |
| 本地运镜 | 本地 ffmpeg Ken Burns | 1–2 秒 | ❌ |

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
pip install edge-tts imageio-ffmpeg requests gradio_client numpy pillow opencv-python-headless
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

### 7. 接入剪映专业版（可选，继续精修）

成片一步就出。想**在剪映里继续调**（换字体、加花字、做音效、改节奏），
把素材直接变成可编辑的剪映工程：

```bash
# --install 直接写进剪映草稿目录，打开剪映首页就能看到
python scripts/jianying_draft.py \
  --clips clips/clip01.mp4 clips/clip02.mp4 clips/clip03.mp4 \
  --voice voice.mp3 --bgm bgm.mp3 --subtitles voice.srt \
  --name "我的混剪" --install
```

打开后已经排好：

| 轨道 | 内容 |
|---|---|
| 主视频轨 | 每个片段独立 segment，转场是**剪映原生转场**（可改时长/样式） |
| 字幕轨 | 真正的文本片段，能一键换字体/描边/花字——**不是烧进画面的** |
| 旁白轨 / BGM 轨 | 分轨摆放，BGM 已按旁白自动做好音量闪避关键帧 |

> 💡 工具会**同时写 `draft_info.json` 和 `draft_content.json`**。剪映 6.0 起只认
> `draft_info.json`，用旧名字会报"草稿内容已损坏"，两个都写就不用管版本。

**读写已有的加密草稿**（剪映 6.0 起草稿是加密的）：

```bash
pip install pycryptodome
python scripts/jianying_crypto.py -p ~/Movies/JianyingPro/User\ Data/Projects/com.lveditor.draft/我的草稿/draft_info.json
```

加密算法已逆转为**纯 Python**（AES-256-GCM，key/nonce 藏在密文自身的 12 个固定偏移里）——
不需要装剪映、不需要 hook、不需要调用原生库。

**草稿打不开时**（版本兼容问题）：

```bash
# 降级方案：素材包 + SRT + manifest + premiere.xml，版本无关
python scripts/jianying_pack.py --clips clips/clip01.mp4 --voice voice.mp3 \
  --bgm bgm.mp3 --subtitles voice.srt --name "我的混剪"

# 自查草稿哪里坏了
python scripts/jianying_check.py ./jianying-out/我的混剪
```

---

## 脚本

| 脚本 | 作用 |
|---|---|
| `scripts/polli_account.py` | 额度管理：余额、用量、quest 清单、key 保存、模型列表 |
| `scripts/pollinations_client.py` | Pollinations 客户端（图像/视频/账户 API） |
| `scripts/gen_image.py` | 单张文生图（keyed 无水印 / anon 有水印，带缓存） |
| `scripts/gen_shots.py` | 按分镜表批量生图（跳过已存在 + 缓存命中） |
| `scripts/gen_video.py` | 图生视频（pollinations → ltx → wan → local 分级回退） |
| `scripts/still_to_clip.py` | 静态图 → 运镜片段（零额度消耗的兜底） |
| `scripts/lipsync.py` | 图片 / 视频对口型 |
| `scripts/tts.py` | 配音 + 时间戳精确对齐的 SRT 字幕 |
| `scripts/mix.py` | 混剪合成：转场 + 字幕烧录 + BGM 自动闪避 |
| `scripts/jianying_draft.py` | 生成剪映专业版草稿（多轨：视频/字幕/旁白/BGM，带转场与闪避关键帧） |
| `scripts/jianying_crypto.py` | 剪映草稿离线加解密（AES-256-GCM，无需安装剪映） |
| `scripts/jianying_pack.py` | 导出剪映素材包 + FCPXML（版本无关的降级方案） |
| `scripts/jianying_check.py` | 校验剪映草稿完整性（悬空引用、重叠、超时长、素材丢失） |
| `scripts/dewatermark.py` | 去除 Pollinations 右下角水印（OpenCV inpaint） |
| `scripts/vmix_common.py` | 公共工具（ffmpeg 定位、依赖安装、重试、额度提示） |
| `tests/test_jianying.py` | 剪映接入测试套件（62 个用例，只用标准库） |

### 跑一遍自测

```bash
cd video-mix
python3 tests/test_jianying.py -v
```

62 个用例，约 30 秒。覆盖草稿加解密、SRT 解析、微秒时间单位、校验器的故障注入、
FCPXML 命名空间，以及三个剪映脚本的端到端命令行调用。测试素材由 ffmpeg 现场合成，
不依赖任何外部文件；缺 ffmpeg 或 `pycryptodome` 时相关用例自动跳过。

---

## 踩坑清单

这些是真实撞过的坑，README 里都有实测对比：

1. **`gen.pollinations.ai` 整个端点都要 key**，不是只有视频要。实测 `key=free`、`referrer=`、`private=true`、不带参数——全部 401。别浪费时间试参数组合，直接注册。
2. **生图限速每 45 秒 1 张**（不是并发限制，是串行配额）。超限返回 HTTP 402 `FREE_LIMIT`。
3. **edge-tts 7.x 时间戳单位是 100 纳秒**（1 秒 = 10,000,000），不是微秒。按微秒算会让 4.6 秒音频变成 45 秒字幕。
4. **libass 对 SRT 默认 PlayResX=384**，字号被放大约 `width/384` 倍。必须先转 ASS 并显式声明 PlayRes。
5. **ZeroGPU 额度按天计**：匿名 2 分钟/天，免费账号 5 分钟/天。且是**首次使用后整 24 小时重置**，不是自然日零点。
6. **ffmpeg 滤镜链标签必须严格串联**，`[v1][vout]` 会直接报 filterchain 解析错误。
7. **中文提示词还原度极差**。`一只橘猫坐在窗台上看雨` → 空房间（无猫）；英文则精准还原。
8. **语音长于画面时 `-shortest` 会砍掉旁白尾句**，需用 `tpad` 克隆末帧补齐。
9. **去水印要设 `--min-pixels 3`（不是 12）**。水印左侧的花朵图标每行只有几个像素，按 12 检测会漏掉它下半截笔画，留下可见白色碎点。
10. **Intel Mac 装不了 PyTorch**。PyTorch 2.3+ 没有 cp313/x86_64 macOS wheel，`pip install torch` 报 "from versions: none"。本地跑扩散模型这条路在 Intel Mac 上是死的。
11. **HF 上没有常驻 GPU 的免费 i2v Space**。扫了 123 个候选，全是 `cpu-basic` 或 `zero-*`；CPU 后端实测 18 分钟未出片。
12. **剪映草稿的时间单位是微秒**（1 秒 = 1,000,000）。写成毫秒会导致时间轴对不上——剪映自动化第一大坑。
13. **剪映 6.0 起正文文件改名 `draft_info.json`**。沿用 5.9 的 `draft_content.json` 会报"草稿内容已损坏，请联系客服"，而内容其实完全正常。**两个名字都写最保险**。
14. **转场挂在「前一个」片段上**，且必须在片段加入轨道**之前**注册到 `materials.transitions`，否则转场素材会丢。
15. **剪映草稿打不开时不报错，只显示空白时间线**。用 `jianying_check.py` 自查，能定位到是哪个引用断了、哪段重叠、哪个素材丢了。
16. **字幕时间轴重叠会被剪映拒绝**。edge-tts 生成的相邻字幕常有几十毫秒重叠，`jianying_draft.py` 已自动裁齐。
17. **FCPXML 的命名空间必须是默认命名空间**。用 `ns1:` 前缀写 asset 元素，Premiere / Resolve 会直接拒导；时间值也要用有理数（`4/1s`）而不是小数。

---

## 免费额度真实边界

| 服务 | 额度 | 够用吗 |
|---|---|---|
| Pollinations 匿名生图 | 每 IP 每 45 秒 1 张 | 单条够，批量要等 |
| Pollinations 匿名生图 | 每张带水印 | 需 `dewatermark.py` 或改用 key |
| Pollinations key 端点 | 账户余额，新号 0.25 USD | 靠 quest 可免费补到 132+ USD |
| HF ZeroGPU（匿名） | 2 分钟 GPU/天 | 约 3–4 个镜头 |
| HF ZeroGPU（免费账号 + token） | 5 分钟 GPU/天 | 约 8–10 个镜头 |
| Edge TTS | 无明确限制 | 充足 |
| ffmpeg | 本地 | 无限制 |

批量生产请配置 Pollinations key（首选，不占 HF 额度）：

```bash
python scripts/polli_account.py key --set pk_xxxxxxxx
```

额度耗尽时，`still_to_clip.py` 提供完全零消耗的本地兜底。

---

## 商用授权

**免费 ≠ 无限制商用。** 详见 `references/licensing.md`，摘要：

- Pollinations 匿名端点输出**带水印**（`nologo=true` 无效），key 端点无水印但**商用授权仍不明确**
- Pollinations 的 25 个视频模型横跨 Google / 字节 / 快手 / 阿里，**授权各不相同**，逐个用之前必须查模型卡
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
