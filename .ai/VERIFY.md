# 验证与对比方法

原则：**只验一端不算完成**；每个阶段收尾必须跑本文件对应清单，并把结果数字写回 `STATUS.md`。

## V1 · 三向一致性（每次开工先跑）

设备侧：

    cd /root/code/grouphub && git rev-parse HEAD && git status --porcelain
    md5sum backend/app/renderer.py frontend/src/views/AIView.vue

云端侧：

    cd /root/code/grouphub && git rev-parse HEAD
    md5sum backend/app/renderer.py frontend/src/views/AIView.vue

远端：`git ls-remote origin main`

基线（2026-10-16 14:57 实测）：三者 HEAD 同为 `0b1f1a1`；`renderer.py` = `80b8dba671e123c7d68b85f6111824f7`；`AIView.vue` = `5277293a7de22aa2ba1bd436529771aa`。对不上说明有人在别处改过，先查清再动手。

注意：设备上这些命令**不要接管道**（`| head` 会报 `Function not implemented`）；网络类 git 命令必须加 `timeout` 与 `GIT_SSH_COMMAND="ssh -o BatchMode=yes"`，否则会挂死整轮。

## V2 · 本地渲染冒烟（改渲染相关代码前必跑）

探针三步：加载 `backend/app/renderer.py` → 把 `_LAUNCH_ARGS` 换成含 `--use-gl=swiftshader` → 调 `render_markdown_img(md, 540)` 写 `/tmp/sw_out.png`。解释器用 `qqbot/.venv/bin/python3`（设备唯一同时具备 markdown + playwright 的环境）。

| 情形 | 实测结果 |
|---|---|
| 缺 swiftshader | 卡死，70s 超时被杀（exit 124） |
| 带 swiftshader | md 图 25341B / text 图 24513B，约 2.6s |

产物自检项：文件头为 PNG 魔数；尺寸 1192×1600（逻辑 596×800）；表头品牌蓝 `#3370ff` 像素约 9.2 万；左上角像素 `255,255,255`（无卡片灰底）。

## V3 · 两端渲染对比（M2 / M4）

同一份输入分别由 backend 与 qqbot 出图，逐项比对：

1. 画布宽高（当前已知不一致：420/540/600 对 680/860/900，即漂移 1）
2. 是否存在卡片灰底边带（漂移 2 的直接体现）
3. 中文字形来源环境（设备 Noto 40 条 / 云端 Droid 1 条）——**必须在结论里标注是在哪台跑出来的**
4. 上述度量写成可复用脚本留数字，不允许只靠肉眼判断

## V4 · 手册与执行器一致性（M1 / M2 自动化，防漂移复发）

遍历 registry 中所有 `Tool` 并断言：

- `params` 文案中出现的默认值数字 == `defaults` 字段 == handler 实际使用的默认值
- `namespace:name` 集合在「手册」「执行表」「native tools 数组」三处完全相等
- text 模式手册的包列表 == registry 已注册包列表
- native 模式 tools 数组**不含** `pkg:activate`，且所有下发工具名满足 `^[a-zA-Z0-9_-]{1,64}$`

任一断言失败禁止合入。

## V5 · 协议与容错回归（M3）

必须覆盖的用例（现两份正则的行为都要保住）：

1. 标准形式：`<tool_call>` 内首行 `web:search`、次行 `query=泰拉瑞亚 最新版本`
2. 闭标签漂移：闭合写成 think / function / invoke / tool 等错误标签时仍能解析
3. 训练格式杂标签残留（parameter、bare 开闭标签行）需被清除且不进入最终回复
4. 工具名被尖括号包裹、名字不含冒号（协议错误）时：保留调用并在执行层回错让模型自纠
5. 只有 function 块、没有 tool_call 外壳时的回退解析
6. 一次回复含多个调用块
7. `strip_tool_calls` 后正文不留 XML 残渣、不产生 3 个以上连续空行
8. 安全：图片 URL 仅允许 http(s) 与同源相对路径，`javascript:` 必须被拒（现有逻辑，不可回退）
9. native 与 text 两种模式对同一工具调用的解码结果（name 归一后 + args）必须逐字节相等

## V6 · 能力判定测试（M3）

- mock 三种端点：支持原生、不支持原生、直接报错。断言判定分别为 native / text / unknown。
- unknown 不得写入缓存表；已判定组合不得重复探测（计数断言）。
- 同 model 不同 api_base 必须产生两条独立判定。
- 人工覆盖值优先于缓存与探测。
- 运行期 native 失效：本次回落 text 且标记待复核，不静默改库。

## V7 · 同步与上下文（M5）

- 断言 `ai_messages` 行数不因跨端行为增加（无镜像消息）。
- 网页提问后立刻在 QQ 提问，AI 能引用上文；反向同理。
- 注入块受条数与字符上限约束，超长历史不撑爆请求。
- 注入内容不参与前端气泡渲染，历史还原与改前一致。

## V8 · 部署后线上核验（每次 M2 之后的阶段）

1. 云端 `git pull` 后 `systemctl restart grouphub-backend`，等 6s 查 `NRestarts=0`、`SubState=running`、`/openapi.json` 200
2. `journalctl -u grouphub-backend -n 12` 无报错（注意：`/api/health` 本来就不存在，404 不算故障）
3. 前端改动需 `npm run build` 后核对产物选择器与 md5
4. 云端与设备的产物文件名与哈希应一致（此前实测逐字节相同，不一致即说明源码或环境分叉）
5. 回滚路径必须写在提交信息里，不要只靠记忆

---

内容由 AI 生成
