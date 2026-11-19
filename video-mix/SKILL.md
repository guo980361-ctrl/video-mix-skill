---
name: video-mix
description: 混剪视频制作的端到端流水线skill，基于免费云端接口实现文生图、图生视频、图片对口型、配音与字幕生成，最终用ffmpeg本地合成为带转场和字幕的成片。支持配置 Pollinations API key 获得无水印生成与长期可用额度，内置内容缓存与额度查询以控制消耗。当用户需要"做混剪视频""AI生成视频""图片转视频""让图片说话""对口型""AI配音""批量生成分镜素材""短视频批量生产"时使用本skill。This skill should be used when creating montage/compilation videos, generating images or video from text, animating still images, lip-syncing photos to audio, or batch-producing short-form video content.
agent_created: true
---

# 混剪视频制作 Skill

用**免费**的云端接口完成「文生图 → 图生视频 → 对口型 → 配音字幕 → 混剪成片」全流程。

## 核心前提：算力决定架构

本 skill 面向**没有独立显卡的机器**（Intel Mac / 集显 / 无 GPU 云主机）。所有重型生成走**云端免费接口**，本地只做 ffmpeg 后期。

**不要尝试本地跑 ComfyUI / Wan2.2 / Stable Diffusion**——需要 NVIDIA 8GB+ 显存，集显机器必然失败。Intel Mac 更不现实：PyTorch 从 2.3 起就没有 x86_64 macOS 的 wheel（实测 `pip install torch` 报 "from versions: none"）。若用户有独显机器，再考虑本地化（见 `references/local-gpu.md`）。

## ⚠️ 第一个必须知道的事：三层资源，各有各的天花板

"免费"在这里有三个不同的含义，选错层级会浪费大量时间：

| 层级 | 后端 | 天花板 | 是否要 key |
|---|---|---|---|
| **① 长期可用** | `gen.pollinations.ai` | 账户余额（可免费赚取） | ✅ 需免费注册 |
| **② 每日额度** | HF ZeroGPU Space | 2–40 分钟 GPU/天 | ❌ 可选 token |
| **③ 零消耗** | 本地 ffmpeg 运镜 | 无限制 | ❌ 不需要 |

### ① 长期可用层（推荐首选）

注册一次 Pollinations key，之后长期使用，**无水印、无 45 秒限速、不占 HF 额度**。

```bash
# 1. 免费注册并创建 key：https://enter.pollinations.ai/keys
# 2. 保存（脚本会写 ~/.workbuddy/.pollinations_key，权限 600）
python scripts/polli_account.py key --set pk_xxxxxxxx

# 3. 随时查看余额与用量
python scripts/polli_account.py balance
python scripts/polli_account.py usage
```

**新账号自动到账 0.25 USD。** 余额用完可以免费赚回来——quest 系统里有 **26 个零成本任务合计 132.75 USD**（Discord 等级、提交 PR、做一个 agent skill 等）：

```bash
python scripts/polli_account.py quests
```

这个层级能用的是 **25 个视频模型**（Veo 3.1、Seedance 2.x、Kling 3.0、Wan 3.0、Flux 3 Max…），全部支持 `start_frame` 图生视频。

### ② 每日额度层（无 key 时的兜底）

Hugging Face ZeroGPU 的免费额度**按天计，不是按次计**：

| 账户状态 | 每日 GPU 时长 | 够跑几个镜头 |
|---|---|---|
| 匿名（无 token） | 2 分钟 | 约 3–4 个镜头 |
| 免费账号（绑 token） | 5 分钟 | 约 8–10 个镜头 |
| PRO（$9/月） | 40 分钟 | 足够批量生产 |

一次图生视频约 20 秒 GPU，一次对口型 30–60 秒。额度耗尽时报 `You have exceeded your ZeroGPU quota`，脚本会自动回退。

### ③ 零消耗层（永不失败）

`scripts/still_to_clip.py` 用本地 ffmpeg 做 Ken Burns 运镜，**零额度、零网络、1–2 秒出片**。`gen_video.py --backend local` 已集成它。

### 省额度的三个习惯

1. **配 key**（①层）——没有 key 就只能挤在 2 分钟/天的额度里
2. **用缓存**——`gen_image.py` 内置内容寻址缓存（`~/.workbuddy/cache/video-mix`），同一 prompt + 参数**只付一次钱**。实测：批量生图首次 62.6 秒，重跑 3.3 秒全部命中缓存
3. **先查余额**——批量任务前跑 `polli_account.py balance`，别做到一半发现余额空了

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

**配了 key 之后**（推荐），走 `gen.pollinations.ai`：**无水印、无 45 秒限速**，而且生图很便宜。脚本会自动检测 key 并选后端。

**没配 key** 时走 `image.pollinations.ai` 匿名端点：每 IP 每 45 秒只能生成 1 张图，超过返回 HTTP 402（`FREE_LIMIT`）。脚本已内置 46 秒节流和 402 自动重试。批量生成 8 张约需 6 分钟，**不要并发跑**。

**缓存已内置**：同一 prompt + 参数只付一次钱。已存在的文件会被跳过，重跑整个分镜表零消耗。

单张补图用 `scripts/gen_image.py`（参数见 `--help`）。

### 第 3 步：图片转视频

```bash
python scripts/gen_video.py --image shots/shot01.jpg \
  --prompt "the woman walks forward, neon reflections ripple in puddles" \
  --out clips/clip01.mp4
```

后端按可用性分级回退：

| 后端 | 条件 | 画质 | 耗时 | 占额度 |
|---|---|---|---|---|
| `pollinations` | 有 key | 高 | 30–120 秒 | 账户余额 |
| `ltx` | 无 key | 高 | 17–20 秒 | HF 每日 2–40 分钟 |
| `wan` | 无 key | 高（14B） | 60–120 秒 | HF 每日额度 |
| `local` | 手动指定 | 中 | **1–2 秒** | **零** |

配了 key 就优先用 `pollinations`——**不与 HF 额度竞争**，而且能选 Veo 3.1、Seedance 2.x、Kling 3.0、Wan 3.0 这些一线模型：

```bash
python scripts/gen_video.py --image shots/shot01.jpg \
  --prompt "..." --out clips/clip01.mp4 \
  --backend pollinations --model bytedance/seedance-2.0-fast
```

**`wan` 和 `sdxt` 极慢**（CPU 跑扩散模型），只适合额度彻底耗尽时救急。真正的兜底是用静态图做运镜：

```bash
python scripts/still_to_clip.py --image shots/shot01.jpg \
  --out clips/clip01.mp4 --seconds 4 --motion zoom-in
```

运镜方向可选 `zoom-in`、`zoom-out`、`zoom-in-left`、`zoom-in-right`、`still`。这条路径**完全不消耗任何云端额度**，且本地渲染只要 1–2 秒。也可以直接 `--backend local`。

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

### 第 6.5 步（推荐）：精细化流水线 —— 从"能看"到"能发"

上面那条快路径出的片子**能用但粗糙**。粗糙的根源不在素材，而在五个环节
各做各的：分辨率被裁掉了、色调不统一、运镜一眼假、剪辑没节奏、音乐像测试音。

想要作品级的成片，改用这条精细流水线。**所有决策收进一份主控方案
（JSON），每个环节都从它读取**，这是整套流程的关键：

```bash
# 1. 写主控方案 shots.json：全局 anchor + 每镜的时长/转场/景别/光线/台词
python scripts/gen_stills.py --plan shots.json --outdir shots \
  --width 1024 --height 1820            # 竖屏原生，别用方图裁切

python scripts/clean_watermark.py --indir shots --outdir shots_clean

# 2. 做片段：真动态优先，额度不够自动降级为 2.5D 视差运镜
python scripts/make_clips.py --plan shots.json --stills shots_clean --out clips
python scripts/make_clips.py --plan shots.json --stills shots_clean --out clips --no-ltx

# 3. 音乐 + 房间音 + 精确闪避（人声频段做 EQ，不是简单压低）
python scripts/audio_score.py --duration 28 --out audio/score.wav \
  --room-out audio/room.wav --voice audio/voice.mp3 --mix-out audio/mix.wav

# 4. 逐镜转场 + 字幕 + 首尾淡入淡出（剪辑点对齐台词）
python scripts/assemble.py --plan shots.json --clips clips --out raw.mp4 \
  --audio audio/mix.wav --subtitles audio/voice.srt

# 5. 全片统一调色（一次过，跨镜头色温一致）
python scripts/grade.py --in raw.mp4 --out final.mp4 --preset amber
```

新增脚本：

| 脚本 | 作用 |
|---|---|
| `scripts/gen_stills.py` | 按主控方案批量生图，自动限速去重 |
| `scripts/clean_watermark.py` | 批量去水印（免费层必带） |
| `scripts/camera_move.py` | 2.5D 视差 / 呼吸感 / 手持微抖运镜，9 种模式 |
| `scripts/make_clips.py` | 分发到真动态或运镜，**额度耗尽自动降级** |
| `scripts/audio_score.py` | 加法合成 BGM + 房间底噪 + 精确闪避混音 |
| `scripts/assemble.py` | 逐镜转场、字幕样式、首尾淡入淡出 |
| `scripts/grade.py` | 统一调色 + 暗角 + 颗粒 + 高光溢出 |

**完整的提示词工程踩坑记录**（`frame` 会招来相框、`ginger` 会变成发色、
"人的腿"一定画出人、免费档分辨率天花板等）见
`references/finishing.md`，做片之前务必读一遍。

### 第 7 步（可选）：接入剪映专业版继续精修
成片一步就出，但如果你想**在剪映里继续调**（换字体、加花字、做音效、改节奏），
把上面那堆素材直接变成一个可编辑的剪映工程：

```bash
# 生成剪映草稿，--install 直接写进剪映的草稿目录
python scripts/jianying_draft.py \
  --clips clips/clip01.mp4 clips/clip02.mp4 clips/clip03.mp4 \
  --voice voice.mp3 --bgm bgm.mp3 --subtitles voice.srt \
  --name "我的混剪" --install
```

打开剪映专业版，首页第一个草稿就是它。里面已经排好：

- **主视频轨**：每个片段是独立 segment，可单独回拉调整、转场是可改的剪映原生转场
- **字幕轨**：字幕是真正的文本片段（不是烧进画面的），能一键换字体/描边/动画
- **旁白轨**、**BGM 轨**：分轨摆放，BGM 已按旁白自动做了音量闪避（关键帧）

常用参数：

| 参数 | 说明 |
|---|---|
| `--transition 叠化` | 转场名，共 19 种免费转场（`叠化`/`故障`/`上下翻页`/`中心旋转`…） |
| `--transition-duration 0.5` | 转场时长（秒） |
| `--width/--height/--fps` | 画布，默认 1080×1920@30（竖屏） |
| `--bgm-volume 0.2` | BGM 基础音量 |
| `--subtitle-size 8.0` | 剪映字号（5~12 之间试） |
| `--subtitle-y -0.8` | 字幕纵向位置，-0.8 在下方，0.8 在上方 |
| `--encrypt` | 输出加密草稿（需 `pip install pycryptodome`） |
| `--install` | 直接写入剪映草稿目录；不加则输出到 `--out` |
| `--out ./jianying-out` | 输出**父目录**，草稿落在 `<目录>/<名称>/`（和默认路径一致） |

> 💡 工具会**同时写 `draft_info.json` 和 `draft_content.json`**：
> 6.0 以后剪映只认 `draft_info.json`，用错名字会报"草稿内容已损坏"。
> 两个都写就不用管用户装的是哪个版本。

> ⚠️ **版本兼容**：剪映 6.0 起草稿加密，7+ 还隐藏了自动导出的控件。
> 生成的草稿适配 5.9 / 6.x–10.x；**11.x 可能需要额外注册**。
> 打不开时用下面的降级方案，或直接用 `mix.py` 出片。

**读写自己已有的加密草稿**：

```bash
pip install pycryptodome
python scripts/jianying_crypto.py -p ~/Movies/JianyingPro/User\ Data/Projects/com.lveditor.draft/我的草稿/draft_info.json
```

加密算法已逆向为纯 Python（AES-256-GCM，key/iv 藏在密文自身），
**不需要装剪映、不需要 hook、不需要调用原生库**。

**降级方案（版本无关，永远可用）**：

```bash
python scripts/jianying_pack.py \
  --clips clips/clip01.mp4 clips/clip02.mp4 --voice voice.mp3 \
  --bgm bgm.mp3 --subtitles voice.srt --name "我的混剪" --draft
```

产出一个素材包：分段视频 + 对齐的 SRT 字幕 + 旁白 + BGM + `manifest.json`（含每个片段的时间轴位置）+
`premiere.xml`（Premiere Pro / DaVinci Resolve 可直接导入）。在剪映里手动导入也是几十秒的事。

**校验草稿**（剪映打不开时先自查，能定位到坏在哪）：

```bash
python scripts/jianying_check.py ./jianying-out/我的混剪
```

会检查：JSON 是否合法、素材引用是否悬空、片段是否重叠、取用范围是否超出素材时长、素材文件是否还在。

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

**免费 ≠ 无水印。** Pollinations 免费层每张图右下角都盖 `pollinations.ai`，且 `nologo=true` 实测无效。生图后必须过一遍去水印：

```bash
python scripts/dewatermark.py --image shots/shot01.jpg --out shots/shot01_clean.jpg
```

脚本用 OpenCV 的 Telea inpaint 填补白色文字像素。**关键参数是 `--min-pixels` 默认 3 而不是 12**——水印左侧的花朵图标每行只有几个像素，按 12 检测会漏掉它下半截的笔画，留下肉眼可见的白色碎点。去水印后再喂给 `still_to_clip.py` / `gen_video.py`。

## 踩坑清单（已实测确认）

这些坑都真实撞过，不要重复踩：

1. **Pollinations 视频接口需要 API Key**。`gen.pollinations.ai/video/` 无 Key 返回 401，`image.pollinations.ai` 的旧免 Key 路径在部分网络下会返回 402。本 skill 的生图走 `image.pollinations.ai/prompt/`（实测可用），视频走 HF Space，不走 Pollinations。
2. **生图限速是每 45 秒一张**（不是并发限制，是串行配额）。批量任务务必串行。
3. **edge-tts 7.x 的时间戳单位是 100 纳秒**（1 秒 = 10,000,000），不是微秒。按微秒换算会让 4.6 秒的音频变成 45 秒字幕。
4. **libass 对 SRT 默认 PlayResX=384**，字号会被放大约 `width/384` 倍。`mix.py` 已改为先转 ASS 并显式声明 PlayResX/PlayResY，字号才是真实像素值。
5. **ZeroGPU Space 冷启动会超时**。gradio_client 默认超时太短，脚本已加长超时 + 指数退避重试（3 次）。
6. **ffmpeg 滤镜链的标签必须严格串联**。`[v1][vout]` 这种写法会报 "Error parsing filterchain"，必须由 `;` 分隔成独立 filter。
7. **Pollinations 免费层必带水印**。`nologo=true` 实测无效，图中右下角固定有 `pollinations.ai`。用 `scripts/dewatermark.py` 去除，别直接交付带水印的成片。（要根治得配 key，用 `gen.pollinations.ai` 端点，天然无水印。）
8. **CPU 后端不要用于生产**。`sdxt` 实测跑 18 分钟未出片，`stable-video-diffusion-img2vid-xt` 也超时。CPU Space 只适合应急验证，正式出片走 Pollinations key 层或本地运镜。
9. **`gen.pollinations.ai` 必须带 key**。不带 key 一律 401（实测 `key=free`、`referrer=` 都不能绕过）。不要浪费时间试参数组合——直接注册。
10. **Intel Mac 装不了 PyTorch**。PyTorch 2.3+ 没有 cp313/x86_64 macOS wheel，`pip install torch` 报 "from versions: none"。本地生成模型这条路在 Intel Mac 上是死的，别浪费时间。
11. **HF 上没有常驻 GPU 的免费 i2v Space**。扫了 123 个候选，全部是 `cpu-basic` 或 `zero-*`。真动态只有 ZeroGPU 和 Pollinations 两条路。
7. **本机无 brew**。ffmpeg 用 `pip install imageio-ffmpeg` 的静态二进制，脚本会自动定位，不要要求用户装 brew。
8. **相对输出路径按 CWD 解析**。`--out video-mix-out/demo/a.mp4` 落在 CWD 下，不会变成 `video-mix-out/video-mix-out/...`。
9. **中文提示词还原度差**。生图一律用英文提示词（见第 1 步）。
10. **语音比画面长时会截断配音**。`mix.py` 已用 `tpad` 克隆末帧补齐视频时长，并按目标时长裁剪输出，避免 `-shortest` 砍掉旁白尾句。
11. **ZeroGPU 额度是按天计的**。同一天内跑满约 3–4 个镜头就会耗尽，必须提前配 `HF_TOKEN`。
12. **剪映草稿的时间单位是微秒**（1 秒 = 1,000,000）。写成毫秒会导致时间轴对不上——这是剪映自动化最常见的坑。
13. **转场挂在「前一个」片段上**，且必须在片段加入轨道**之前**注册到 `materials.transitions`，否则转场素材会丢失。
14. **剪映 6.0 起草稿加密**，7+ 还隐藏了导出控件（自动导出仅 6.x 及以下 + Windows）。
15. **提示词里的 `frame` 会招来相框**。模型把 frame 理解成"画框"，直接给你画一张装在相框里的照片。同类触发词：picture frame、painting、artwork。改成"photograph"或"picture"。
16. **`ginger` 会被理解成发色**。`ginger tabby cat` 和 person 同框时会变成红发的人；单独用也偏奶油色。一律写 `orange tabby cat`。
17. **提示词里写"人的腿/脚/手"大概率画出人**。实测三连翻车：画成红发的人、画成人抱着猫、或者手直接消失。把主体放句首（`an orange cat nuzzling a person's calf`）比把人体放主语位（`a person's calf being nuzzled`）成功率高得多。实在画不好就换语义——"蹭腿"改成"蹭进毯子"。
18. **极端姿态会崩坏**。`stretching its head upward` 会把猫头拉成长条；`the whole body visible` 常常只出一个头。用安全姿态：坐着、侧头、躺下、走路、蜷成一团。
19. **免费档分辨率长边锁死 1024**，9:16 最高 576×1023。生成方图再裁切只剩 432×768，要损失一档有效像素——直接请求竖屏。
20. **`colortemperature` 和 `colorbalance` 方向会互相抵消**，叠加后结果不可预测（实测把暖度参数调大，成片暖度反而从 +59 掉到 +51）。暖度提升要用 `lutrgb` 逐通道做，单调且可测量。
21. **ASS 样式行的字段顺序必须严格对上 Format**。少写 `Fontname` 字段，后面全部错位——最常见的事故是把宽高填进 Fontname 和 Fontsize，字号变成 1080。
22. **调色要靠测量不能靠感觉**。抽帧算平均 R−B，暖色片应 > +70；逐镜也测一遍，差距大说明某镜头色偏没救回来。
    加密算法已逆转为纯 Python AES-256-GCM（key/iv 藏在密文自身的 12 个固定偏移里），
    **不需要装剪映、不需要 hook、不需要原生库**——见 `jianying_crypto.py`。
15. **剪映 6.0 起正文文件改名 `draft_info.json`**。继续用 5.9 的 `draft_content.json`
    会报"草稿内容已损坏，请联系客服"，而内容其实完全正常。**两个名字都写最保险**。
16. **剪映草稿打不开时不会报错，只会显示空白时间线**。用 `jianying_check.py` 自查，
    它能定位到具体是哪个引用断了、哪段重叠、哪个素材丢了。
17. **字幕时间轴重叠会被剪映拒绝**。真实场景里 edge-tts 生成的相邻字幕常有几十毫秒重叠，
    `jianying_draft.py` 已自动裁齐（上游库会直接抛异常）。
18. **FCPXML 的命名空间必须是默认命名空间**。用 `ns1:` 前缀写 asset 元素，
    Premiere / Resolve 会直接拒导；时间值也要用有理数（`4/1s`）而不是小数。
19. **别为了让剪映刷新草稿就去杀剪映进程**。剪映启动时自己会扫描草稿目录，
    重启即可。强杀有丢失未保存内容的风险。

## 资源消耗预期

| 步骤 | 耗时 | 外部依赖 |
|---|---|---|
| 单张生图 | 9–45 秒 | Pollinations（45s 配额） |
| 图生视频 | 17–25 秒 | HF LTX Space |
| 图片对口型 | 31–60 秒 | HF Wav2Lip Space |
| 配音 | 2–5 秒 | Edge TTS |
| 混剪合成 | 5–20 秒 | 本地 ffmpeg |
| 生成剪映草稿 | <1 秒 | 本地（零外部依赖） |
| 导出剪映素材包 | <1 秒 | 本地（零外部依赖） |

## 扩展与替代

- **本地 GPU 机器**：见 `references/local-gpu.md`，可改用 ComfyUI + Wan2.2 + MuseTalk 完全离线
- **更多免费后端与实测记录**：见 `references/free-backends.md`
- **商用授权注意事项**：见 `references/licensing.md`

## 脚本清单

| 脚本 | 作用 |
|---|---|
| `scripts/polli_account.py` | 额度管理：余额、用量、quest 清单、key 保存、模型列表 |
| `scripts/pollinations_client.py` | Pollinations 客户端（图像/视频/账户 API） |
| `scripts/gen_image.py` | 单张文生图（keyed 无水印 / anon 有水印，带缓存） |
| `scripts/gen_shots.py` | 按分镜表批量生图（跳过已存在 + 缓存命中） |
| `scripts/gen_video.py` | 图生视频（pollinations → ltx → wan → local 分级回退） |
| `scripts/still_to_clip.py` | 静态图 → 运镜片段（零额度消耗的兜底） |
| `scripts/lipsync.py` | 图片或视频对口型 |
| `scripts/tts.py` | 配音 + 同步字幕 |
| `scripts/mix.py` | 混剪合成：转场 + 字幕 + BGM 闪避 |
| `scripts/jianying_draft.py` | 生成剪映专业版草稿（多轨：视频/字幕/旁白/BGM，带转场与闪避关键帧） |
| `scripts/jianying_crypto.py` | 剪映草稿离线加解密（AES-256-GCM，无需安装剪映） |
| `scripts/jianying_pack.py` | 导出剪映素材包 + FCPXML（版本无关的降级方案） |
| `scripts/jianying_check.py` | 校验剪映草稿完整性（悬空引用、重叠、超时长、素材丢失） |
| `scripts/dewatermark.py` | 去除 Pollinations 右下角水印（OpenCV inpaint） |
| `scripts/vmix_common.py` | 公共工具（ffmpeg 定位、依赖安装、重试、额度提示） |
| `scripts/gen_stills.py` | 按主控方案批量生图（精细化流水线） |
| `scripts/clean_watermark.py` | 批量去水印 |
| `scripts/camera_move.py` | 2.5D 视差 / 呼吸感 / 手持微抖运镜，9 种模式 |
| `scripts/make_clips.py` | 分发到真动态或运镜，额度耗尽自动降级 |
| `scripts/audio_score.py` | 加法合成 BGM + 房间底噪 + 精确闪避混音 |
| `scripts/assemble.py` | 逐镜转场 + 字幕样式 + 首尾淡入淡出 |
| `scripts/grade.py` | 全片统一调色 + 暗角 + 颗粒 + 高光溢出 |
| `tests/test_jianying.py` | 剪映接入测试套件（62 个用例，只用标准库） |

## 自测

改动剪映相关脚本后跑一遍回归：

```bash
python3 tests/test_jianying.py -v
```

62 个用例，约 30 秒。覆盖草稿加解密、SRT 解析、时间单位（微秒）、
校验器的故障注入、FCPXML 命名空间，以及三个脚本的端到端命令行调用。
测试素材用 ffmpeg 现场合成，不依赖任何外部文件；缺 ffmpeg 或
`pycryptodome` 时相关用例会自动跳过。

