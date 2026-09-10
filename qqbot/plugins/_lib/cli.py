"""GNU 风格命令行解析核心 —— 群机器人命令体系。

设计规范见 docs/机器人命令规范.md。本模块**不依赖 nonebot**，可独立单测。

职责：
  1. 命令元数据表（命名空间 / 中英别名 / 一级快捷别名 / 选项定义）
  2. GNU 解析器（--long、--long=value、-s、-svalue、可重复、布尔开关）
  3. QQ 消息归一化（全角空格横线引号、零宽字符、@mention 段）
  4. 中文报错 + 近似纠正（编辑距离 ≤2）
  5. 帮助渲染（单命令 -h 与 /帮助 <命令> 共用）

handler 以 "commands.work:search" 字符串登记，运行时用 importlib 相对导入解析，
避免 cli ↔ commands 循环导入。
"""
from __future__ import annotations

import importlib
import shlex
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# ============================================================ 消息归一化

_FULLWIDTH_MAP = {
    "\u3000": " ",          # 全角空格
    "\uff0d": "-",          # 全角连字符 －
    "\uff0f": "/",          # 全角斜杠 ／（手机输入法易打出）
    "\u2013": "-",          # en dash –
    "\u2014": "-",          # em dash —
    "\u2015": "-",          # horizontal bar ―
    "\uff1d": "=",          # 全角等号 ＝
    "\uff0c": ",",          # 全角逗号（--tags 用）
    "\u201c": '"', "\u201d": '"',   # 中文双引号
    "\u2018": "'", "\u2019": "'",   # 中文单引号
}
_ZERO_WIDTH = "\u200b\u200c\u200d\u200e\u200f\ufeff\u2060"


def normalize(text: str) -> str:
    """全角 → 半角归一化并剔除零宽字符。

    QQ 客户端在手机输入法下极易打出全角空格与「——」，不归一化则 `--limit` 会被
    当成普通文本，命令直接匹配不上。
    """
    out: List[str] = []
    for ch in text:
        if ch in _ZERO_WIDTH:
            continue
        out.append(_FULLWIDTH_MAP.get(ch, ch))
    return "".join(out)


def strip_mentions(text: str) -> str:
    """去掉消息开头的 @某人 段与空白。

    QQ 官方适配器把 @机器人 转成 mention 段，get_plaintext() 后仍可能残留
    "@机器人 " 文本；OneBot v11 同样。不剥离则命令首 token 变成 "@机器人"。
    """
    s = text.strip()
    while s.startswith("@"):
        parts = s.split(None, 1)
        if len(parts) < 2:
            return ""
        s = parts[1].lstrip()
    return s


def tokenize(text: str) -> List[str]:
    """归一化 + shlex 切分。引号不配对时抛 CommandError（中文提示）。"""
    cleaned = strip_mentions(normalize(text))
    try:
        return shlex.split(cleaned, posix=True, comments=False)
    except ValueError as exc:
        raise CommandError(
            f"引号没有配对，无法解析参数（{exc}）。\n"
            '含空格的值请用半角引号包起来，例：--author "爱潜水的 乌贼"'
        ) from exc


def is_bare_code(text: str) -> Optional[str]:
    """裸数字兜底通道：消息去空白后整体就是一个 4~8 位数字 → 返回该码。

    仅用于 /auth bind（注册流程唯一准入通道，群友最自然的动作是直接把页面
    给的码甩给机器人）。判定必须严格：整体匹配，避免把聊天里的普通数字误判。
    """
    s = strip_mentions(normalize(text)).strip()
    if s.isdigit() and 4 <= len(s) <= 8:
        return s
    return None


# ============================================================ 显示宽度

def _display_width(s: str) -> int:
    """东亚宽字符算 2，其余算 1（帮助文本对齐用）。"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(1, width - _display_width(s))


# ============================================================ 数据结构

@dataclass(frozen=True)
class Option:
    """一个命令行选项。

    value_name 为 None 表示布尔开关（不消费后续 token）。
    choices_map: 规范值 → 可接受的别名（如 novel → 小说），大小写不敏感。
    """

    long: str
    short: Optional[str] = None
    value_name: Optional[str] = None
    kind: str = "str"                       # str | int | csv | flag
    repeatable: bool = False
    required: bool = False
    default: Any = None
    help: str = ""
    choices_map: Optional[Dict[str, Tuple[str, ...]]] = None
    min_value: Optional[int] = None
    max_value: Optional[int] = None

    @property
    def is_flag(self) -> bool:
        return self.value_name is None

    @property
    def spec(self) -> str:
        """帮助文本左列，如 `-k, --keyword <词>`。"""
        left = f"-{self.short}, " if self.short else "    "
        tail = f" {self.value_name}" if self.value_name else ""
        return f"{left}--{self.long}{tail}"


HELP_OPTION = Option(long="help", short="h", help="显示本命令的帮助")


@dataclass(frozen=True)
class Command:
    """一条命令（命名空间 + 子命令，或一级命令如 /帮助）。"""

    ns_en: str
    ns_zh: str
    sub_en: Optional[str] = None
    sub_zh: Optional[str] = None
    quick: Tuple[str, ...] = ()             # 一级快捷别名，如 ("search", "搜索")
    summary: str = ""                        # 一行说明（命令目录用）
    brief: str = "[选项]"                    # 目录里的极简用法，如 "-k <词>"
    usage: str = "[选项]"                    # 完整用法的参数部分
    examples: Tuple[str, ...] = ()
    options: Tuple[Option, ...] = ()
    handler: str = ""                        # "commands.work:search"
    admin_only: bool = False
    notes: Tuple[str, ...] = ()
    allow_positional: bool = False           # 仅 /帮助 例外：接受命令名作位置参数

    @property
    def canonical(self) -> str:
        return f"/{self.ns_en}" + (f" {self.sub_en}" if self.sub_en else "")

    @property
    def zh_path(self) -> str:
        return f"/{self.ns_zh}" + (f" {self.sub_zh}" if self.sub_zh else "")

    @property
    def display(self) -> str:
        """面向群友的展示形式：优先中文快捷别名，其次中文路径。

        报错与帮助里用中文（群友实际打的就是中文），canonical 仅用于「别名」行。
        """
        for q in self.quick:
            if any(ord(ch) > 0x2E80 for ch in q):
                return f"/{q}"
        return self.zh_path

    @property
    def aliases(self) -> Tuple[str, ...]:
        """除 canonical 外的全部触发写法（用于帮助里的「别名」行）。"""
        out: List[str] = []
        for first in (self.ns_en, self.ns_zh):
            for second in (self.sub_en, self.sub_zh):
                path = f"/{first}" + (f" {second}" if second else "")
                if path != self.canonical:
                    out.append(path)
        out.extend(f"/{q}" for q in self.quick)
        return tuple(dict.fromkeys(out))

    @property
    def trigger_names(self) -> Tuple[Tuple[str, ...], ...]:
        """全部可触发的 token 组合（中英混搭共 4 种 + 快捷别名）。"""
        out: List[Tuple[str, ...]] = []
        for first in (self.ns_en, self.ns_zh):
            if self.sub_en is None:
                out.append((first,))
            else:
                for second in (self.sub_en, self.sub_zh):
                    out.append((first, second))
        out.extend((q,) for q in self.quick)
        return tuple(dict.fromkeys(out))


class CommandError(Exception):
    """面向群友的中文错误。dispatcher 捕获后直接发送 message。"""

    def __init__(self, message: str, command: Optional[Command] = None):
        super().__init__(message)
        self.message = message
        self.command = command


@dataclass
class ParseResult:
    command: Command
    values: Dict[str, Any] = field(default_factory=dict)
    help_requested: bool = False
    positional: List[str] = field(default_factory=list)

    def get(self, name: str, default: Any = None) -> Any:
        value = self.values.get(name)
        return default if value is None else value


# ============================================================ 近似纠正

def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _suggest(token: str, candidates: Sequence[str], max_distance: int = 2) -> Optional[str]:
    best, best_d = None, max_distance + 1
    for cand in candidates:
        d = _levenshtein(token, cand)
        if d < best_d:
            best, best_d = cand, d
    return best


def _hint_footer(command: Optional[Command]) -> str:
    if command is None:
        return "\n\n输入 /帮助 查看全部命令"
    target = (command.quick[0] if any(
        ord(ch) > 0x2E80 for ch in command.quick[0]
    ) else command.sub_zh) if command.quick or command.sub_zh else command.ns_zh
    return f"\n\n输入 /帮助 {target} 查看全部参数"


# ============================================================ 解析器

def _choice_reverse(opt: Option) -> Dict[str, str]:
    """构建 别名(小写) → 规范值 的反查表。"""
    table: Dict[str, str] = {}
    for canon, aliases in (opt.choices_map or {}).items():
        table[canon.lower()] = canon
        for alias in aliases:
            table[alias.lower()] = canon
    return table


def parse(command: Command, args: Sequence[str]) -> ParseResult:
    """GNU 解析。任何错误抛 CommandError（中文 + 可执行下一步）。"""
    opts: List[Option] = list(command.options) + [HELP_OPTION]
    by_long = {o.long: o for o in opts}
    by_short = {o.short: o for o in opts if o.short}

    values: Dict[str, Any] = {}
    for o in opts:
        if o.repeatable:
            values[o.long] = []
        elif o.is_flag:
            values[o.long] = False
        else:
            values[o.long] = o.default

    tokens: List[str] = list(args)

    # -h 出现在任何位置都优先生效：先整体扫一遍
    if "-h" in tokens or "--help" in tokens:
        return ParseResult(command=command, values=values, help_requested=True, positional=[])

    def assign(opt: Option, value: Any) -> None:
        if opt.repeatable:
            if isinstance(value, list):
                values[opt.long].extend(value)
            else:
                values[opt.long].append(value)
        else:
            values[opt.long] = value

    def coerce(opt: Option, raw: str) -> Any:
        raw = raw.strip()
        if opt.kind == "int":
            try:
                num = int(raw)
            except (TypeError, ValueError):
                raise CommandError(f"⚠️ --{opt.long} 需要数字，收到「{raw}」", command) from None
            if opt.min_value is not None and num < opt.min_value:
                raise CommandError(
                    f"⚠️ --{opt.long} 最小 {opt.min_value}，收到 {num}", command
                )
            if opt.max_value is not None and num > opt.max_value:
                raise CommandError(
                    f"⚠️ --{opt.long} 最大 {opt.max_value}，收到 {num}", command
                )
            return num
        if opt.choices_map:
            table = _choice_reverse(opt)
            canon = table.get(raw.lower())
            if canon is None:
                allowed = " / ".join(
                    f"{c}({'、'.join(a)})" for c, a in opt.choices_map.items()
                )
                raise CommandError(
                    f"⚠️ --{opt.long} 只支持：{allowed}\n收到「{raw}」", command
                )
            return canon
        if opt.kind == "csv":
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            if not parts:
                raise CommandError(f"⚠️ --{opt.long} 的值不能为空", command)
            return parts
        if not raw:
            raise CommandError(f"⚠️ --{opt.long} 的值不能为空", command)
        return raw

    def need_value(opt: Option) -> str:
        example = opt.value_name or "值"
        raise CommandError(
            f"⚠️ 参数 --{opt.long} 后面需要一个值。\n例：--{opt.long} {example}", command
        )

    positional: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]

        if tok == "--":
            positional.extend(tokens[i + 1:])
            break

        # ---------- 长选项 ----------
        if tok.startswith("--"):
            body = tok[2:]
            if not body:
                raise CommandError("⚠️ 出现了空的「--」参数。", command)
            if "=" in body:
                name, inline = body.split("=", 1)
            else:
                name, inline = body, None
            opt = by_long.get(name)
            if opt is None:
                guess = _suggest(name, list(by_long))
                tip = f"\n你是不是想用 --{guess}？" if guess else ""
                raise CommandError(f"⚠️ 未知参数：--{name}{tip}", command)
            if opt.is_flag:
                if inline is not None:
                    raise CommandError(
                        f"⚠️ --{opt.long} 是开关，后面不用跟值（收到「{inline}」）。"
                        f"\n正确写法：--{opt.long}",
                        command,
                    )
                assign(opt, True)
                i += 1
                continue
            if inline is not None:
                assign(opt, coerce(opt, inline))
                i += 1
                continue
            if i + 1 >= len(tokens):
                need_value(opt)
            assign(opt, coerce(opt, tokens[i + 1]))
            i += 2
            continue

        # ---------- 短选项（可连写） ----------
        if tok.startswith("-") and len(tok) > 1:
            chars = tok[1:]
            j = 0
            while j < len(chars):
                ch = chars[j]
                opt = by_short.get(ch)
                if opt is None:
                    guess = _suggest(ch, list(by_short))
                    tip = f"\n你是不是想用 -{guess}？" if guess else ""
                    raise CommandError(f"⚠️ 未知参数：-{ch}{tip}", command)
                if opt.is_flag:
                    assign(opt, True)
                    j += 1
                    continue
                rest = chars[j + 1:]
                if rest:
                    # -n10 连写形式
                    assign(opt, coerce(opt, rest))
                else:
                    if i + 1 >= len(tokens):
                        need_value(opt)
                    assign(opt, coerce(opt, tokens[i + 1]))
                    i += 1
                break
            i += 1
            continue

        # ---------- 位置参数 ----------
        if command.allow_positional:
            positional.append(tok)
            i += 1
            continue
        required_hint = next(
            (o for o in command.options if o.required and o.value_name), None
        )
        if required_hint is not None:
            guide = f"例：{command.display} --{required_hint.long} {tok}"
        else:
            guide = f"例：{command.display} {command.usage}"
        raise CommandError(
            f"⚠️ 「{tok}」不是参数。本机器人的参数一律用 --选项 形式给出。\n\n"
            f"用法：{command.display} {command.usage}\n{guide}"
            f"{_hint_footer(command)}",
            command,
        )

    # ---------- 必填校验 ----------
    for opt in command.options:
        if not opt.required:
            continue
        current = values.get(opt.long)
        missing = current is None or (opt.repeatable and not current)
        if missing:
            short_tip = f"-{opt.short} / " if opt.short else ""
            raise CommandError(
                f"⚠️ 缺少必填参数 {short_tip}--{opt.long}"
                f"\n\n用法：{command.canonical} {command.usage}"
                f"{_hint_footer(command)}",
                command,
            )

    return ParseResult(command=command, values=values, positional=positional)


# ============================================================ 帮助渲染

def render_command_help(command: Command) -> str:
    """单命令帮助：-h/--help 与 /帮助 <命令> 共用。"""
    lines: List[str] = [f"用法：{command.canonical} {command.usage}"]
    if command.aliases:
        lines.append("别名：" + " · ".join(command.aliases))
    lines.append("")

    opts = list(command.options) + [HELP_OPTION]
    width = max(_display_width(o.spec) for o in opts) + 2
    for opt in opts:
        lines.append(f"  {_pad(opt.spec, width)}{opt.help}")

    if command.notes:
        lines.append("")
        lines.extend(f"注：{n}" for n in command.notes)

    if command.examples:
        lines.append("")
        lines.append("示例：")
        lines.extend(f"  {e}" for e in command.examples)

    if command.admin_only:
        lines.append("")
        lines.append("（本命令仅管理员可用）")
    return "\n".join(lines)


def render_catalog(commands: Sequence[Command]) -> str:
    """/帮助 无参数输出：按命名空间分组，控制在 800 字符内。"""
    groups: Dict[str, List[Command]] = {}
    order: List[str] = []
    for cmd in commands:
        key = "【管理】（仅管理员）" if cmd.admin_only else f"【{cmd.ns_zh}】"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(cmd)

    lines: List[str] = ["📖 群资源站机器人命令"]
    rows: List[Tuple[str, str]] = []
    for key in order:
        rows.append((key, ""))
        for cmd in groups[key]:
            shown = cmd.quick[0] if cmd.quick else (cmd.sub_zh or cmd.ns_zh)
            prefix = f"/{shown}"
            if not cmd.quick and cmd.sub_zh:
                prefix = f"/{cmd.ns_zh} {cmd.sub_zh}"
            rows.append((f"{prefix} {cmd.brief}".rstrip(), cmd.summary))

    cmd_widths = [_display_width(left) for left, _ in rows if left and not left.startswith("【")]
    width = (max(cmd_widths) if cmd_widths else 0) + 2
    for left, right in rows:
        if not left:
            continue
        if left.startswith("【"):
            lines.append("")
            lines.append(left)
        else:
            lines.append(f"{_pad(left, width)}{right}")

    lines.append("")
    lines.append("输入 /帮助 <命令> 查看详细用法，如 /帮助 安利")
    return "\n".join(lines)


# ============================================================ 命令注册表

# 用可变容器原地更新（build_registry 不重新绑定），这样任何 `from cli import COMMANDS`
# 的模块都能读到最新注册表，避免 Python 导入绑定陷阱。
COMMANDS: List[Command] = []
_LOOKUP: Dict[Tuple[str, ...], Command] = {}
_NAMESPACE_SUBS: Dict[str, List[str]] = {}


def build_registry(commands: Sequence[Command]) -> None:
    """构建触发名 → 命令 的查找表。启动时调用一次。"""
    COMMANDS.clear()
    COMMANDS.extend(commands)
    _LOOKUP.clear()
    _NAMESPACE_SUBS.clear()
    for cmd in COMMANDS:
        for name in cmd.trigger_names:
            _LOOKUP[name] = cmd
        if cmd.sub_en:
            for ns in (cmd.ns_en, cmd.ns_zh):
                bucket = _NAMESPACE_SUBS.setdefault(ns, [])
                for sub in (cmd.sub_zh, cmd.sub_en):
                    if sub and sub not in bucket:
                        bucket.append(sub)


def find_command(tokens: Sequence[str]) -> Tuple[Optional[Command], List[str], Optional[str]]:
    """按 token 匹配命令。

    返回 (命令, 剩余参数, 错误提示)：
      - 命中：(cmd, rest, None)
      - 命名空间存在但子命令未知：(None, [], 中文错误)
      - 完全不是命令：(None, [], None) —— dispatcher 应放行，不影响聊天
    """
    if not tokens:
        return None, [], None

    for width in (2, 1):
        if len(tokens) >= width:
            cmd = _LOOKUP.get(tuple(tokens[:width]))
            if cmd is not None:
                return cmd, list(tokens[width:]), None

    first = tokens[0]
    subs = _NAMESPACE_SUBS.get(first)
    if subs:
        available = " · ".join(subs)
        if len(tokens) < 2:
            # 只输了命名空间（如 /作品），列出可用子命令
            return None, [], f"/{first} 可用的子命令：{available}"
        guess = _suggest(tokens[1], subs)
        tip = f"\n你是不是想用 /{first} {guess}？" if guess and guess != tokens[1] else ""
        return None, [], (
            f"⚠️ 没有「/{first} {tokens[1]}」这个命令{tip}\n\n"
            f"该命名空间可用：{available}"
        )
    return None, [], None


def load_handler(spec: str) -> Callable:
    """把 "commands.work:search" 解析为可调用对象（相对本包导入）。"""
    module_path, func_name = spec.split(":")
    module = importlib.import_module(f"..{module_path}", package=__package__)
    return getattr(module, func_name)


@dataclass
class MessageMatch:
    """match_message 的结果。

    kind:
      "command"  → 命中已注册命令，command/args 有效，dispatcher 应处理并 block
      "error"    → 像命令但解析/匹配失败，message 为中文报错，dispatcher 发送并 block
      "none"     → 不是命令（普通聊天），dispatcher 应放行
    """

    kind: str
    command: Optional[Command] = None
    args: List[str] = field(default_factory=list)
    message: str = ""


def match_message(text: str) -> MessageMatch:
    """判断一条消息是否命令，并完成命令匹配与初步解析。

    这是 dispatcher 在 nonebot rule 阶段的唯一入口：
      - 非 `/` 开头 → "none"（放行，不影响群友聊天）
      - `/` 开头但未知命令 → "none"（放行，避免 `/某句话` 误触发报错刷屏；
        仅当命中已注册命名空间时才对未知子命令报错）
      - 命中命名空间但子命令未知 → "error"（给可用子命令提示）
      - 命中命令 → "command"
    """
    norm = strip_mentions(normalize(text)) if text else ""
    if not norm.startswith("/"):
        return MessageMatch(kind="none")
    try:
        tokens = tokenize(text)
    except CommandError as exc:
        # 引号不配对等：先看首 token 是否命中已注册命令/命名空间。
        # 命中才报错（疑似命令写错），否则放行（如群友打的 "/it's fine"）。
        head = norm.split(None, 1)[0][1:] if norm.split() else ""
        is_known = head in _NAMESPACE_SUBS or any(
            t[0] == head for t in _LOOKUP
        )
        if is_known:
            return MessageMatch(kind="error", message=exc.message)
        return MessageMatch(kind="none")
    if not tokens or not tokens[0].startswith("/"):
        return MessageMatch(kind="none")

    first = tokens[0][1:]  # 去掉前导 /
    rest = tokens[1:]
    lookup_tokens = [first] + rest

    cmd, args, err = find_command(lookup_tokens)
    if cmd is not None:
        return MessageMatch(kind="command", command=cmd, args=args)
    if err:
        return MessageMatch(kind="error", message=err)
    # 完全未知的 /xxx：放行（可能是群友自己打的 /斜杠文本）
    return MessageMatch(kind="none")
