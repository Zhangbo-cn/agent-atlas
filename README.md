# Agent Atlas / Agent 解剖室

一个面向初学者的 Agent 源码拆解学习站点。目标不是罗列 API，而是沿着一次真实请求，
把 Agent 的循环、钩子、工具、上下文、会话和模型边界讲清楚。

当前第一卷：**Pi Agent**（`earendil-works/pi` v1.4.0 源码快照）。

## 页面

- `index.html` — 站点首页、学习路径、章节入口
- `pi.html` — Pi Agent 深潜（8 节，约 35 分钟）
  - 三个心智模型
  - 项目全景与分层
  - 一次请求的一生
  - Agent Loop 拆解
  - Hook 触发机制
  - Agent Infra 全景
  - 设计取舍与可迁移结论
  - 源码导读、练习、自测与术语表

## 实验

- `experiments/pi-context-compaction.md` — 上下文压缩机制实测：阈值压缩、切点选择、
  工具结果截断、显式/静默超限恢复，附真实请求体、事件流与 session 记录
- `experiments/rig/` — 可复现装置（脚本化 provider + 启动脚本）

## 本地预览

```bash
cd agent-atlas
python3 -m http.server 4173
# 打开 http://127.0.0.1:4173/
```

页面是静态 HTML/CSS/JS，没有构建步骤，也没有运行时依赖。

## 发布到 Artifact

Artifact 宿主会把主页面包进自己的 `<!doctype html>` 骨架，所以发布版首页是一个
body 级片段。`scripts/build_artifact.py` 负责生成它并复制其余文件：

```bash
python3 scripts/build_artifact.py
# dist/index.html  ← 发布入口（片段）
# dist/pi.html     ← 独立完整页面
# dist/assets/     ← 样式与脚本
```

发布时使用 `dist/index.html`，并把 `dist/pi.html`、`dist/assets/site.css`、
`dist/assets/site.js` 作为 supporting files。不要手改 `dist/`，它每次都会重新生成。

## 内容约定

每一卷都按同一个骨架展开，方便横向对照不同 Agent：

1. 心智模型：先纠正最容易误解的概念
2. 架构：包、依赖方向、目录边界
3. 请求生命周期：从输入到落盘的完整链路
4. Loop：调度单位、状态机、并发与中断
5. Hooks：触发点、返回值语义、执行顺序
6. Infra：工具、上下文、持久化、模型、UI
7. 设计取舍：哪些是本质，哪些是产品选择
8. 源码导读：可执行的阅读路线与练习

写作要求：不废话、关键简洁、通俗但准确；每个结论尽量落到文件名、函数名或行号。

## 下一卷候选

- Claude Code：工具权限、hooks、子 Agent、计划模式
- Codex：沙箱、审批模型、执行循环
- 横向专题：工具协议、多 Agent 调度、评测

## 说明

本站是独立的学习项目，与 Pi 项目无隶属关系。源码行号基于 v1.4.0，
升级版本后请以本地 checkout 为准。
