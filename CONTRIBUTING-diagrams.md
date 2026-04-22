# Diagram Style Guide (Contributor Reference)

> 维护者文档,不面向终端用户。所有产品文档(`docs/**`)中的 mermaid 图都应遵循本指引。

## 商业级调色板

所有 `classDef` / `style` 必须使用下表中的语义色,禁止自创 hex:

| 语义 | fill | stroke | color | 用途 |
|---|---|---|---|---|
| **entry** | `#0984e3` | `#0566b3` | `#fff` | 用户入口、起点、端口 |
| **process** | `#00b894` | `#009577` | `#fff` | 常规处理步骤 |
| **decision** | `#fdcb6e` | `#e0a33e` | `#333` | 判定、分支、门闸 |
| **data** | `#a29bfe` | `#6c5ce7` | `#fff` | 持久化存储、密钥、缓存 |
| **gateway** | `#fdcb6e` | `#e0a33e` | `#333` | Nginx / Stream 分流器(决策语义) |
| **xray** | `#a29bfe` | `#6c5ce7` | `#fff` | xray 内核及其组件 |
| **sing** | `#55efc4` | `#00b894` | `#333` | sing-box 内核及其组件 |
| **external** | `#dfe6e9` | `#636e72` | `#333` | 第三方系统、互联网、伪装站点 |
| **warning** | `#ff7675` | `#d63031` | `#fff` | 高亮但非错误 |
| **error** | `#d63031` | `#b71c1c` | `#fff` | 失败、阻断、fail-closed |
| **terminal** | `#2d3436` | `#636e72` | `#fff` | 终点、进程移交(`os.execvp`) |

### 完整 classDef 片段(复制即用)

```
classDef entry    fill:#0984e3,stroke:#0566b3,stroke-width:2px,color:#fff
classDef process  fill:#00b894,stroke:#009577,stroke-width:2px,color:#fff
classDef decision fill:#fdcb6e,stroke:#e0a33e,stroke-width:2px,color:#333
classDef data     fill:#a29bfe,stroke:#6c5ce7,stroke-width:2px,color:#fff
classDef external fill:#dfe6e9,stroke:#636e72,stroke-width:2px,color:#333
classDef warning  fill:#ff7675,stroke:#d63031,stroke-width:2px,color:#fff
classDef error    fill:#d63031,stroke:#b71c1c,stroke-width:2px,color:#fff
classDef terminal fill:#2d3436,stroke:#636e72,stroke-width:3px,color:#fff
```

简单图(≤6 节点)也可以只用 2–3 类,不必全套引入。

## 形状约定

| 形状 | 语法 | 语义 |
|---|---|---|
| 圆角矩形 stadium | `X(["标签"])` | 入口 / 终点 / 用户 |
| 矩形 | `X["标签"]` | 常规步骤 |
| 圆角矩形 subroutine | `X[["标签"]]` | 进程移交 / 外部服务 |
| 菱形 diamond | `X{"标签"}` | 决策点 |
| 六边形 | `X{{"标签"}}` | 网关 / 分流器 |
| 圆形 | `X(("标签"))` | 核心组件(xray / sing-box) |
| 柱体 database | `X[("标签")]` | 持久化数据、配置文件 |

## 图类型选择

- **`flowchart TD` / `flowchart TB`** — 自上而下的流程、生命周期。默认首选。
- **`flowchart LR`** — 线性数据流、架构视角切片,宽度不超 6-8 个节点。
- **`sequenceDiagram`** — 协议握手、请求响应时序。
- **`graph TD`** — **已废弃**;用 `flowchart TD` 替代(新版 Mermaid 等价,`flowchart` 支持更多特性)。

## 配套文字

每个图**必须**有前导和后续文字:
- **前导段落**: 一句话说明这张图展示什么(what) + 为什么值得看(why)。
- **图例(可选)**: 形状 / 颜色→含义的简要对照,当图内类别 >3 时加。
- **走读(可选但推荐)**: 分 3-5 步文字走读决策点,告诉读者"先看哪里,再看哪里"。

标准不是"每张图都写 200 字",而是"每张图都有入口"。

## 反模式

- ❌ 纯黑白方框无 classDef(视觉上死气沉沉)
- ❌ 自创十六进制色(`#f96`, `#f9f`, `#6f9`, `#ff6b6b`, `#4ecdc4`, `#95e1d3`, `#61dafb`, `#b19cd9`, `#98fb98`)
- ❌ 节点标签写成一整句话(用 `<br/>` 分行或拆成两个节点)
- ❌ 图与正文无关联,读者只能看完从头开始猜
- ❌ 同一份文档内混用 `graph LR` / `flowchart LR` / `graph TD` / `flowchart TD`
- ❌ 标签内出现开发阶段术语(Phase/Sprint/M1 等,参见全局规则 `common/documentation.md`)

## 审查 checklist

提交文档改动前自检:
- [ ] 所有 mermaid 块都用 `flowchart` 语法(不用 `graph`)
- [ ] `classDef` / `style` 都来自本文的调色板
- [ ] 图前一句话前言 + 图后走读(至少其一)
- [ ] 节点标签无动词长句,长内容用 `<br/>` 分行
- [ ] 无 `#f96` `#f9f` `#6f9` `#ff6b6b` `#4ecdc4` `#95e1d3` 等历史色
