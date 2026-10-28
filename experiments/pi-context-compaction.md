# 实验：pi 的上下文压缩机制

这篇是实测记录，不是 API 说明。所有数字来自三次真实运行：pi 进程真实启动、真实发 HTTP、
真实执行 bash 工具、真实写 session 文件；只有模型端换成了一个按脚本回话的本地
OpenAI 兼容端点（`experiments/rig/mock-provider.py`），这样同一段 trace 可以复现，
也能精确控制「请求有多大」「什么时候返回 400」。

实验环境：

| 项 | 值 |
|---|---|
| pi | `packages/coding-agent/dist/cli.js`，`--mode json --print` |
| 模型声明 | `contextWindow: 8192`，`maxTokens: 1024` |
| 压缩设置 | `reserveTokens: 2500`，`keepRecentTokens: 3500` / `1800` |
| 工具 | 只开 `bash`，`seq` / `awk` 产生大输出 |
| 配置隔离 | `PI_CODING_AGENT_DIR=/tmp/pi-lab/agent` |

复现：

```bash
python3 experiments/rig/mock-provider.py     # 127.0.0.1:4873
./experiments/rig/run.sh threshold 3500 t1   # 阈值压缩
./experiments/rig/run.sh overflow  1800 o1   # 显式超限恢复
./experiments/rig/run.sh silent    1800 s1   # 静默超限
```

每次运行的产物：`req-NNN.json`（pi 发出去的完整请求体）、`events.jsonl`（`--mode json` 事件流）、
`sessions/*.jsonl`（落盘会话）。

---

## 一、现象

同一个 8K 窗口下的会话，请求体在三轮里翻了十倍：

| 请求 | 类型 | 消息数 | 请求体 | 说明 |
|---|---|---|---|---|
| req-001 | agent | 2 | 6,186 字节 | 系统提示 + 用户指令 |
| req-002 | agent | 4 | 18,565 字节 | 多了 1 次 bash 结果 10,082 字符 |
| req-003 | agent | 6 | 71,498 字节 | 又多了 1 次 bash 结果 51,297 字符 |
| req-004 | **summarization** | 2 | 6,006 字节 | 压缩请求，只有摘要提示词 |
| req-005 | agent | 4 | 8,189 字节 | 压缩后继续干活 |

事件流（`events.jsonl`）：

```json
{"type":"compaction_start","reason":"threshold"}
{"type":"compaction_end","reason":"threshold","result":{
  "tokensBefore":17245,"estimatedTokensAfter":379,"firstKeptEntryId":"8ce7368c"}}
```

session 文件里多出一条记录，后面所有上下文都从它开始重建：

```json
{"type":"compaction","id":"4aeab09c","tokensBefore":17245,
 "firstKeptEntryId":"8ce7368c","summary":"No prior history.\n\n---\n\n**Turn Context (split turn):**…",
 "usage":{"input":1501,"output":220,"totalTokens":1721},
 "details":{"readFiles":[],"modifiedFiles":[]}}
```

三个数字说明了机制的骨架：**17,245 → 379 tokens**，切点是 `8ce7368c`（一条 assistant 消息），
摘要本身花了 1,721 tokens。

---

## 二、问题与原因

### 2.1 膨胀几乎全部来自工具结果，不是对话

req-003 里，系统提示 2,535 字节、用户指令 96 字节、两条 assistant 消息各 15 字节；
剩下 61,379 字节全是两条 `tool` 消息。`estimateTokens()` 对 toolResult 也是按字符数 / 4 粗算
（`compaction.ts:281`），所以工具输出直接决定了何时触发压缩。

真正危险的是单条结果的量级：`seq 1 5000` 带 padding 后原始输出 **118,893 字节**，
一条消息就能吃掉半个窗口。

### 2.2 不能简单“删掉旧消息”

工具调用是成对的：`toolCall(id=call_step_1)` 和 `toolResult(toolCallId=call_step_1)`。
从中间随便切一刀，会留下一个没有结果的调用，或一个没有调用方的结果，多数 provider 直接报错。
`findCutPoint()` 因此只允许切在 user/assistant 上，**永远不切 toolResult**：

```ts
function isCutPointMessage(message: AgentMessage): boolean {
  switch (message.role) {
    case "user": case "assistant": case "bashExecution":
    case "custom": case "branchSummary": case "compactionSummary": return true;
    case "toolResult": return false;          // compaction.ts:332
  }
}
```

### 2.3 一次 turn 可能本身就超预算

本次实验就是这么撞上的：最后一条工具结果 51,297 字符 ≈ 13,000 tokens，比
`keepRecentTokens`（3,500）大得多。往回累计时，`findCutPoint()` 一碰到它就超预算，
只能把切点落在这一轮内部的 assistant 消息上——这就是「split turn」。
于是 `prepareCompaction()` 把这一轮拆成两段：`turnStartIndex` 之前进摘要，之后原样保留。

### 2.4 阈值判定是滞后的

`shouldCompact` 用的是「上一次响应的 usage + 其后消息的估算」：

```ts
export function estimateContextTokens(messages: AgentMessage[]): ContextUsageEstimate  // compaction.ts:216
export function shouldCompact(contextTokens, contextWindow, settings) {
  return contextTokens > contextWindow - settings.reserveTokens;                       // compaction.ts:250
}
```

trace 印证了这一点：第 2 轮响应返回 usage 3,839（低于 8192−2500=5,692），第 3 轮响应返回
17,072，压缩发生在**第 4 轮开始之前**（`_compactBeforeNextAssistantResponse`，
`agent-session.ts:545`）。也就是说，真正超限的那一轮请求已经发出去了，压缩是“补一枪”，
不是“提前拦”。`reserveTokens` 就是给这一枪留的余量。

---

## 三、解决方案

pi 分了五层，从便宜到贵依次兜。

### 第 1 层：入口截断，别让工具结果进上下文

bash 工具在**写进消息之前**就截断了，默认 2,000 行 / 50KB，并把完整输出落到临时文件
（`tools/truncate.ts:11`）：

```
[Showing lines 1001-3000 of 3000. Full output: /tmp/pi-bash-b102a029dfd03e7b.log]
```

第二条命令撞到字节上限：

```
[Showing lines 3721-5000 of 5000 (50.0KB limit). Full output: /tmp/pi-bash-d291574b5b44711f.log]
```

模型看到的是尾部 2,000 行/50KB 加一句“完整输出在哪”，需要时再 `read`。
这一步决定了后面所有数值的上限。

### 第 2 层：阈值触发

两个入口，都走同一个判断：

- **每轮开始前**：`_compactBeforeNextAssistantResponse()`（`agent-session.ts:545`）——本次实验走的就是它
- **每轮结束后**：`_checkCompaction()`（`agent-session.ts:2206`）

默认值写在 `DEFAULT_COMPACTION_SETTINGS`（`compaction.ts:147`）：`reserveTokens: 16384`、
`keepRecentTokens: 20000`，可按模型覆盖（`compaction.modelOverrides["provider/model"]`）。

### 第 3 层：选切点，保证 tool_use/tool_result 不被拆散

`findCutPoint()`（`compaction.ts:418`）从最新消息往回累计 token，累计到
`keepRecentTokens` 就停，然后**向后找最近的合法切点**。合法切点只可能是
user/assistant/bashExecution/custom/summary。

本次 trace 的切点：

```
entries: … user → a@7023c87c(toolCall) → tool 12,113B → a@91a3c02e(toolCall) → tool 52,608B → a@8ce7368c(toolCall) → tool 922B
cut    :                                                                  ↑ firstKeptEntryId=8ce7368c
```

`8ce7368c` 是 assistant，不是 user，所以 `isSplitTurn = true`，这一轮被拆成
「prefix（前两条工具结果）」和「suffix（8ce7368c + 它的小结果 922 字节）」。

### 第 4 层：摘要请求本身也要瘦身

摘要请求只发一条 user 消息，格式是 `<conversation>…</conversation>` + 固定模板。
序列化时逐条压缩（`compaction/utils.ts:109`）：

- 文本消息完整保留
- 每个工具结果**截到 2,000 字符**，追加 `[... N more characters truncated]`
- 思考内容单独成段，工具调用压成 `bash(command="…", description="…")`

实测这一层省下来的量：原始 61,379 字节的工具输出，进摘要提示词后只剩 4,962 字符。
其中的截断标记：

```
[Tool result]: 1001
1002
…
[... 8082 more characters truncated]

[Assistant tool calls]: bash(command="seq 1 5000 | awk …", description="Print 5000 padded lines")
[Tool result]: 1721 padded-padded-…
[... 49297 more characters truncated]
```

系统提示只有一句话（`utils.ts:156`）：

> Do NOT continue the conversation. Do NOT respond to any questions in the conversation. ONLY output the structured summary.

输出必须是一个固定格式的检查点（`## Goal` / `## Progress` / `## Next Steps` / `## Critical Context`…，
`compaction.ts:482`）。split turn 时还要再发一次「turn prefix」摘要，用另一套更短的模板
（`compaction.ts:850`），两次结果拼成一条：

```
No prior history.

---

**Turn Context (split turn):**

## Goal
…
```

> 注：`No prior history.` 是代码里的字面量兜底（历史段没有消息可总结时用它），
> 不是模型输出。这一行会连同 `---` 一起进入后续上下文，属于可以改进的粗糙点。

摘要里的工具结果是有损的，所以额外把文件操作单独记了一份，附在摘要末尾：

```ts
function formatFileOperations(readFiles, modifiedFiles) → "<read-files>…</read-files>\n\n<modified-files>…</modified-files>"
```

对应 session 里的 `details: { readFiles, modifiedFiles }`，下一次压缩会把它并进新的文件清单。

### 第 5 层：把压缩结果写回上下文

`appendCompaction()` 落盘一条 `compaction` 记录，之后 `buildSessionContext()` 只做两件事：
从 `firstKeptEntryId` 开始保留原始条目，并在最前面插一条 user 消息
（`core/messages.ts:11`）：

```
The conversation history before this point was compacted into the following summary:

<summary>
…摘要…
</summary>
```

o1（超限恢复）压缩后的第一个请求只剩 2 条消息，那条“摘要用户消息”912 字符：

| 请求 | 消息数 | 请求体 | 角色 |
|---|---|---|---|
| req-003（压缩前） | 6 | 68,051 字节 | system, user, assistant, tool, assistant, tool |
| req-005（压缩后） | 2 | 3,552 字节 | system, user |

阈值压缩（t1）不一样：切点落在 assistant 上，所以压缩后是 4 条消息
（system、摘要 user、被保留的 assistant、它对应的 tool 结果），8,189 字节。

注意：压缩不吃掉「模型自己的最后一条回答」，它只把历史折叠成一段 user 文本，
所以后续请求在语义上仍然是「用户贴了一份摘要，请继续」。

---

## 四、超限恢复

阈值压缩是“预防”，超限恢复是“急救”。两者触发条件不同，代码路径也不同
（`_checkCompaction()`，`agent-session.ts:2206`）。

### 4.1 显式超限：provider 报错

实验 o1 让 mock 在第三次请求时返回真实格式的 400：

```json
{"error":{"message":"Input length (265330) exceeds model's maximum context length (262144).","type":"invalid_request_error"}}
```

pi 侧的事件流：

```
assistant message_end stopReason=error  errorMessage=400: {"message":"Input length (265330) exceeds …"}
compaction_start  reason=overflow
compaction_end    reason=overflow  willRetry=true  tokensBefore=16664  after=185
assistant message_end stopReason=toolUse        ← 重试成功
assistant message_end stopReason=stop
```

识别靠一组正则（`packages/ai/src/utils/overflow.ts`），覆盖 Anthropic / OpenAI /
Gemini / xAI / Groq / OpenRouter / Together / llama.cpp / LM Studio / DashScope 等不同措辞，
并用 `NON_OVERFLOW_PATTERNS` 排除限流类误判（Bedrock 的
`ThrottlingException: Too many tokens` 会被 `/too many tokens/i` 命中，属于必须排掉的假阳性）。

恢复动作只有三步（`agent-session.ts:2262-2276`）：

1. 把这条报错的 assistant 消息从 `agent.state.messages` 里摘掉（session 文件里仍然保留，便于回放）
2. `_runAutoCompaction("overflow", willRetry=true)`
3. 压缩完成后 `agent.continue()` 重放这一轮

重试**只给一次**。第二次再撞上同样的错误就直接报错收工：

```
Context overflow recovery failed after one compact-and-retry attempt.
Try reducing context or switching to a larger-context model.
```

另外有个容易忽略的细节：压缩后 `buildSessionContext()` 可能把刚被摘掉的那条错误消息
（因为 `firstKeptEntryId` 落在它前面）重新装回来，所以 `_runAutoCompaction()` 在
`continue()` 之前又清了一次尾部 assistant（`agent-session.ts:2465`）。没有这一步，
`agent.continue()` 会因为「最后一条是 assistant」而拒绝运行。

### 4.2 静默超限：provider 不报错，但 usage 已经越界

有些后端（z.ai 一类）不返回错误，直接接受超长输入。pi 用 usage 反推：

```ts
// overflow.ts，Case 2
if (contextWindow && message.stopReason === "stop") {
  const inputTokens = message.usage.input + message.usage.cacheRead;
  if (inputTokens > contextWindow) return true;
}
```

实验 s1 复现了这条路径：响应正常结束（`stopReason=stop`），但 `usage.totalTokens=30308`
超过 8,192：

```
compaction_end reason=overflow willRetry=false tokensBefore=30308 after=206
```

`willRetry=false` 的原因写在代码注释里：这条回答是**成功**的，`agent.continue()`
没法从一条已完成的 assistant 消息继续，所以只压缩、不重试。

同一文件里还有第三种：`stopReason=length` 且 `output=0`、输入几乎填满窗口
（小米 MiMo 会静默截断输入）——用「填满窗口 + 零输出」这个组合信号判定。

---

## 五、几个坑

**1. `keepRecentTokens` 和巨型工具结果耦合。**
本次 `keepRecentTokens` 设 3,500 还是 1,800，压缩结果完全一样（都是 379 tokens 之后）：
因为往回数第一条就是 52,608 字节的工具结果，两个预算都在它那里越界，切点自然相同。
调小这个参数并不能“多保留一点”，只会让切点在同一处徘徊。

**2. 摘要调用不走缓存。**
`completeSummarization()` 显式设置 `cacheRetention: "none"`（`compaction.ts:606`），
并给没有 session 的调用方发一个新 routing id。一次性的摘要不该污染主会话的 prompt cache。

**3. 摘要失败必须丢弃，不能落盘。**
`getSummarizationFailure()` 把 `stopReason=error` 和 `stopReason=length` 都当成失败：
半截的摘要是最坏的情况——它看起来像检查点，实际丢了关键上下文。摘要调用还额外禁止
模型发工具调用（`Summarization attempted to call a tool`）。

**4. 压缩后仍可能立刻再次压缩。**
`_checkCompaction()` 会跳过「早于最近一次压缩边界」的 assistant usage
（`assistantIsFromBeforeCompaction`），否则被保留下来的旧消息带着压缩前的巨大 usage，
一进下一轮就再触发一次压缩，形成抖动。

---

## 六、这套设计的可迁移结论

1. **在工具边界做预算，不要在消息边界做预算。** 工具结果是唯一的量级失控点，
   入口截断 + 落盘路径（`Full output: /tmp/pi-bash-*.log`）比任何摘要策略都便宜。
2. **切点选择是正确性问题，不是优化问题。** 只切 user/assistant、保住 tool 配对，
   是这套机制能稳定运行的前提；split turn 的双段摘要是为它付的代价。
3. **摘要也要被摘要。** 序列化阶段截 2,000 字符，是把「压缩请求本身不超限」也纳入设计。
4. **超限恢复要区分“失败”和“成功但越界”。** 前者删消息重试一次，后者只压缩不重试；
   把两者混在一起，会出现“重试一条已经完成的回答”这种无效动作。
5. **状态重建要和文件一致。** 压缩是追加一条记录 + 从 `firstKeptEntryId` 重建上下文，
   而不是就地修改历史；这让回放、分支、撤销都还能工作。

---

## 附录：原始证据

| 文件 | 内容 |
|---|---|
| `experiments/traces/request-table.json` | 三次运行的请求规模表 |
| `experiments/traces/t1-compaction-entry.json` | 阈值压缩写入 session 的完整记录 |
| `experiments/traces/o1-compaction-events.jsonl` | 显式超限恢复的事件序列 |
| `experiments/rig/mock-provider.py` | 脚本化 provider（记录每个请求体） |
| `experiments/rig/run.sh` | 单次实验启动脚本 |

自测：

1. 为什么 `findCutPoint()` 允许切在 assistant 上，却要禁止切在 toolResult 上？
2. 一次响应 usage 显示 `input=7000, cacheRead=1500`，窗口 8192，`reserveTokens=16384`
   时会发生什么？走哪条分支？
3. 压缩后第一个请求里，`<summary>` 是用哪种 role 发出去的，为什么？
4. `willRetry=true` 只在什么条件下出现？为什么成功响应越界时不重试？
