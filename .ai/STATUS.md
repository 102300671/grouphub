# 进度看板

最后更新：2026-10-26 09:40（Asia/Shanghai）　更新本文件是每次收工的硬性动作。

## 阶段状态

| 阶段 | 状态 | 说明 |
|---|---|---|
| 现状勘察 | ✅ 完成 | 两端工具链路差异、6 处漂移已查清（`FACTS.md` / `RISKS.md`） |
| 设计决策 | ✅ **已批准** | 用户 16:24 回复"还可以"，D1–D4 生效；`.ai/` 已入库 |
| M0 前置核实 | ✅ **全部完成，根因已确证** | ①②③ 全测完。**markdown 段群聊可用**（富文本/代码块/表格全对）。**嵌图根因确证：官方图片语法要带尺寸提示** `![alt #Wpx #Hpx](url)` —— 加上后**手机端正常出图**（②c：E/F 出图、H 无尺寸复现 `[alt]`）。另有独立因素：自建未备案域名的图卡在"加载中"。**"手机上 markdown 嵌图没戏"的旧判断已作废** |
| M1 建共享包 | ⬜ 未开始 | 依赖 M0 排期，不阻塞开工 |
| M2 backend 接入 | ⬜ 未开始 | |
| M3 双协议+判定 | ⬜ 未开始 | |
| M4 图片与 markdown | ⬜ 未开始 | 硬依赖 M0 结论 |
| M5 跨端上下文 | ⬜ 未开始 | |

## 本轮已落地的动作（16:24–16:34）

| 动作 | 结果 |
|---|---|
| `.ai/` 提交 | `09691c6 docs(ai): 新增 .ai 多端协作目录，固化工具链路统一设计`（9 文件 / 498 行）**尚未推送远端** |
| 设备 `backend/.venv` | 已建（`python3 -m venv` 成功），已装 `requirements.txt` 全部包 |
| 依赖缺陷 | 装完自检缺 `markdown` / `playwright`（renderer 必需）→ 已按云端生产版本补装 `Markdown==3.11.1`、`playwright==1.62.0`；`sqlmodel` 确认不需要（项目用 SQLAlchemy） |
| 文档纠错 | R3 原"签名不校验"结论**已推翻并更正**为"签名有效但永不过期"，`FACTS.md` 增补方法论留痕 |
| R3 根因定位 | 确切开关已找到：alist 库 `link_expiration = '0'`（配合 `sign_all = 'true'`），alist 版本 3.65.0。**不能单改这一项**——直链含 sign 会被写进消息正文，开有效期会让历史图片集体裂图，必须与"backend 读时重签"配套（详见 RISKS R3） |
| 站点可达性 | 实测 `http://…:4173/` 通、https 与 alist https 均不通（R1 仍成立） |

## 2026-10-26 轮：M0 测试通道就绪（`/test` 上线生产）

上一版（本文件 10-16 版）列的 M0 阻塞是"需要向 QQ 沙箱群发测试消息，属对外操作"。
本轮用**临时命令**绕开了这个阻塞：**谁给机器人发 `/test`，机器人就把测试内容回发给那个人**——
不刷屏、不指定群、每个人各自看到各自的结果。

| 动作 | 结果 |
|---|---|
| 新增 `qqbot/plugins/commands/test.py` | 临时命令，4 组测试内容（① 图片段 URL 直发 / ② markdown 段 / ③ 纯文本 markdown 源码 / ④ 观察清单） |
| `qqbot/plugins/cli_router.py` | 注册 `test_cmd.COMMANDS`，命令表 **18 → 19 条**（重启日志实测） |
| 测试图直链 | 云服务器 `59.110.163.88` 的 nginx `/var/www/html/m0test/`：`blocks.png`（480×480 四色块，2.0 KB，肉眼易辨认）+ `render1/2.png`（AI 渲染图，26 KB / 24 KB）。**公网可达已实测**（外网 curl 200 + `image/png`） |
| 部署 | 云端 pull → `systemctl restart grouphub-qqbot` → `active`，`NRestarts=0`，日志「命令注册表已构建：19 条命令」 |
| 提交 | `cb2a9cf`（连同此前压着的 3 个 `docs(ai)` 提交一并推送，`0b1f1a1..cb2a9cf`） |

**为什么测试图必须放云服务器**：手机 proot 在 NAT 后面，QQ 服务端拉不到它的地址。
`QQSegment.image(url)` 走 `msg_type=7` + `{"file_type":1,"url":...}`，**由 QQ 服务器自己去拉**，
所以直链必须公网可达。`M0_TEST_IMAGE` 环境变量可覆盖。

**适配器差异（实测自 nonebot-adapter-qq 源码）**：`markdown` 段 → `msg_type=2`，
是 QQ **频道**的能力；`image` 段 → `msg_type=7`。两者在同一条消息里会互相顶掉
（`msg_type` 单值），所以测试内容必须**分条发**。OneBot v11 通道没有 `markdown` 段，命令会自动跳过 ②。

**既有告警（与本轮无关）**：`nonebot_plugin_htmlrender` 导入失败
（`RenderPluginConfig` pydantic 校验错）——10-24 开机日志里同样有，属旧账。
项目自己的 `/渲染` 走 playwright，不依赖该插件。

## 下一步（按优先级）

1. **M0 收尾（唯一遗留）**：②c 的 **G 变体**（自建未备案域名 + 尺寸提示）在手机端显示
   **"加载中"** —— 过几分钟再看一眼：若变成裂图/仍是加载中，则**自建未备案域名这条路不通**，
   图要换到**已备案域名 / CDN**；若是正常出图，则只剩转存延迟。
2. **M1 建共享包**：**新增一项能力 —— 图片尺寸自动探测**（HTTP Range 拉前 64KB，
   解析 PNG IHDR / JPEG / GIF / WebP 头取宽高，照 `tencent-connect/openclaw-qqbot`
   的 `image-size.ts` 做）。M4 的 markdown 嵌图要用它。
3. **M4 方案（已定，不再纠结）**：
   - markdown 段统一负责文字排版 + 图片，**图片必须带 `#宽px #高px`**
   - **不需要**为手机端单独维护一套"渲染成图"分支
   - 兜底：尺寸探测失败 / 地址加载不出来 → `msg_type=7` 独立图片段，或整段渲染成一张图
4. R3 处置：先实测"alist 设置签名有效期后，旧 `:0` 链接是否仍可用"，再决定改配置还是后端按需重签。**属线上配置，需用户确认**。
5. ~~把 `markdown` / `playwright` 写进 `backend/requirements.txt`~~ —— **已完成**（`0a69b61` 已写入 `Markdown==3.11.1` / `playwright==1.62.0`），本文件上一版此处已过期。
6. ~~`.ai/` 与后续提交是否推送远端~~ —— **已完成**（`cb2a9cf` 已推 `origin/main`，云端已 pull）。

## 已知未完成/待清理

- 设备上有两个 9 月遗留备份未跟踪：`deploy/proxy_failover.py.bak.20260924`、`frontend/vite.config.ts.bak.20260924`。
- 云端 `npm audit` 报 2 个 high 级别漏洞，未处理。
- 设备 `backend/.venv` 目前无法完整启动后端服务验证（依赖已齐，但缺 `backend/.env` 配置：`config.py` 无站点基址、`PORT` 未设）。
- **`/test` 是临时命令，验完必须删**（`qqbot/plugins/commands/test.py` + `cli_router.py` 的 import 与注册行）。


## 已完成的相关改动（同一条线索的前置工作）

| 提交 | 内容 | 部署状态 |
|---|---|---|
| `3ced34f` | 前端：scoped 与 v-html 失配导致渲染图样式从未生效，改 `:deep()` 并重设宽度/间距 | ✅ 已上线（4173 产物已重建，备份在云端 `/root/grouphub-dist-backup/dist.20261016_002454`） |
| `0b1f1a1` | 后端：渲染图去掉卡片外壳（body 白底直出，圆角阴影交给前端） | ✅ 已上线（云端 pull + `systemctl restart grouphub-backend`，NRestarts=0） |

这两笔改动本身也印证了本次任务的核心问题：**样式与协议在两端各写一份，改一处只生效一处**（去卡片只对 Web 生效，QQ 端仍用 htmlrender）。

## 下一步（按优先级）

1. 用户批准本设计 → 进入 M0。
2. M0 具体动作：在 QQ 沙箱群实测 markdown 嵌图与代码块/表格支持情况（不改生产代码）。
3. 待用户决定的两件事：
   - `.ai/` 目录**是否提交进 git**（当前是未跟踪状态；建议提交，便于多 agent 与多人协作续接）。
   - 是否在设备上建 `backend/.venv`，让后端也能本地起服务（当前只有 `qqbot/.venv` 可加载 renderer）。

## 已知未完成/待清理

- 设备上有两个旧备份未跟踪：`deploy/proxy_failover.py.bak.20260924`、`frontend/vite.config.ts.bak.20260924`。
- 云端 `npm audit` 报 2 个 high 级别漏洞，未处理（不在本任务范围，但部署时需留意）。

---

内容由 AI 生成
