"""
helpers.py — Shared utilities: formatting, keyboards, captcha, rate-limiting, and spam-check.
Standardized for Kurigram / Pyrogram MTProto API.
"""

import asyncio
import datetime
import html
import random
import re
import time
from typing import Optional, Tuple
import aiohttp
from pyrogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup
import config
from config import LOGGER

UTC = datetime.timezone.utc


# ═══════════════════════════════════════════════════════════════════════════════
#  TEXT & TEMPLATE FORMATTER
# ═══════════════════════════════════════════════════════════════════════════════
class fmt:
    """Static helpers for dynamic variable replacement and safe Telegram HTML."""

    @staticmethod
    def escape(text: str) -> str:
        return html.escape(str(text)) if text else ""

    @staticmethod
    def mention(user_id: int, name: str) -> str:
        return f'<a href="tg://user?id={user_id}">{fmt.escape(name)}</a>'

    @staticmethod
    def render(
        template: str,
        user=None,
        chat=None,
        invite_link: Optional[str] = None,
        total_members: Optional[int] = None,
    ) -> str:
        """
        Replace dynamic tags in welcome/broadcast messages with real values.
        Variables: {mention}, {first_name}, {last_name}, {full_name}, {username},
                   {user_id}, {chat_title}, {chat_id}, {date}, {time}, {weekday},
                   {invite_link}, {total_members}
        """
        if not template:
            return ""

        now = datetime.datetime.now(UTC)
        first_name = getattr(user, "first_name", None) or "User"
        last_name = getattr(user, "last_name", None) or ""
        full_name = f"{first_name} {last_name}".strip()
        username = f"@{user.username}" if getattr(user, "username", None) else "N/A"
        user_id = str(getattr(user, "id", 0))

        subs = {
            "{mention}": fmt.mention(int(user_id), first_name),
            "{first_name}": fmt.escape(first_name),
            "{last_name}": fmt.escape(last_name),
            "{full_name}": fmt.escape(full_name),
            "{username}": username,
            "{user_id}": user_id,
            "{chat_title}": fmt.escape(getattr(chat, "title", None) or "Channel"),
            "{chat_id}": str(getattr(chat, "id", 0)),
            "{date}": now.strftime("%Y-%m-%d"),
            "{time}": now.strftime("%H:%M UTC"),
            "{weekday}": now.strftime("%A"),
            "{invite_link}": invite_link or "N/A",
            "{total_members}": str(total_members) if total_members is not None else "N/A",
        }

        result = template
        for tag, val in subs.items():
            result = result.replace(tag, val)
        return result

    @staticmethod
    def parse_buttons(raw: str) -> Tuple[str, Optional[Markup]]:
        """
        Parse inline buttons formatted as:
        [Button Name | https://link.com] [Button 2 | https://link2.com]
        [Callback Button | cb:custom_callback_data]
        """
        lines = raw.splitlines()
        clean = []
        rows = []
        pat = re.compile(r"\[([^\|]+)\|([^\]]+)\]")

        for line in lines:
            matches = pat.findall(line)
            stripped = line.strip()
            if matches and stripped.startswith("[") and stripped.endswith("]"):
                row = []
                for label, target in matches:
                    label, target = label.strip(), target.strip()
                    if target.startswith("cb:"):
                        row.append(Btn(label, callback_data=target[3:]))
                    elif target.startswith(("http://", "https://", "tg://")):
                        row.append(Btn(label, url=target))
                if row:
                    rows.append(row)
            else:
                clean.append(line)

        markup = Markup(rows) if rows else None
        return "\n".join(clean).strip(), markup


# ═══════════════════════════════════════════════════════════════════════════════
#  INLINE KEYBOARDS
# ═══════════════════════════════════════════════════════════════════════════════
class kb:
    """Inline keyboard builders for clean UI/UX navigation."""

    @staticmethod
    def main(bot_username: str, is_admin_user: bool = False) -> Markup:
        rows = [
            [
                Btn("➕ Add to Channel", url=f"https://t.me/{bot_username}?startchannel=botstart&admin=invite_users+manage_chat"),
                Btn("➕ Add to Group", url=f"https://t.me/{bot_username}?startgroup=botstart&admin=invite_users+manage_chat"),
            ],
            [
                Btn("📢 My Channels & Groups", callback_data="chats:1"),
                Btn("📊 Statistics", callback_data="global_stats"),
            ],
            [
                Btn("❓ Help & Setup", callback_data="help"),
                Btn("⚙️ Manage Settings", callback_data="chats:1"),
            ],
        ]
        if is_admin_user:
            rows.append([Btn("📡 Broadcast Suite", callback_data="broadcast_menu")])
        return Markup(rows)

    @staticmethod
    def back(target: str = "main") -> Markup:
        return Markup([[Btn("🔙 Back", callback_data=target)]])

    @staticmethod
    def cancel(target: str = "main") -> Markup:
        return Markup([[Btn("❌ Cancel", callback_data=target)]])

    @staticmethod
    def chat_list(chats: list, page: int = 1, page_size: int = 5) -> Markup:
        import math
        total_pages = max(1, math.ceil(len(chats) / page_size))
        page = min(max(1, page), total_pages)
        slice_ = chats[(page - 1) * page_size : page * page_size]

        rows = []
        for c in slice_:
            icon = "🟢" if c.get("auto_approve", True) else "🔴"
            title = c.get("title", f"Chat {c.get('chat_id')}")
            rows.append([Btn(f"{icon} {title}", callback_data=f"chat:{c['chat_id']}")])

        nav = []
        if page > 1:
            nav.append(Btn("◀️ Previous", callback_data=f"chats:{page - 1}"))
        nav.append(Btn(f"📄 {page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            nav.append(Btn("Next ▶️", callback_data=f"chats:{page + 1}"))

        if nav:
            rows.append(nav)

        rows.append([
            Btn("🔄 Refresh", callback_data=f"chats:{page}"),
            Btn("🔙 Main Menu", callback_data="main"),
        ])
        return Markup(rows)

    @staticmethod
    def chat_settings(chat_id: int, cfg: dict) -> Markup:
        aa = "🟢 ON" if cfg.get("auto_approve", True) else "🔴 OFF"
        cap = "🟢 ON" if cfg.get("captcha", False) else "🔴 OFF"
        wel = "🟢 ON" if cfg.get("welcome", {}).get("enabled", True) else "🔴 OFF"
        pfp = "🟢 ON" if cfg.get("filters", {}).get("require_pfp", False) else "🔴 OFF"
        cas = "🟢 ON" if cfg.get("filters", {}).get("cas_check", True) else "🔴 OFF"
        delay = cfg.get("delay", 0)

        return Markup([
            [Btn(f"⚡ Auto-Approve: {aa}", callback_data=f"toggle:aa:{chat_id}")],
            [
                Btn(f"🛡 Captcha: {cap}", callback_data=f"toggle:cap:{chat_id}"),
                Btn(f"⏳ Delay: {delay}s", callback_data=f"set_delay:{chat_id}"),
            ],
            [
                Btn(f"👋 Welcome: {wel}", callback_data=f"welcome:{chat_id}"),
                Btn("🎨 Buttons", callback_data=f"wel_btns:{chat_id}"),
            ],
            [
                Btn(f"🖼 Require Avatar: {pfp}", callback_data=f"toggle:pfp:{chat_id}"),
                Btn(f"🚫 Anti-Spam (CAS): {cas}", callback_data=f"toggle:cas:{chat_id}"),
            ],
            [
                Btn("⚡ Backlog Actions", callback_data=f"mass:{chat_id}"),
                Btn("📊 Analytics", callback_data=f"chat_stats:{chat_id}"),
            ],
            [
                Btn("🗑 Remove Chat", callback_data=f"del_chat:{chat_id}"),
                Btn("🔙 Channels List", callback_data="chats:1"),
            ],
        ])

    @staticmethod
    def delay_picker(chat_id: int) -> Markup:
        opts = [
            ("⚡ Instant (0s)", 0),
            ("⏱ 5 Seconds", 5),
            ("⏱ 15 Seconds", 15),
            ("⏱ 30 Seconds", 30),
            ("⏱ 1 Minute", 60),
            ("⏱ 5 Minutes", 300),
        ]
        rows = []
        row = []
        for label, val in opts:
            row.append(Btn(label, callback_data=f"delay:{chat_id}:{val}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([Btn("🔙 Back", callback_data=f"chat:{chat_id}")])
        return Markup(rows)

    @staticmethod
    def mass_actions(chat_id: int) -> Markup:
        return Markup([
            [Btn("✅ Approve All Pending Requests", callback_data=f"mass_approve:{chat_id}")],
            [Btn("❌ Decline All Pending Requests", callback_data=f"mass_decline:{chat_id}")],
            [Btn("📥 Export Pending Requests (CSV)", callback_data=f"mass_export:{chat_id}")],
            [Btn("🔙 Back to Settings", callback_data=f"chat:{chat_id}")],
        ])

    @staticmethod
    def welcome_editor(chat_id: int, wcfg: dict) -> Markup:
        en = "🟢 Enabled" if wcfg.get("enabled", True) else "🔴 Disabled"
        pm = "📬 DM (PM)" if wcfg.get("send_pm", True) else "📢 In Chat"
        has_media = bool(wcfg.get("media_id"))

        return Markup([
            [Btn(f"Status: {en}", callback_data=f"toggle:wel:{chat_id}")],
            [Btn(f"Target: {pm}", callback_data=f"toggle:wel_pm:{chat_id}")],
            [Btn("✏️ Edit Welcome Text / Tags", callback_data=f"set_wel_text:{chat_id}")],
            [Btn("🖼 Change / Remove Media" if has_media else "➕ Attach Media (Photo/Video)", callback_data=f"set_wel_media:{chat_id}")],
            [Btn("👁 Preview Message", callback_data=f"preview_wel:{chat_id}")],
            [Btn("🔙 Back to Settings", callback_data=f"chat:{chat_id}")],
        ])


# ═══════════════════════════════════════════════════════════════════════════════
#  CAPTCHA GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════
_EMOJIS = ["🍎", "🚀", "🐱", "💎", "⭐", "🎉", "🔥", "🌈", "🍕", "⚡", "🎸", "🎯"]


def make_captcha(kind: str, chat_id: int) -> Tuple[str, Markup, str]:
    """
    Generate verification challenge (button, math equation, or emoji match).
    Returns (text, Markup, answer).
    """
    if kind == "math":
        a, b = random.randint(1, 9), random.randint(1, 9)
        correct = a + b
        opts = {correct}
        while len(opts) < 4:
            w = correct + random.choice([-3, -2, -1, 1, 2, 3, 4])
            if w > 0:
                opts.add(w)
        shuffled = list(opts)
        random.shuffle(shuffled)

        text = (
            "🛡️ <b>Security Verification</b>\n\n"
            f"Please solve this math equation to join the chat:\n"
            f"👉 <b>{a} + {b} = ?</b>"
        )
        btns = []
        row = []
        for n in shuffled:
            ok = "1" if n == correct else "0"
            row.append(Btn(str(n), callback_data=f"captcha:{chat_id}:math:{ok}"))
            if len(row) == 2:
                btns.append(row)
                row = []
        if row:
            btns.append(row)
        return text, Markup(btns), str(correct)

    if kind == "emoji":
        sample = random.sample(_EMOJIS, 4)
        target = random.choice(sample)
        text = (
            "🛡️ <b>Security Verification</b>\n\n"
            f"Please tap the matching emoji to join:\n"
            f"🎯 Target: <b>{target}</b>"
        )
        btns = [[]]
        for e in sample:
            ok = "1" if e == target else "0"
            btns[0].append(Btn(e, callback_data=f"captcha:{chat_id}:emoji:{ok}"))
        return text, Markup(btns), target

    # Default: 1-Click Human Verification
    text = (
        "🛡️ <b>Security Verification</b>\n\n"
        "Please tap the button below to confirm you are human and complete your join request."
    )
    return text, Markup([[Btn("🟢 I am Human — Verify", callback_data=f"captcha:{chat_id}:button:1")]]), "verified"


# ═══════════════════════════════════════════════════════════════════════════════
#  TOKEN BUCKET RATE LIMITER
# ═══════════════════════════════════════════════════════════════════════════════
class RateLimiter:
    """
    Token bucket rate limiter to keep outgoing Telegram requests under rate limits.
    Handles FloodWait exponential backoff automatically.
    """

    def __init__(self, rate: float = 25.0, capacity: float = 30.0):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()
        self._flood_until: float = 0.0

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                if now < self._flood_until:
                    sleep_time = self._flood_until - now
                else:
                    self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.rate)
                    self._last = now
                    if self.tokens >= 1.0:
                        self.tokens -= 1.0
                        return
                    sleep_time = (1.0 - self.tokens) / self.rate

            await asyncio.sleep(sleep_time)

    def flood_wait(self, seconds: float) -> None:
        LOGGER.warning(f"FloodWait received: pausing approvals for {seconds}s")
        self._flood_until = max(self._flood_until, time.monotonic() + seconds + 0.5)


limiter = RateLimiter(rate=float(config.MAX_APPROVALS_PER_SECOND))


# ═══════════════════════════════════════════════════════════════════════════════
#  ANTI-SPAM & SECURITY CHECKER
# ═══════════════════════════════════════════════════════════════════════════════
async def check_spam(user, filters_cfg: dict, client) -> Tuple[bool, Optional[str]]:
    """
    Evaluate anti-spam rules for an incoming join request.
    Returns (is_approved, reason).
    """
    if not filters_cfg:
        return True, None

    # Username rule
    if filters_cfg.get("require_username") and not getattr(user, "username", None):
        return False, "Missing @username"

    # CAS Global Ban List Check
    if filters_cfg.get("cas_check", True) and config.ENABLE_CAS_CHECK:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://api.cas.chat/check?user_id={user.id}",
                    timeout=aiohttp.ClientTimeout(total=3.0),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("ok"):
                            return False, "Flagged in CAS anti-spam database"
        except Exception:
            pass

    # Profile Photo Requirement
    if filters_cfg.get("require_pfp"):
        try:
            has_pfp = False
            async for _ in client.get_chat_photos(user.id, limit=1):
                has_pfp = True
                break
            if not has_pfp:
                return False, "No profile photo uploaded"
        except Exception:
            pass

    return True, None
