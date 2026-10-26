# grouphub AI 工具链路统一 —— 多端 / 多 Agent 协作入口

> 任何 AI 或人接手本任务前，先读完本文件，再看 `STATUS.md` 确认做到哪一步。
> 生成时间：2026-10-16（Asia/Shanghai）

## 项目一句话

群资源站（grouphub）有两个 AI 入口——Web 前端 `/ai` 页与 QQ 机器人 `/ai` 命令，二者共用同一套后端会话体系，但**工具链路是两份独立实现**，已产生 6 处行为漂移。本任务把它们统一为一个共享包，并同时支持模型原生 tool_call 与高兼容性文本协议。

## 系统拓扑（现状）

```
        浏览器 /ai 页                    QQ 群 / 私聊
             │                                │
      backend/app/api/ai.py            qqbot/plugins/commands/ai.py
      (1400 行，循环内联)               (1336 行，循环内联)
             │                                │
   _build_tool_table(请求级)          build_qq_agent()（进程级一次）
             │                                │
      backend/app/renderer.py        qqbot/plugins/_lib/renderer.py
      (自写 CSS, 纯 playwright)      (nonebot-plugin-htmlrender)
             └──────── 同一套 8 工具 ────────┘
                            ▲
                            │  M1 已建：shared/aitools/（仓库根，唯一实现）
                            └── registry · prompt · protocol/textcodec · loop
                                tools/{web,site} · ports · imagesize
```

**M1 后**：QQ 端已切到共享包；**backend 端仍是旧的两份私有实现**（M2 接入）。
两端暂时仍是两套，**但工具实现本身已经只有一份**（QQ 侧），漂移面在收窄。

三台机器分工（**改动一律先落在本地设备**）：

| 角色 | 路径 | 用途 |
|---|---|---|
| 本地设备 localdev（安卓 Termux + PRoot） | `/root/code/grouphub` | 唯一开发机，改代码 + 前端构建 |
| 云服务器 cloud（阿里云） | `/root/code/grouphub` | 生产：nginx 4173 服务 dist、`grouphub-backend.service`、`grouphub-qqbot.service` |
| GitHub | `git@github.com:102300671/grouphub.git` main | 唯一同步通道，两台都从这里走 |

## 接手时的硬规则

1. **只在本地设备改代码**，云端只 `git pull` + 构建 + 重启，云端临时副本已弃用。
2. 云端重启服务、改 `.env`、改 nginx 属**有副作用操作**，需用户明确确认后再做。
3. 设备上服务按需手动启动；"服务没在跑"是正常状态，不要当异常上报。
4. **PRoot 陷阱**：命令管道接 `head` 会报 `Function not implemented`；`ps` 的 `lstart` 全是假值只看 `etime`；`ss`/`netstat` 不可用，端口探测用 `nc -z` 或 `curl`；`git fetch` 会报 `packed-refs: Function not implemented`，网络类 git 操作必须加 `timeout` 与 `GIT_SSH_COMMAND="ssh -o BatchMode=yes"`。
5. 本地跑 playwright 出图**必须**加 `--use-gl=swiftshader`，否则卡死（已实测，详见 `FACTS.md`）。
6. 改任何渲染相关代码，必须同时核对 Web 端与 QQ 端两端表现，只验一端不算完成。
   **M0 又补了一条同性质的纪律**：QQ 端内部还要**手机端 + 电脑端各看一遍** ——
   markdown 里的图片在手机端和电脑端表现可能不同（缺尺寸提示时手机端只显示 alt 文本）。
   凡"能不能显示"的结论，**必须两端都看**（见 `VERIFY.md` V0）。
8. **共享包（`shared/aitools/`）的改动是跨端改动**：改端口签名 → 两端的 ports 适配器都要跟；
   改工具文案 → 直接改发给模型的 prompt。合入前必须绿：
   `qqbot/.venv/bin/python -m unittest discover -s shared/tests -t .`（V10 第 1 步）
   + QQ 端插件导入冒烟（V10 第 2 步）。**共享包不 import nonebot/fastapi**（D1 约束），
   日志只用标准库 `logging`（QQ 端靠 `aitools_host.py` 桥接 loguru，见 RISKS R14）。
7. **阶段收尾必须写回 `.ai`，且写回在提交之前** —— 详见下一节《阶段收尾写回协议》。
   这条是从 M0 的漏写事故来的：M0 实测做完了、代码也推了，但**没有把结论按 PLAN.md
   的要求写进 `RISKS.md` / `DECISIONS.md` / `VERIFY.md`，也没建 PLAN.md 指定的产出物**，
   导致阶段"看起来完成"而知识没沉淀 —— 下一个人接手会重复劳动或被过期结论带偏。

---

## 阶段收尾写回协议（**每个阶段都要走**）

### 时点与顺序（固定）

1. 代码在**本地设备**改完并自测
2. 部署到云端 + 线上核验
3. **跑 `VERIFY.md` 该阶段的清单，取数字**
4. **写回 `.ai`（见下表）**
5. **提交**（文档与代码**同一笔**）
6. 汇报"阶段完成"

> **第 4 步在第 5 步之前。** 文档没写回，阶段就不算完成、也不提交 ——
> 这样"代码提交了但文档没写"在 git 历史上就不可能发生。

### 写回哪个文件

| 文件 | 写什么 | 何时必写 |
|---|---|---|
| `STATUS.md` | 阶段状态行、本轮动作表、下一步、待决项 | **每阶段收尾** |
| `FACTS.md` | 新实测事实（带核验日期 + 端/环境） | 每轮实测后 |
| `VERIFY.md` | 跑过的清单与数字；基线过期就更新基线 | 跑清单时 / 基线变动时 |
| `RISKS.md` | 相关风险的状态流转（🔴→🟡→✅）与处置结果 | 阶段动了对应风险时 |
| `DECISIONS.md` | 被验证/推翻的决策条目（**原文保留 + 更正**，不删历史） | 结论影响某条决策时 |
| `PLAN.md` | 阶段状态、范围变化、验收结论 | 阶段状态变化时 |
| `ARCHITECTURE.md` | 模块职责、分层变化 | 结构变化时 |
| `CONTRACT.md` | 接口契约、命名、错误约定 | 接口变化时 |
| `<阶段产出物>.md` | `PLAN.md` 该阶段指定的那个文件 | **PLAN.md 明确要求时** |

### 三道门禁（收尾自查）

1. **收尾前重读 `PLAN.md` 该阶段的「产出 / 验收 / 回滚」三行** ——
   那是**这一阶段的交作业清单**，不是背景资料。M0 就是漏了这一步。
2. **写回在 commit 之前完成**，文档与代码同一笔提交。
3. commit 后自查 `git show --stat`：该阶段要求的文件**是否都动了**；
   汇报"完成"前 `git status` 必须干净（除已知未跟踪项）。

### 为什么要有这三条

M0 期间我漏写过三样东西，而它们**恰恰是项目文档已经写好的要求**：
`PLAN.md` 指定的产出物没建、`RISKS.md` / `DECISIONS.md` 没按 PLAN.md 的指示更新、
`VERIFY.md` 的基线早就过期了却没人重测。
**根因不是"忘了"，是把项目文档当成了"要查的资料"而不是"要交的作业"。**
所以协议的重点不是"记得写"，是**把写回做成流程上的前置条件**。

## 文档索引

| 文件 | 内容 | 什么时候读 |
|---|---|---|
| `STATUS.md` | **进度看板**：做到哪了、下一步是什么 | 每次接手第一读 |
| `M0-markdown-可行性实测.md` | **M0 阶段完整实测记录**（三轮原始数据、官方文档依据、结论与踩坑留痕） | 动 QQ 端图片/markdown 前 |
| `DECISIONS.md` | 4 项架构决策及被否方案 | 想改设计方向前 |
| `ARCHITECTURE.md` | 现状与目标架构、分层与模块职责 | 写代码前 |
| `CONTRACT.md` | 共享包接口契约（结构体、命名、错误约定） | 实现模块时 |
| `PLAN.md` | M0–M5 阶段划分、验收标准、回滚方式 | 排期与提交时 |
| `VERIFY.md` | 验证与对比方法、基线数字 | 每阶段收尾 |
| `FACTS.md` | **已实测事实基线**，避免重复劳动 | 怀疑某结论时 |
| `RISKS.md` | 阻塞项与安全/合规风险 | 每次评审 |

---

内容由 AI 生成
