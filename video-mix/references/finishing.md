# 精细化混剪：从"能看"到"能发"

这一篇记录把一条 AI 混剪从"能看"做到"能发"的全过程，以及每个环节踩过的坑。
写给下一次要做片子的人，也写给想改这套脚本的人。

---

## 一、先搞清楚"粗糙"到底来自哪里

同样一批素材，别人做出来像电影，你做出来像 PPT，差别不在设备，在这五处：

| 粗糙的来源 | 具体表现 | 解法 |
|---|---|---|
| **分辨率不够** | 画面发糊，放大后有涂抹感 | 竖屏原生生成，不裁方图 |
| **色调不统一** | 每个镜头底色不同，像拼贴 | 全片统一调色 |
| **运镜是假的** | 所有画面同速缩放，一眼假 | 2.5D 视差 + 呼吸感 |
| **剪辑无节奏** | 等长分镜、全部同一种转场 | 逐镜时长对齐台词、转场多样化 |
| **声音像测试音** | BGM 是正弦波，人声被音乐盖住 | 加法合成音色 + 频段 EQ + 精确闪避 |

其中**色调不统一**是最容易被忽略、也最伤的一条。8 张图分别生成，
白平衡和对比必然漂移，单独看每张都还行，连起来就像 8 个不同的场景。

---

## 二、分镜：先定数据，再写提示词

不要边想边写提示词。先建一份**主控方案**（JSON），把所有决策固化下来，
后面每个环节都从它读取。这样可以保证：

- 生图、运镜、配音、剪辑用的是同一套时长和台词，不会各说各话
- 想改一个镜头，只改一处
- 逐镜的转场、时长、旁白一目了然

一份合格的分镜数据长这样：

```json
{
  "anchor": "全局一致性锚点，所有镜头都原样附加",
  "shots": [
    {
      "id": "s04",
      "duration": 3.9,
      "motion": "push-in",
      "ltx": false,
      "subject": "主体是谁",
      "action": "在做什么",
      "framing": "景别与构图",
      "light": "光位与色温",
      "grade": "调色倾向",
      "camera": "镜头语言（焦段、光圈）",
      "negative": "排除项",
      "narration": "对应的旁白"
    }
  ]
}
```

字段不是越多越好，但 `subject / action / framing / light / negative` 这五个
必须有——它们分别对应模型的五个理解维度，缺一个就会出现"画出来的东西
和脑子里想的不是一回事"。

---

## 三、生图：模型听不懂的词，比想不到的更致命

这一节是整套流程里最费时间的部分。免费档的 flux 模型对提示词的理解
有固定的坑，**每一条都是实测踩出来的**。

### 坑 1：`frame` 会招来相框

模型把 "frame" 理解成"画框"，于是给你画一张**装在相框里的照片**。
触发词包括：frame、picture frame、painting、artwork、framed。

> 反例：`tight close-up on the face within the frame`
> 正例：`tight close-up on the face, a photograph`

同理，"no picture frame" 这种否定词也有效，因为模型仍会读到 "picture frame"。

### 坑 2：`ginger` 会被理解成发色

"ginger tabby cat" 在和 "person" 同框时会变成**红头发的人**。
"ginger" 单独用也会让猫偏成奶油色。

> 一律写成 `orange tabby cat` 或 `orange striped tabby cat`。

### 坑 3：任何"人的腿/脚/手"都会画出整个人

实测三连翻车：

| 提示词 | 结果 |
|---|---|
| `cat nuzzling a person's bare calf` | 画成红发的人 |
| `cat lying next to a person's lower leg` | 画成红发的人抱着猫 |
| `a human hand petting a cat's head` | 只有猫，手消失了 |

**规律**：只要把人体部件写进主语位，模型就优先画人，把猫挤掉。

> 有效写法：把主体放句首，人体部件降级为细节：
> `an orange tabby cat nuzzling a person's calf` → 成了
> `a ginger tabby cat nuzzling a person's bare calf` → 成了

若实在画不好，**换语义而不是换措辞**：把"蹭腿"改成"蹭进毯子"，
把"被摸头"改成"抬头蹭向画外"。观众读的是情绪，不是解剖学。

### 坑 4：极端姿态会崩坏

`stretching its head upward` → 猫头被拉成长条。
`the whole body visible` → 只出一个头。

> 安全的姿态：坐着、侧头、躺下、走路、蜷成一团。
> 避免：拉伸、腾空、大幅扭转、明确要求"全身可见"。

### 坑 5：`negative` 里别写正面描述

`negative` 字段里的 "no warm tones" 会被模型读成 "warm tones"。
负面描述要用"排除另一个具体事物"的写法，且**不要重复正面已出现的词**。

### 坑 6：分辨率的天花板

免费档长边锁死 1024，即 9:16 最高 **576×1023**。

| 路线 | 有效像素 | 评价 |
|---|---|---|
| 1024 方图裁 9:16 | 432×768 | 差，放大到 1080×1920 是 2.5 倍拉伸 |
| **竖屏原生 1024×1820** | **576×1023** | 好，1.87 倍拉伸，配颗粒可接受 |

> 直接请求竖屏，不要生成方图再裁。免费档会自己把请求缩到长边 1024，
> 但比例是对的，比裁切多出一档有效像素。

### 一致性锚点怎么写

锚点要覆盖三件事：**主角外观 + 场景 + 光线**，缺一不可。
只写"橘猫"而不管背景，每张图的房间和色调都会飘。

```
an orange striped tabby cat, short plush fur, white chest patch and white paws,
round amber eyes; a cozy living room at dusk bathed in golden amber lamplight,
warm honey coloured walls, grey fabric sofa, warm oak floorboards, a glowing
amber floor lamp; everything in warm amber and honey tones, no cool colours,
no green background, no grey background; photorealistic, vertical 9:16 photograph
```

那两句 `no cool colours / no grey background` 不是废话——**它们是让全片
色调统一的最省力手段**，比后期调色有效得多。

### 验收要拼图看

逐张看会放过问题。把全部图缩到同一尺寸横向拼成一张，一眼就能看出
哪张偏冷、哪张的花色不一样、哪张画风跑偏。

---

## 四、运镜：假在哪里

`still_to_clip.py` 的 Ken Burns 是整帧同速缩放，一眼假。真实摄影机移动时
**近景和远景位移速度不同**（视差），这是"照片"和"镜头"的分界线。

`camera_move.py` 的做法：

1. **过采样** 1.32 倍，给移动留出余量（不然会露边）
2. `zoompan` 做主推拉 + 摇移，横幅叠一个**呼吸式缩放**（模拟手持）
3. **视差**：把下半幅（近景）比上半幅（远景）多移一点，
   用 `blend` 的 `all_expr` 按 Y 做软过渡，无接缝
4. **对焦**：开头轻微虚化，0.5 秒后合焦
5. **动态颗粒**：`noise=alls=5:allf=t+u`，让画面表面不死板

`blend` 的 `all_expr` 里可以用 `Y` 变量做垂直渐变蒙版，这是关键：

```
blend=all_expr='A*(1-clip((Y/H-0.42)/0.24,0,1))+B*clip((Y/H-0.42)/0.24,0,1)'
```

可用运镜：`push-in / pull-out / pan-left / pan-right / parallax-left /
parallax-right / crane-up / breath / still`

---

## 五、音频：三个都要自己算

### BGM 不能用正弦波

4 个 sine 相加 = 测试音，没有音色、没有包络、没有和声运动。
`audio_score.py` 用**加法合成**：

- 每个音是**谐波堆叠**，每个泛音有**独立的衰减率**
  （高频泛音先衰减，模拟真实弦乐/钢琴的"变薄"过程）
- 和声进行：`Am9 → Fmaj7 → Cmaj7 → G6`（暖而不悲伤）
- 低音铺底 + 琶音，琶音两声部交替、力度微差，避免机械感
- 铺底段落**重叠 1 秒**交叉淡化，否则每个和弦之间会掉到静音

### 闪避不能只靠压缩器

`sidechaincompress` 是阈值触发，音量不同则下潜深度不同，听起来忽深忽浅。
正确做法是**从人声本身算包络**：

```
env = 指数平滑(|voice|)，attack 15ms / release 320ms
gain = 10^(duck_db * env / 20)
```

人声峰值处恒定下潜 -10dB，每一句都一样。

### 还要给音乐做 EQ

人声清晰度在 **1–4kHz**，而 BGM 的泛音正好在这里。
只做闪避不够，音乐要在这段挖掉：

```
equalizer=f=2200:t=q:w=1.4:g=-7    # 挖语音频段
equalizer=f=260:t=q:w=0.7:g=3.5     # 补低中频
equalizer=f=6000:t=q:w=1.6:g=2.5    # 补空气感
```

### 别让间隙是数字静音

加一层**房间底噪**（滤波噪声 + 远处电流声 + 缓慢电平漂移），
音量压到 -20dB 左右。人声停顿时画面还在，音乐也在，观感才连贯。

### 验收看包络不看频谱

画一条"语音 RMS / 混音 RMS"的时间轴曲线：
- 说话时段应该在 **-2dB 附近**（人声占主导）
- 停顿时段应该掉到 **-40dB 以下**（说明人声确实停了，且间隙有音乐填充）

---

## 六、剪辑：节奏要跟着台词走

`mix.py` 的问题：所有转场同一种、所有片段按源长度切。
所以要自己写装配器（`assemble.py`）：

- **逐镜转场**：同一场景延续用 `dissolve / smoothleft / smoothup`，
  时间跳跃用 `fade / fadewhite`，不要七个镜头全用叠化
- **时长对齐台词**：每镜时长 = 本镜旁白时长 + 一点余量，
  首尾各留 0.6~0.8 秒呼吸。剪辑点落在词上，节奏才成立
- **转场长度 0.55 秒**左右：短了像跳切，长了像 PPT
- **首尾淡入淡出**：0.8 秒，否则第一帧和最后一帧很突兀

### 字幕

ASS 的样式行**字段顺序必须严格对上 Format**，少写一个字段，
后面全部错位——最常见的事故是把宽高填进了 `Fontname` 和 `Fontsize`，
结果字号变成 1080。

```
Format: Name, Fontname, Fontsize, PrimaryColour, ... , Encoding
Style: Main,PingFang SC,46,&H00FFFFFF,...,2,65,65,259,1
                  ^^^^^^^^^^^^^ 必须有字体名
```

其它要点：
- `PlayResX/PlayResY` 等于输出尺寸，`Fontsize` 才是真实像素
- 字号取宽度的 **4.3%**，1080 宽即 46px
- `MarginV` 抬到画面高度的 **13.5%**，避开短视频平台的底部 UI
- 用 `{\fad(160,160)}` 做每句的淡入淡出
- 描边用柔和深灰（`&H00202020`）+ 阴影，不要纯黑硬描边

---

## 七、统一调色：让 8 张图像一部片子

调色要作用在**装配之后的成片**上，而不是单张图上——这样转场混过的部分
也一起统一，且只需要调一次。

`grade.py` 的顺序：

1. `eq` —— 对比度、饱和度、gamma、亮度（唯一能设对比曲线的滤镜）
2. `colorbalance` —— 中间色调往暖色推
3. `colortemperature` —— 实际色温
4. **`lutrgb`** —— 最后的暖度提升

### 为什么必须有 lutrgb 这一步

`colortemperature` 和 `colorbalance` **方向会互相抵消**：前者工作在相关
色温上，后者工作在平衡通道上，叠加后结果不可预测。实测把参数调大，
成片暖度反而从 +59 掉到 +51。

`lutrgb` 是逐通道的、单调的、可测量的：

```python
r = min(255, max(0, val*1.121 + 13.2))    # 红抬
g = min(255, max(0, val*1.049))            # 绿几乎不动
b = min(255, max(0, val*0.901 - 9.9))      # 蓝压
```

> **调色要靠测量，不能靠感觉。** 抽帧算平均 R−B：
> 暖色片应 > +70。逐镜也测一遍，差距大说明某镜头色偏没救回来。

最后加 `vignette`（暗角）+ `noise`（颗粒）+ 一点高光溢出（bloom），
这三样是"像照片"和"像插画"的分界线。

---

## 八、真动态：为什么"运镜兜底"是个陷阱

> 2026-11-19 实测修订。上一版按"额度优先"思路，把 8 个镜头里的 6 个降级成
> `camera_move.py` 运镜，成片被评价为"图片拼接，根本没有动态"。
> **这个判断是对的**：运镜只动相机，不动主体，和照片推拉没区别。

### 关键区分

| | 运镜（camera_move） | 真动态（视频模型） |
|---|---|---|
| 动的对象 | 整帧缩放/平移 | 猫的腿、耳朵、胡须、毛发 |
| 观众感知 | "这是一张图在动" | "这是一只猫在动" |
| 实现 | ffmpeg zoompan/blend | 扩散模型逐帧生成 |

**运镜不能冒充动态。** 如果成片的主体没有自身运动，就算加了视差、呼吸、
颗粒，观众依然会判定为静图。

### 额度不是理由，是约束

匿名 ZeroGPU 额度约 **2 分钟 GPU/天**，LTX 每段 1.9 秒约吃 1 分钟。
所以正确做法**不是**把 6 个镜头降级成运镜，而是：

1. **优先换取 Pollinations key**（见下节），它按账户余额计费、不占 HF 额度，
   免费 Quest 额度就足够跑完 8 个镜头
2. 拿到 key 后，**全部镜头走真动态**，运镜只作为"模型服务不可用"的最后兜底
3. 分镜表里**不要**再用 `"ltx": true/false` 这种"哪个镜头值得花钱"的标记 ——
   它会诱导你做出"6 个静图 + 2 个动态"的错误结构

### 2026-11-19 免费通道实测结论

| 通道 | 实测结果 | 结论 |
|---|---|---|
| HF ZeroGPU 匿名 | `120s requested vs -60s left`，24h 重置制 | 一天只能做 1~2 个镜头 |
| HF Space `DeepRat/LTX-Video-Optimized` | 同一配额池，同样透支 | 不解决 |
| HF Space `abonline/stable-video-diffusion-img2vid-xt` | `cpu-basic` 常驻，不占额度，但 18 分钟未出片 | 本机 CPU 太慢 |
| Pollinations 视频模型 | 25 个模型**全部 `paid_only: true`** | Quest 免费额度**付不了** |
| Pollinations Quest | 297 个任务，`tier` 桶合计 1998.75 Pollen | 需**付费余额**才能付视频模型 |
| wavespeed / veo3-api 等代理 Space | 均要求用户自带 API key，无内置共享凭据 | 不可用 |
| `api.agnes.ai` 等"无限免费"说法 | 域名不可达或无文档端点 | 不要相信 |

**结论：免费云端真动态当天不可得，除非有付费余额的 Pollinations key。**

### 成本其实很低

按 `completionVideoSeconds` 计价（1 Pollen ≈ $1）：

| 模型 | 每秒 | 4 秒一段 | 8 镜 × 4 秒 |
|---|---|---|---|
| `alibaba/wan-2.2-fast` | 0.010 | 0.04 | **0.32 Pollen** |
| `bytedance/seedance-1-pro-fast` | 0.021 | 0.084 | 0.67 Pollen |
| `google/veo-3.1-fast` | 0.080 | 0.32 | 2.56 Pollen |
| `kwaivgi/kling-v3.0-std` | 0.084 | 0.34 | 2.68 Pollen |

**跑完 8 个镜头的真动态，wan-2.2-fast 只要 0.32 Pollen。**
额度焦虑是选错了模型，不是通道本身贵。

### 用数字验收，不要用感觉

`scripts/measure_motion.py` 用**仿射残差**判定"真动态 vs 运镜"：
先拟合相邻帧的全局仿射变换（zoompan/平移正好被完全解释），再看残差。

- 残差 ≈ 0 → 整帧缩放，即"图片在动"
- 残差 > 0 → 主体自身在动，即真动态

2026-11-19 在本项目上的实测值：

| 素材 | affine 残差 | 判定 |
|---|---|---|
| 实拍猫咪（玩猫） | 1.0267 | 真动态 |
| 实拍猫咪（抚摸） | 0.7517 | 真动态 |
| 实拍猫咪（静静躺着） | 0.0187 | 几乎不动 |
| v2 成片 s01 | 0.0355 | 运镜静态 |
| v2 成片 s02（标记为 LTX） | 0.0818 | 运镜静态 |
| v2 成片 s04（标记为 LTX） | 0.0370 | 运镜静态 |
| v2 成片 s07 | 0.0793 | 运镜静态 |

**8 个镜头无一达标，差 13 倍。** 这张表比任何主观描述都有说服力，
交付前用它逐镜验收，不要靠"我觉得动了"。

```bash
python scripts/measure_motion.py --dir clips
```

### 视频模型的动作提示词写法

给视频模型的提示词和给图像模型的完全不同：

- **写动作，不写状态**：`walks slowly toward the camera, each paw placing one after another`
  而不是 `the cat walks`（后者只是描述，可能被当成静止构图）
- **写节奏**：`slowly` / `in a steady rhythm` / `one by one` / `briefly`
- **写细节的连带运动**：`whiskers twitching with each step`、`fur ruffling against the knit`、
  `the ribcage expanding and contracting` —— 这是让画面"活"起来的关键词
- **相机单独一句**，放在动作之后：`the camera tracks backward at the cat's pace`
- **禁止出现**：`still image` / `photo` / `painting` / `frame`（会触发"相框里的画"）
- **禁止 `frame`**：`frame` 容易被理解成"画框"，改用 `shot on a 35mm lens`

---

## 八之二、本地兜底：真动态跑不动时怎么办

没有 key 且要马上出片时，本机（Intel Mac 8GB）唯一能跑的真扩散动态是：

```bash
# PyTorch 从 2.3 起停止发布 macOS x86_64 wheel，Python 3.13 装不了
# 用独立构建的 Python 3.12 + torch 2.2.2（cp312 有 x86_64 wheel）
curl -L https://github.com/astral-sh/python-build-standalone/releases/download/20250818/\
cpython-3.12.11+20250818-x86_64-apple-darwin-install_only.tar.gz | tar xz
./python/bin/python3 -m pip install torch==2.2.2 --index-url https://download.pytorch.org/whl/cpu
```

模型选 `stabilityai/stable-video-diffusion-img2vid-xt`（~4.7GB，fp16）。

**但要如实告知代价**：8GB 内存 Intel Mac 上 CPU 推理每镜 20~40 分钟，
8 镜合计 3~5 小时，且画质明显不如 Veo/Seedance。
**这是"没有 key 时唯一不撒谎的方案"，不是好方案。**

实测补充（2026-11-19，本机 i5-8257U / 8GB）：

- 权重下载：代理带宽约 **1.7MB/s**，单连接与 8 线程分块**同速**（瓶颈在代理不在连接数），
  3GB unet 需约 30 分钟；`HF_HUB_DISABLE_XET=1` 可绕过 Xet token 刷新的 502 报错
- 推理布局：`diffusers` 0.31 的 SVD unet 输入是 `(B, F, C, H, W)`，
  **不是** `(B, C, T, H, W)`；`C = 4(latent) + 4(image)` = 8；
  `encoder_hidden_states` 是 `(B, 1, cross_attention_dim=1024)`；
  `added_time_ids` 为 `(B, 6)` = `[F-1, H, W, 0, 0, fps]`
- 单步 CPU 前向在 256×256/14 帧下约 15~25 秒 → 25 步约 8~10 分钟/镜

如果只是要"看起来在动"而不是"真的在动"，
用 Mixkit 等免 key 真实素材源（`https://assets.mixkit.co/videos/<id>/<id>-1080.mp4`，
目录页 `mixkit.co/free-stock-video/cat/` 可抓到直链）是更快的路，
但那是**别人的猫**，角色一致性会断。

> 实测：Mixkit 只对**确实存在该分辨率**的组合返回 200，猜错的分辨率一律 403。
> 用 HEAD 逐个探测再下载，别盲下。

### Pollinations 图生视频的 414 陷阱（已修复）

`pollinations_client.py` 曾把起始帧 base64 内联进 query string：

```python
q["image"] = f"data:image/jpeg;base64,{b64}"   # 102KB URL -> HTTP 414
```

服务端 URI 长度限制约 8KB，一张 576×1024 的 JPEG 必然触发 **414 URI Too Long**。
正确做法是走官方 media 上传端点拿 URL：

```python
POST https://media.pollinations.ai/upload    # multipart/form-data, 字段名 file
-> {"url": "https://..."}                      # 把它塞进 q["image"]
```

已实现于 `_upload_media()`。注意 `gen.pollinations.ai/media/upload` 和
`/v1/media/upload` 都是 404，**只有 `media.pollinations.ai/upload` 存在**。

---

## 九、完整流程

```bash
# 1. 写主控分镜（含 anchor / 每镜时长 / 转场 / 台词）
#    shots.json  —— 每镜必须写 motion_prompt（主体动作）+ camera_motion（相机动作）

# 2. 生图（竖屏原生，免费档 45s/张）
python gen_stills.py --plan shots.json --outdir shots --width 1024 --height 1820

# 3. 去水印（免费层必带）
python clean_watermark.py --indir shots --outdir shots_clean

# 4. 做片段：真动态优先（pollinations → ltx → 运镜兜底）
#    先配好 key：export POLLINATIONS_KEY=sk_xxx
python make_clips.py --plan shots.json --stills shots_clean --out clips \
    --backend pollinations --model bytedance/seedance-2.0-mini
#    没有 key 时：--backend ltx（吃 HF 额度）或 --backend move（兜底）
python make_clips.py --plan shots.json --stills shots_clean --out clips --backend move

# 5. 配音 + 字幕
python tts.py --script script.txt --out audio/voice.mp3 --srt audio/voice.srt --voice xiaoxiao --rate=-6%

# 6. 音乐 + 房间音 + 精确闪避混音
python audio_score.py --duration 28 --out audio/score.wav \
    --room-out audio/room.wav --voice audio/voice.mp3 --mix-out audio/mix.wav

# 7. 装配（逐镜转场 + 字幕 + 首尾淡入淡出）
python assemble.py --plan shots.json --clips clips --out raw.mp4 \
    --audio audio/mix.wav --subtitles audio/voice.srt

# 8. 统一调色
python grade.py --in raw.mp4 --out final.mp4 --preset amber
```

---

## 十、工具链位置

| 脚本 | 作用 |
|---|---|
| `gen_stills.py` | 按主控方案批量生图，自动去重、限速 |
| `clean_watermark.py` | 批量去水印 |
| `camera_move.py` | 2.5D 视差 / 呼吸感 / 手持微抖运镜 |
| `make_clips.py` | 分发到 Pollinations / LTX 真动态，运镜只作兜底 |
| `audio_score.py` | 加法合成 BGM + 房间音 + 精确闪避 |
| `assemble.py` | 逐镜转场、字幕、首尾淡入淡出 |
| `grade.py` | 全片统一调色 + 暗角 + 颗粒 + 溢出 |

---

## 十一、一句话总结

**粗糙不是素材不够好，是各环节各做各的。**

把分镜、时长、台词、色调、音量全部收进一份主控数据，
让每个环节都从它读取；再用测量（暖度、信噪比、时长、亮度连续性）
而不是感觉来验收——片子自然就精致了。

**但"精致"的前提是"真"。** 主体自己没有运动的成片，再怎么调色加颗粒
都还是图片拼接。**运镜不是动态的替代品**——先解决真动态的通道，
再谈精致。
