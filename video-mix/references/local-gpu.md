# 本地 GPU 部署路线

仅当机器有 **NVIDIA 独显 8GB+ 显存** 时适用。当前测试机（Intel 集显 1.5GB）**无法运行**本路线。

## 选型速查

| 显存 | 建议方案 | 说明 |
|---|---|---|
| 6GB | FramePack | 图生视频，最长 60 秒 |
| 8–12GB | Wan2.2 TI2V-5B（GGUF Q4） | 文生视频 + 图生视频二合一，Apache 2.0 可商用 |
| 16GB | Wan2.2 A14B FP8 / HunyuanVideo 1.5 | 甜点档，可量产 |
| 24GB+ | Wan2.2 A14B FP16 | 画质优先，24G 是"放松档" |

**内存也要看**：8GB 显存跑不动 33B，靠的是权重放内存里按需换入（offload）。建议内存 64GB 起步。

## 组件选择

### 统一推理框架

**DiffSynth-Studio**（阿里 ModelScope）——把文生图、图生视频、风格迁移整合在一个可微渲染管线里，适合需要精确控制的任务。

https://github.com/modelscope/DiffSynth-Studio

### 图像生成

- **FLUX.1-schnell**（Apache 2.0）：4 步出图，质量与速度平衡最好
- **SDXL Turbo**：1–4 步极速出图，适合批量草稿
- **Qwen-Image**：中文提示词理解力强

### 视频生成

| 模型 | 参数 | 原生音频 | 许可 | 实用显存 |
|---|---|---|---|---|
| Wan2.2 TI2V-5B | 5B | 无 | Apache 2.0 | 8–12GB（量化） |
| Wan2.2 A14B | 14B MoE | 无 | Apache 2.0 | 24GB+ |
| HunyuanVideo 1.5 | 8.3B | 无 | 腾讯社区许可 | 约 14GB |
| LTX-Video 2.x | 19–22B | 有 | LTX 社区 | 16GB（FP8） |
| FramePack | 约 13B | 无 | 开放权重 | 6GB |
| LongCat-Video | 13.6B | 无 | MIT | 分钟级长视频 |
| Open-Sora 2.0 | 11B | — | — | 支持文/图生视频 |

**许可警告**：MiniMax H3 排除美/欧/英/韩地区，不要用于海外发行。

### 对口型

| 工具 | 特点 | 许可 |
|---|---|---|
| **MuseTalk 1.5** | 实时 30fps+，音质均衡，默认首选 | MIT |
| **LatentSync** | 扩散式，口腔内部细节最锐利，支持 720p+ | Apache 2.0 |
| **Wav2Lip** | 同步精度基准，轻量稳健 | 开源 |
| **Sonic** | 表情随音调变化，情绪对白最佳 | 阿里，ModelScope |
| **VideoRetalking** | 野外场景说话头修复 | Apache 2.0 |
| **Hallo2 / CHAMP** | 长时长高清肖像 / 人体动画 | MIT |

 MuseTalk 项目：https://github.com/TMElyralab/MuseTalk
LatentSync 项目：https://github.com/bytedance/LatentSync

### 配音

- **edge-tts**（云端免费，无需 GPU）——本 skill 默认
- **Piper**：离线神经网络 TTS，44 种语言，音色包 20–90MB
- **ChatTTS**：情感表达自然，适合对话场景
- **CosyVoice / Amphion**：阿里开源，中文情感 TTS 质量高

## ComfyUI 工作流

ComfyUI 是把上述组件串成流水线的最佳载体，官方节点已覆盖 Wan2.2、LTX、HunyuanVideo 等。

整合包推荐（免配置）：
- **秋叶 ComfyUI 整合包**：预装约 50 个插件，含 SageAttention2、FlashAttention2
- **TETAE 整合包**：Python 3.13 + PyTorch 2.6

注意整合包体积约 350GB，磁盘紧张时优先手动装。

## 迁移步骤

从本 skill 的云端流程迁移到本地：

1. 把 `gen_image.py` 的 `fetch()` 换成 ComfyUI API 调用（`POST /prompt` 提交 workflow JSON，`/history/{id}` 轮询结果）
2. 把 `gen_video.py` 的 LTX Space 调用换成 ComfyUI 的 Wan2.2 工作流
3. 把 `lipsync.py` 的 Space 调用换成 MuseTalk 的 `inference.py`
4. `tts.py` 和 `mix.py` **保持不变**——纯本地 ffmpeg，与后端无关

脚本结构已按"后端可替换"设计：`vmix_common.py` 里的 `install_deps()` / `ffmpeg_bin()` / `retry()` 都是通用的，只要替换单个 backend 函数即可。

## 不推荐的做法

- **不要在集成显卡上强行跑**：Intel Iris Plus 的 1.5GB 显存连 SD 1.5 量化都吃力，强行安装只会浪费时间并可能搞坏 Python 环境
- **不要混用 pip 和 conda 的 PyTorch**：CUDA 版本冲突是本地部署最常见的失败原因
- **不要用 `--no-verify` / 跳过签名**：模型权重下载失败要排查网络，不要跳过校验
