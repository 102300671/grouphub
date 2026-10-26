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
      (1400 行，循环内联)               (1336 行，循环在 _lib/aitools.py)
             │                                │
   _build_tool_table(请求级)          register_tool(进程级全局表)
             │                                │
      backend/app/renderer.py        qqbot/plugins/_lib/renderer.py
      (自写 CSS, 纯 playwright)      (nonebot-plugin-htmlrender)
             └──────── 同一套 8 工具，两套实现 ────────┘
```

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
