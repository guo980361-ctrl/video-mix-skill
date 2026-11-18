# 剪映接入：格式、版本与取舍

> 实测记录，2026-11-18。所有结论都基于真实草稿样本和实际生成的工程文件。

## 一、剪映草稿是什么

剪映专业版把每个工程存成一个**文件夹**，核心是两个 JSON：

```
~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/<草稿名>/
├── draft_content.json     时间线主体（轨道 + 素材库）
└── draft_meta_info.json   元信息（名称、封面、时长…）
```

Windows 对应 `%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft\`。
CapCut 国际版用 `~/Movies/CapCut/…`，**格式不通用**，本方案只针对剪映专业版。

### 时间线的数据模型

只有两块真正驱动时间线：

- **`materials`** —— 素材库。每个视频/音频/文本/转场在这里注册一次，拿到一个 `id`
- **`tracks`** —— 轨道。每个轨道是一串 `segments`，通过 `material_id` 引用素材

```jsonc
{
  "materials": {
    "videos":    [{ "id": "r1", "path": "/abs/a.mp4", "duration": 4000000,
                    "width": 1080, "height": 1920, "type": "video" }],
    "audios":    [{ "id": "a1", "path": "/abs/voice.mp3", "duration": 10344000 }],
    "texts":     [{ "id": "t1", "content": "{\"styles\":[…],\"text\":\"…\"}" }],
    "transitions":[{ "id": "x1", "name": "叠化", "duration": 500000,
                     "effect_id": "322577", "resource_id": "6724845717472416269",
                     "is_overlap": true }],
    "speeds":    [{ "id": "s1", "speed": 1.0 }],
    "audio_fades":[{ "id": "f1", "fade_in_duration": 500000 }]
  },
  "tracks": [{
    "type": "video", "name": "main",
    "segments": [{
      "material_id": "r1",
      "target_timerange": { "start": 0, "duration": 4000000 },  // ← 在时间轴上
      "source_timerange": { "start": 0, "duration": 4000000 },  // ← 取素材哪一段
      "speed": 1.0, "volume": 1.0,
      "extra_material_refs": ["s1", "x1"],   // ← 变速/转场挂在这里
      "clip": { "alpha": 1.0, "scale": {"x":1.0,"y":1.0}, "transform": {"x":0.0,"y":0.0} },
      "render_index": 0
    }]
  }]
}
```

### 五个必须记住的规则

1. **时间单位是微秒**（1 秒 = 1,000,000）。写错单位 = 时间轴对不上。
2. **`extra_material_refs` 是挂载点**：变速、转场、淡入淡出、动画的 id 都放这里。
3. **转场挂在「前一个」片段上**，并且要**先**注册进 `materials.transitions`，
   **再**把片段加入轨道。顺序反了转场会丢。
4. **文本素材的 `content` 是"JSON 套 JSON"**——一个被 `json.dumps` 过的字符串。
5. **同一轨道内片段不能重叠**。剪映的自动导入字幕遇到重叠条目会直接失败。

## 二、版本兼容：这是最大的坑

| 剪映版本 | 草稿格式 | 本方案生成的草稿能否打开 |
|---|---|---|
| 5.9 | 明文 `draft_content.json` | ✅ 可以 |
| 6.0 – 10.x | 加密，**正文改名 `draft_info.json`** | ✅ 可以（双文件名 + 可选加密） |
| 11.x | 加密，结构再变（正文移到 `Timelines/<id>/draft_info.json`） | ⚠️ 多半可以，但可能要注册 |

### ⚠️ 文件名：6.0 起必须叫 `draft_info.json`

这是最容易踩、后果最严重的一个坑：

- 5.9 及更早：时间线正文叫 **`draft_content.json`**
- 6.0 及以后：正文改叫 **`draft_info.json`**

用旧名字喂给新版剪映，它会报 **"草稿内容已损坏，请联系客服"**——
而实际内容完全没问题，只是名字不对。

本工具**两个名字都写**（内容相同），哪个版本找哪个都能找到。

### 加密机制（已逆向，纯 Python 可实现）

剪映 6.0 起用 AES-256-GCM 加密草稿文件。算法已被社区逆向：

1. 加密后的文件是一行 base64 文本
2. 文本在 **12 个固定字符偏移**处各"夹带"了 4 个字符：
   - 前 8 组（32 字符）→ AES-256 key
   - 后 4 组（16 字符）→ GCM nonce
   - 这些字符是 ASCII，不是随机二进制
3. 剔除这 48 个字符后，剩下的才是真正的密文 base64
4. 解 base64 得到 `ciphertext || GCM-tag(16 字节)`，用 key/nonce 解密

**关键点：key 和 nonce 就藏在密文自己里面**，所以：

- 不需要 hook、不需要 Frida、不需要本机装剪映
- 不需要调用 `videoeditor.dll` / `libvideoeditor.dylib`
- 任意机器、任意新旧草稿都能离线加解密

偏移表：

```python
OFFSETS = (0x00, 0x07, 0x14, 0x21, 0x28, 0x2F,
           0x3B, 0x42, 0x4C, 0x59, 0x63, 0x7F)
```

本工具的 `jianying_crypto.py` 实现了加解密，依赖 `pycryptodome`。
逆向思路来自社区项目 `si-wang-wen-xuan/JY-Draft-Decrypt`。

> ⚠️ 加密文本长度必须 ≥ 131 字节，这是剪映加密核心的硬性要求。
> 短内容加密时本工具会在 JSON 内部补空格（不影响解析）来满足长度。

### 首页显示还多一层要求（11.x）

剪映 11.x 的草稿要出现在首页，除了正文文件，还需要：

1. `draft_info.json`（加密）
2. ID 一致的 `draft_meta_info.json`
3. `project.json` / `Timelines/<id>/draft_info.json` 等新结构
4. 用户数据目录 `Projects/com.lveditor.draft/root_meta_info.json` 里的**首页注册条目**

`root_meta_info.json` 的确切字段结构没有权威公开文档，各社区实现说法不一。
本方案**不实现**这一步——它高度依赖本机剪映安装，属于"必须真机验证"的部分。

但有个好消息：**剪映启动时会扫描草稿根目录**，自己把新草稿登记进首页。
所以绝大多数情况下，**直接放对文件、重启剪映就能在首页看到**，不需要手动注册。

### 一个安全提醒

部分实现为了省事会**自动关闭剪映进程**。这是危险操作：

- 剪映崩溃时可能留下损坏的草稿
- 强制杀进程有丢失未保存内容的风险

本工具**不碰剪映进程**，只写文件。写完提示你重启剪映即可。

## 三、本方案的三层交付

| 脚本 | 产物 | 依赖 | 适用 |
|---|---|---|---|
| `jianying_draft.py` | 真正的剪映草稿（多轨 + 转场 + 闪避关键帧） | 无（ffmpeg 仅用于探测） | 想要**可继续编辑**的工程 |
| `jianying_crypto.py` | 草稿离线加解密（AES-256-GCM） | `pycryptodome` | 读/改新版剪映的加密草稿 |
| `jianying_pack.py` | 素材包 + SRT + manifest + FCPXML | 无 | 草稿打不开时的**保底**，也用于 Premiere/Resolve |
| `jianying_check.py` | 校验报告 | 无（加密草稿需 pycryptodome） | 草稿打不开时**定位原因** |

### `jianying_draft.py` 生成的东西

- **两个文件名都写**：`draft_info.json`（6.0+）和 `draft_content.json`（5.9），内容相同
- 主视频轨：每个片段独立 segment，转场是剪映原生转场（可改时长/样式）
- 字幕轨：真正的文本片段，能一键换字体/描边/花字——**这是相对烧字幕的最大优势**
- 旁白轨、BGM 轨：分轨摆放
- BGM 自动闪避：按字幕时间轴生成音量关键帧（`KFTypeVolume`），
  有人声时压到 12%，间隙回到 100%
- BGM 短于视频时自动循环补齐（每段都是独立 segment）
- `--encrypt` 开关：需要时输出加密格式

### `jianying_crypto.py` 的用法

```bash
# 解密一个加密草稿（打印到屏幕）
python scripts/jianying_crypto.py -p ~/Movies/JianyingPro/.../draft_info.json

# 解密到文件
python scripts/jianying_crypto.py ~/Movies/JianyingPro/.../draft_info.json

# 把明文草稿加密回去
python scripts/jianying_crypto.py -e draft_content.json
```

> ⚠️ 加密算法的逆向成果来自社区。用于读取**自己**的草稿、做格式迁移没有问题；
> 但请自行确认是否符合所在地区的法律与剪映的服务条款。

### `jianying_pack.py` 的 manifest.json

给"人或 agent"看的结构化说明，每个片段的时间轴位置都写清楚：

```json
{
  "clips": [
    { "index": 1, "file": "clips/clip01.mp4", "duration": 4.0,
      "timeline_start": 0.0, "timeline_end": 4.0 }
  ],
  "voice": { "file": "voice.mp3", "duration": 10.344 },
  "subtitles": [ { "start": 0.1, "end": 3.975, "text": "阳光落在窗台…" } ]
}
```

### FCPXML 的两个坑

写 `premiere.xml` 时踩过，务必注意：

1. **命名空间必须是默认命名空间**。用 `ns1:` 前缀写 `<asset>` 元素，
   Premiere / Resolve 会直接拒导。要 `ET.register_namespace("", FCPXML_NS)`。
2. **时间值用有理数**（`4/1s`），不是小数秒。Resolve 的解析器会拒绝 `4.0s`。

## 四、验证方法

### 格式对拍

`jianying_draft.py` 的 JSON 结构不是照着文档猜的，是**逐字段对拍**出来的：
用 pyJianYingDraft（Apache-2.0）生成一份参照草稿，再和本工具的输出做 key 集合比对，
顶层、materials 各类型、segment、track 全部要求**零差异**。

```bash
python - <<'EOF'
import json
mine = json.load(open("jianying-out/我的混剪/draft_content.json"))
ref  = json.load(open("参照草稿/draft_content.json"))
print(set(mine) ^ set(ref))          # 顶层
print(set(mine["materials"]) ^ set(ref["materials"]))  # 素材表
EOF
```

### 结构校验

```bash
python scripts/jianying_check.py ./jianying-out/我的混剪
```

检查项：JSON 合法性、素材引用是否悬空、片段是否重叠、取用范围是否超出素材时长、
草稿时长与轨道长度是否一致、素材文件是否还在、platform 版本号是否合法。

**剪映草稿损坏时不会弹错误框，只会显示空白时间线**——这个校验器就是为此存在的。

## 五、为什么不做"自动导出成片"

剪映 7.0 起隐藏了导出相关的 UI 控件，社区的 UI 自动化方案（`uiautomation`）
**只在剪映 6 及以下 + Windows 上有效**。macOS 上没有等价方案。

所以本方案的分工是：

- **要成片** → 用 `mix.py`，本地 ffmpeg 一条命令，几秒出片，全程离线
- **要可编辑的工程** → 用 `jianying_draft.py`，在剪映里精修后手动导出

让剪映去做它擅长的（精修、套模板、AI 字幕），让 ffmpeg 做它擅长的（快速出片）。

## 六、素材来源与授权

草稿里引用的都是**本地绝对路径**，剪映直接读原文件，不做拷贝。
好处是体积小、改素材不用同步；代价是**移动或删除源文件会让草稿失效**。

`jianying_pack.py` 则相反：它把素材**复制**进素材包，自包含、可整体搬走。

素材本身的版权（生图结果、字体、音乐）另见 `licensing.md`。
