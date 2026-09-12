"""
plugins/managechnls.py — Unified Channel Control Center.

Wraps admin toggles, welcome media/button editor, goodbye message builder,
live previews, premium feature gates, and cached channel listings.
"""

import asyncio
from typing import Dict, Optional
from pyrogram import Client, filters, ContinuePropagation
from pyrogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
import config
from config import LOGGER
from database import db
from core.cache import get_cached_chat, set_cached_chat, invalidate_chat
from core.permissions import can_manage_chat
from core.quota import is_feature_allowed, get_chat_plan
from helpers import fmt, style, ui

# User conversation state for custom text/media editing:
# { user_id: { "action": str, "kind": str ("welcome"|"goodbye"), "chat_id": int } }
_editor_states: Dict[int, dict] = {}


# ─── Main /managechnls Command ──────────────────────────────────────────────
@Client.on_message(filters.command(["managechnls", "managechannels", "channels", "manage"]))
async def cmd_managechnls(client: Client, msg: Message):
    uid = msg.from_user.id
    try:
        await _render_channel_list(client, msg, uid, page=1)
    except Exception as e:
        LOGGER.error(f"/managechnls render failed for user {uid}: {e}")
        await msg.reply_text(f"{style.h('Something went wrong loading your channels')}. Please try again.")


async def _render_channel_list(client: Client, target, user_id: int, page: int = 1):
    """Render list of channels managed by user_id."""
    from pyrogram.errors import MessageNotModified

    is_admin_user = config.is_admin(user_id)

    # NOTE: this used to filter every chat through can_manage_chat()
    # (core/permissions.py, a live per-chat check) fed by
    # get_cached_admin_chats() (core/cache.py) — neither of which I've
    # reviewed. That path could legitimately return zero chats (and, if it
    # raised, leave the Refresh button's spinner hanging with no q.answer())
    # even when the DB already had chats correctly attributed to this user.
    # /admin (start.py) uses a plain owner_id match successfully, so this
    # does the same thing first and only falls back to the live check for
    # chats with no recorded owner_id (e.g. ones added before chat_added.py
    # started stamping it).
    all_chats = await db.all_chats(owner_id=None if is_admin_user else user_id)
    managed_chats = list(all_chats)

    if not is_admin_user:
        unowned = [c for c in await db.all_chats() if c.get("chat_id") not in {x["chat_id"] for x in managed_chats} and not c.get("owner_id")]
        for c in unowned:
            try:
                if await can_manage_chat(user_id, c["chat_id"], client):
                    managed_chats.append(c)
            except Exception:
                pass

    if not managed_chats:
        text = (
            f"{style.h('Unified Channel Control Center')}\n\n"
            "⚠️ <b>No managed channels found.</b>\n\n"
            "To manage a channel or group:\n"
            "1. Add this bot to your channel as an Administrator.\n"
            "2. Ensure you have <i>Invite Users via Link</i> permissions.\n"
            "3. Send /managechnls again to configure settings!"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(style.btn("Refresh"), callback_data="mchnls_list:1")],
            [InlineKeyboardButton(style.btn("Main Menu"), callback_data="main")],
        ])
        try:
            if isinstance(target, CallbackQuery):
                await target.message.edit_text(text, reply_markup=markup)
            else:
                await target.reply_text(text, reply_markup=markup)
        except MessageNotModified:
            pass
        return

    page_size = 5
    import math
    total_pages = max(1, math.ceil(len(managed_chats) / page_size))
    page = min(max(1, page), total_pages)
    slice_ = managed_chats[(page - 1) * page_size : page * page_size]

    rows = []
    for c in slice_:
        state_str = style.on(c.get("auto_approve", True))
        title = c.get("title", f"Chat {c['chat_id']}")
        rows.append([InlineKeyboardButton(f"📢 {title}  ·  {state_str}", callback_data=f"mchnls_chat:{c['chat_id']}")])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(style.btn("Previous"), callback_data=f"mchnls_list:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(style.btn("Next"), callback_data=f"mchnls_list:{page + 1}"))

    if nav:
        rows.append(nav)

    rows.append([
        InlineKeyboardButton(style.btn("Refresh"), callback_data=f"mchnls_list:{page}"),
        InlineKeyboardButton(style.btn("Main Menu"), callback_data="main"),
    ])

    text = (
        f"{style.h('Unified Channel Control Center')}\n\n"
        f"Select a channel to configure auto-approvals, welcome/goodbye messages, captchas, and filters:"
    )
    markup = InlineKeyboardMarkup(rows)

    try:
        if isinstance(target, CallbackQuery):
            await target.message.edit_text(text, reply_markup=markup)
        else:
            await target.reply_text(text, reply_markup=markup)
    except MessageNotModified:
        # Tapped Refresh with nothing changed — already showing the right
        # thing, nothing to do (previously unhandled, which could leave
        # the button's loading spinner hanging).
        pass


# ─── Per-Channel Control Center Menu ────────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^mchnls_chat:(-?\d+)$"))
async def cb_mchnls_chat(client: Client, q: CallbackQuery, chat_id: Optional[int] = None):
    # chat_id is accepted explicitly because other handlers (mchnls_tgl,
    # mchnls_delay_set) call this function directly to redraw the control
    # center screen after an update, instead of going through Pyrogram's
    # dispatch — that means q.matches still holds THEIR regex match, not
    # this handler's own "^mchnls_chat:(-?\d+)$" pattern. Reading
    # q.matches[0].group(1) in that case grabbed the wrong group (e.g. the
    # toggle key "cas" instead of the chat id) and crashed with
    # ValueError: invalid literal for int(). Only fall back to parsing
    # q.matches when chat_id isn't supplied by the caller (i.e. this is a
    # real dispatch through the ^mchnls_chat:(-?\d+)$ regex).
    if chat_id is None:
        chat_id = int(q.matches[0].group(1))
    uid = q.from_user.id

    # Permission check via core/permissions.py
    if not await can_manage_chat(uid, chat_id, client):
        await q.answer("❌ Access Denied: You are not an administrator of this channel.", show_alert=True)
        return

    cfg = await get_cached_chat(chat_id, db.get_chat)
    if not cfg:
        cfg = {"chat_id": chat_id}

    title = cfg.get("title", f"Chat {chat_id}")
    stats = cfg.get("stats", {})
    approved = stats.get("approved", 0)
    rejected = stats.get("rejected", 0)
    plan = await get_chat_plan(chat_id)

    # Feature gate checks via core/quota.py — bypassed entirely while
    # config.PUBLIC_MODE is on (bot running free-for-everyone).
    has_gb = config.PUBLIC_MODE or await is_feature_allowed(chat_id, "goodbye", uid)
    has_pfp = config.PUBLIC_MODE or await is_feature_allowed(chat_id, "require_pfp", uid)
    has_cas = config.PUBLIC_MODE or await is_feature_allowed(chat_id, "cas_check", uid)

    aa = style.on(cfg.get("auto_approve", True))
    cap = style.on(cfg.get("captcha", False))
    wel = style.on(cfg.get("welcome", {}).get("enabled", True))
    gb = style.on(cfg.get("goodbye", {}).get("enabled", False)) if has_gb else "🔒 PRO"
    pfp = style.on(cfg.get("filters", {}).get("require_pfp", False)) if has_pfp else "🔒 PRO"
    cas = style.on(cfg.get("filters", {}).get("cas_check", True)) if has_cas else "🔒 PRO"
    delay = cfg.get("delay", 0)

    rows = [
        [InlineKeyboardButton(f"{style.btn('Auto-Approve')}  ·  {aa}", callback_data=f"mchnls_tgl:aa:{chat_id}")],
        [
            InlineKeyboardButton(f"{style.btn('Captcha')}  ·  {cap}", callback_data=f"mchnls_tgl:cap:{chat_id}"),
            InlineKeyboardButton(f"{style.btn('Delay')}  ·  {delay}s", callback_data=f"mchnls_delay:{chat_id}"),
        ],
        [
            InlineKeyboardButton(f"{style.btn('Welcome')}  ·  {wel}", callback_data=f"mchnls_wel:{chat_id}"),
            InlineKeyboardButton(
                f"{style.btn('Goodbye')}  ·  {gb}" if has_gb else "⭐ Upgrade for Goodbye",
                callback_data=f"mchnls_gb:{chat_id}" if has_gb else f"mchnls_upg:{chat_id}:goodbye",
            ),
        ],
        [
            InlineKeyboardButton(
                f"{style.btn('Require Avatar')}  ·  {pfp}" if has_pfp else "⭐ Unlock Avatar Filter",
                callback_data=f"mchnls_tgl:pfp:{chat_id}" if has_pfp else f"mchnls_upg:{chat_id}:require_pfp",
            ),
            InlineKeyboardButton(
                f"{style.btn('Anti-Spam CAS')}  ·  {cas}" if has_cas else "⭐ Unlock CAS Filter",
                callback_data=f"mchnls_tgl:cas:{chat_id}" if has_cas else f"mchnls_upg:{chat_id}:cas_check",
            ),
        ],
        [
            InlineKeyboardButton("👁️ Live Preview Welcome", callback_data=f"mchnls_prev:wel:{chat_id}"),
            InlineKeyboardButton("👁️ Live Preview Goodbye", callback_data=f"mchnls_prev:gb:{chat_id}"),
        ],
        [
            InlineKeyboardButton(style.btn("Backlog Actions"), callback_data=f"mass:{chat_id}"),
            InlineKeyboardButton(style.btn("Analytics"), callback_data=f"chat_stats:{chat_id}"),
        ],
        [InlineKeyboardButton("🗑 Remove Chat", callback_data=f"mchnls_del_confirm:{chat_id}")],
        [
            InlineKeyboardButton(style.btn("Back to Channels"), callback_data="mchnls_list:1"),
        ],
    ]

    text = (
        f"{style.h('Control Center')} — <b>{fmt.escape(title)}</b>\n"
        f"<code>{chat_id}</code>  ·  Plan: <b>{plan}</b>\n\n"
        f"{style.h('Quick Stats')}\n"
        f"• {style.kv('Approved', f'<b>{approved:,}</b>')}\n"
        f"• {style.kv('Rejected', f'<b>{rejected:,}</b>')}\n\n"
        f"<i>Tap any setting below to toggle options or customize messages.</i>"
    )

    await ui.edit(q.message, text, reply_markup=InlineKeyboardMarkup(rows))
    await q.answer()


# ─── Channel List Pagination / Refresh (was unwired — every Refresh/Next/
# Previous button in the control center pointed here and did nothing) ───────
@Client.on_callback_query(filters.regex(r"^mchnls_list:(\d+)$"))
async def cb_mchnls_list(client: Client, q: CallbackQuery):
    page = int(q.matches[0].group(1))
    try:
        await _render_channel_list(client, q, q.from_user.id, page=page)
    except Exception as e:
        LOGGER.error(f"mchnls_list render failed for user {q.from_user.id}: {e}")
        await q.answer("Something went wrong loading your channels. Try again.", show_alert=True)
        return
    await q.answer()


# ─── Delay Picker (was unwired — "Delay" button had no handler) ────────────
_DELAY_OPTIONS = [
    ("Instant (0s)", 0), ("5 Seconds", 5), ("15 Seconds", 15),
    ("30 Seconds", 30), ("1 Minute", 60), ("5 Minutes", 300),
]


@Client.on_callback_query(filters.regex(r"^mchnls_delay:(-?\d+)$"))
async def cb_mchnls_delay(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    uid = q.from_user.id
    if not await can_manage_chat(uid, chat_id, client):
        await q.answer("❌ Permission denied.", show_alert=True)
        return

    rows, row = [], []
    for label, val in _DELAY_OPTIONS:
        row.append(InlineKeyboardButton(label, callback_data=f"mchnls_delay_set:{chat_id}:{val}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(style.btn("Back"), callback_data=f"mchnls_chat:{chat_id}")])

    await ui.edit(
        q.message,
        f"{style.h('Select Auto-Approval Delay')}\n\n"
        "Configure how long the bot waits before approving a join request.\n"
        "<i>A short delay mimics human admin timing and reduces bot-detection noise.</i>",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    await q.answer()


@Client.on_callback_query(filters.regex(r"^mchnls_delay_set:(-?\d+):(\d+)$"))
async def cb_mchnls_delay_set(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    delay = int(q.matches[0].group(2))
    uid = q.from_user.id
    if not await can_manage_chat(uid, chat_id, client):
        await q.answer("❌ Permission denied.", show_alert=True)
        return

    await db.update_chat_key(chat_id, "delay", delay)
    invalidate_chat(chat_id)
    await q.answer(f"Delay set to {delay} seconds!", show_alert=True)
    await cb_mchnls_chat(client, q, chat_id=chat_id)


# ─── Remove Chat ─────────────────────────────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^mchnls_del_confirm:(-?\d+)$"))
async def cb_mchnls_del_confirm(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    if not await can_manage_chat(q.from_user.id, chat_id, client):
        await q.answer("❌ Access Denied.", show_alert=True)
        return
    await ui.edit(
        q.message,
        f"{style.h('Remove This Chat?')}\n\n"
        f"<code>{chat_id}</code> will stop being managed — auto-approval, "
        f"welcome messages, and captcha stop for it. This can't be undone "
        f"(the bot will just re-add it fresh if a new join request arrives).",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes, Remove", callback_data=f"mchnls_del_go:{chat_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"mchnls_chat:{chat_id}"),
            ]
        ]),
    )
    await q.answer()


@Client.on_callback_query(filters.regex(r"^mchnls_del_go:(-?\d+)$"))
async def cb_mchnls_del_go(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    if not await can_manage_chat(q.from_user.id, chat_id, client):
        await q.answer("❌ Access Denied.", show_alert=True)
        return
    await db.delete_chat(chat_id)
    invalidate_chat(chat_id)
    await q.answer("Chat removed from bot management.", show_alert=True)
    await _render_channel_list(client, q, q.from_user.id, page=1)


# ─── Setting Toggles ────────────────────────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^mchnls_tgl:(aa|cap|pfp|cas):(-?\d+)$"))
async def cb_mchnls_tgl(client: Client, q: CallbackQuery):
    key = q.matches[0].group(1)
    chat_id = int(q.matches[0].group(2))
    uid = q.from_user.id

    if not await can_manage_chat(uid, chat_id, client):
        await q.answer("❌ Permission denied.", show_alert=True)
        return

    cfg = await get_cached_chat(chat_id, db.get_chat) or {"chat_id": chat_id}

    if key == "aa":
        new_val = not cfg.get("auto_approve", True)
        cfg["auto_approve"] = new_val
        await db.update_chat_key(chat_id, "auto_approve", new_val)
    elif key == "cap":
        new_val = not cfg.get("captcha", False)
        cfg["captcha"] = new_val
        await db.update_chat_key(chat_id, "captcha", new_val)
    elif key in ("pfp", "cas"):
        allowed = config.PUBLIC_MODE or await is_feature_allowed(
            chat_id, "require_pfp" if key == "pfp" else "cas_check", uid
        )
        if not allowed:
            await q.answer("⭐ Premium feature locked. Upgrade plan to enable.", show_alert=True)
            return
        filters_d = cfg.setdefault("filters", {})
        target_key = "require_pfp" if key == "pfp" else "cas_check"
        new_val = not filters_d.get(target_key, True if key == "cas" else False)
        filters_d[target_key] = new_val
        await db.update_chat_key(chat_id, "filters", filters_d)

    invalidate_chat(chat_id)
    await q.answer(f"Setting updated!")
    await cb_mchnls_chat(client, q, chat_id=chat_id)


# ─── Welcome Message Editor ─────────────────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^mchnls_wel:(-?\d+)$"))
async def cb_mchnls_wel(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    uid = q.from_user.id

    if not await can_manage_chat(uid, chat_id, client):
        await q.answer("❌ Permission denied.", show_alert=True)
        return

    cfg = await get_cached_chat(chat_id, db.get_chat) or {"chat_id": chat_id}
    wcfg = cfg.get("welcome", {})
    title = cfg.get("title", str(chat_id))

    DEFAULT_WELCOME = "<b>Welcome {mention}</b>\n\nYou have been approved to join <b>{chat_title}</b>."
    cur_text = wcfg.get("text", DEFAULT_WELCOME)
    en = style.on(wcfg.get("enabled", True))
    pm = style.btn("Direct Message") if wcfg.get("send_pm", True) else style.btn("In Chat")
    img_count = len(wcfg.get("welcome_images") or [])
    has_other_media = bool(wcfg.get("media_id"))
    if img_count:
        media_label = f"{style.btn('Media')}  ·  {img_count} photo{'s' if img_count != 1 else ''} (random)"
    elif has_other_media:
        media_label = f"{style.btn('Media')}  ·  1 file"
    else:
        media_label = style.btn("Attach Media")

    rows = [
        [InlineKeyboardButton(f"{style.btn('Status')}  ·  {en}", callback_data=f"mchnls_tgl_wel:{chat_id}")],
        [InlineKeyboardButton(f"{style.btn('Target')}  ·  {pm}", callback_data=f"mchnls_tgl_wel_pm:{chat_id}")],
        [InlineKeyboardButton(style.btn("Edit Welcome Text"), callback_data=f"mchnls_edit_txt:welcome:{chat_id}")],
        [InlineKeyboardButton(media_label, callback_data=f"mchnls_edit_med:welcome:{chat_id}")],
        [InlineKeyboardButton("👁️ Live Preview Message", callback_data=f"mchnls_prev:wel:{chat_id}")],
        [InlineKeyboardButton(style.btn("Back to Control Center"), callback_data=f"mchnls_chat:{chat_id}")],
    ]

    text = (
        f"{style.h('Welcome Message Editor')} — <b>{fmt.escape(title)}</b>\n\n"
        f"{style.l('Current Template')}\n"
        f"<code>{fmt.escape(cur_text)}</code>\n\n"
        f"{style.l('Dynamic Variables')}\n"
        f"• <code>{{mention}}</code>, <code>{{first_name}}</code>, <code>{{full_name}}</code>\n"
        f"• <code>{{username}}</code>, <code>{{user_id}}</code>, <code>{{chat_title}}</code>\n"
        f"• <code>{{date}}</code>, <code>{{time}}</code>, <code>{{invite_link}}</code>\n\n"
        f"<i>Format inline buttons as: <code>[Button Text | https://example.com]</code></i>"
    )

    await ui.edit(q.message, text, reply_markup=InlineKeyboardMarkup(rows))
    await q.answer()


@Client.on_callback_query(filters.regex(r"^mchnls_tgl_wel:(-?\d+)$"))
async def cb_tgl_wel(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    if not await can_manage_chat(q.from_user.id, chat_id, client):
        return
    cfg = await get_cached_chat(chat_id, db.get_chat) or {"chat_id": chat_id}
    wcfg = cfg.setdefault("welcome", {})
    wcfg["enabled"] = not wcfg.get("enabled", True)
    await db.update_chat_key(chat_id, "welcome", wcfg)
    invalidate_chat(chat_id)
    await cb_mchnls_wel(client, q)


@Client.on_callback_query(filters.regex(r"^mchnls_tgl_wel_pm:(-?\d+)$"))
async def cb_tgl_wel_pm(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    if not await can_manage_chat(q.from_user.id, chat_id, client):
        return
    cfg = await get_cached_chat(chat_id, db.get_chat) or {"chat_id": chat_id}
    wcfg = cfg.setdefault("welcome", {})
    wcfg["send_pm"] = not wcfg.get("send_pm", True)
    await db.update_chat_key(chat_id, "welcome", wcfg)
    invalidate_chat(chat_id)
    await cb_mchnls_wel(client, q)


# ─── Goodbye Message Editor (NEW) ───────────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^mchnls_gb:(-?\d+)$"))
async def cb_mchnls_gb(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    uid = q.from_user.id

    if not await can_manage_chat(uid, chat_id, client):
        await q.answer("❌ Permission denied.", show_alert=True)
        return

    # Check premium gate for Goodbye feature (skipped while PUBLIC_MODE is on)
    if not (config.PUBLIC_MODE or await is_feature_allowed(chat_id, "goodbye", uid)):
        await q.answer("⭐ Goodbye messages require a PRO or ENTERPRISE plan.", show_alert=True)
        return

    cfg = await get_cached_chat(chat_id, db.get_chat) or {"chat_id": chat_id}
    gcfg = cfg.get("goodbye", {})
    title = cfg.get("title", str(chat_id))

    DEFAULT_GOODBYE = "<b>Goodbye {first_name} 👋</b>\n\nThank you for being part of <b>{chat_title}</b>!"
    cur_text = gcfg.get("text", DEFAULT_GOODBYE)
    en = style.on(gcfg.get("enabled", False))
    pm = style.btn("Direct Message") if gcfg.get("send_pm", True) else style.btn("In Chat")
    has_media = bool(gcfg.get("media_id"))

    rows = [
        [InlineKeyboardButton(f"{style.btn('Status')}  ·  {en}", callback_data=f"mchnls_tgl_gb:{chat_id}")],
        [InlineKeyboardButton(f"{style.btn('Target')}  ·  {pm}", callback_data=f"mchnls_tgl_gb_pm:{chat_id}")],
        [InlineKeyboardButton(style.btn("Edit Goodbye Text"), callback_data=f"mchnls_edit_txt:goodbye:{chat_id}")],
        [InlineKeyboardButton(style.btn("Change Media") if has_media else style.btn("Attach Media"), callback_data=f"mchnls_edit_med:goodbye:{chat_id}")],
        [InlineKeyboardButton("👁️ Live Preview Goodbye", callback_data=f"mchnls_prev:gb:{chat_id}")],
        [InlineKeyboardButton(style.btn("Back to Control Center"), callback_data=f"mchnls_chat:{chat_id}")],
    ]

    text = (
        f"{style.h('Goodbye Message Editor')} — <b>{fmt.escape(title)}</b>\n\n"
        f"{style.l('Current Goodbye Template')}\n"
        f"<code>{fmt.escape(cur_text)}</code>\n\n"
        f"{style.l('Dynamic Variables')}\n"
        f"• <code>{{mention}}</code>, <code>{{first_name}}</code>, <code>{{full_name}}</code>\n"
        f"• <code>{{username}}</code>, <code>{{user_id}}</code>, <code>{{chat_title}}</code>\n"
        f"• <code>{{date}}</code>, <code>{{time}}</code>\n\n"
        f"<i>Fired automatically when a member leaves or is removed from the group.</i>"
    )

    await ui.edit(q.message, text, reply_markup=InlineKeyboardMarkup(rows))
    await q.answer()


@Client.on_callback_query(filters.regex(r"^mchnls_tgl_gb:(-?\d+)$"))
async def cb_tgl_gb(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    if not await can_manage_chat(q.from_user.id, chat_id, client):
        return
    cfg = await get_cached_chat(chat_id, db.get_chat) or {"chat_id": chat_id}
    gcfg = cfg.setdefault("goodbye", {})
    gcfg["enabled"] = not gcfg.get("enabled", False)
    await db.update_chat_key(chat_id, "goodbye", gcfg)
    invalidate_chat(chat_id)
    await cb_mchnls_gb(client, q)


@Client.on_callback_query(filters.regex(r"^mchnls_tgl_gb_pm:(-?\d+)$"))
async def cb_tgl_gb_pm(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    if not await can_manage_chat(q.from_user.id, chat_id, client):
        return
    cfg = await get_cached_chat(chat_id, db.get_chat) or {"chat_id": chat_id}
    gcfg = cfg.setdefault("goodbye", {})
    gcfg["send_pm"] = not gcfg.get("send_pm", True)
    await db.update_chat_key(chat_id, "goodbye", gcfg)
    invalidate_chat(chat_id)
    await cb_mchnls_gb(client, q)


# ─── Upgrade Plan Prompt ───────────────────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^mchnls_upg:(-?\d+):(.+)$"))
async def cb_mchnls_upgrade(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    feature = q.matches[0].group(2)

    text = (
        f"{style.h('⭐ Premium Feature Locked')}\n\n"
        f"The <b>{feature.replace('_', ' ').title()}</b> feature is locked for this channel under the current plan.\n\n"
        f"Upgrade your channel to <b>PRO</b> or <b>ENTERPRISE</b> tier to unlock goodbye messages, avatar filters, and priority processing!"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ Contact Owner to Upgrade", url=config.owner_contact_url())],
        [InlineKeyboardButton("🔙 Back to Settings", callback_data=f"mchnls_chat:{chat_id}")],
    ])
    await ui.edit(q.message, text, reply_markup=markup)
    await q.answer()


# ─── Live Preview Engine ───────────────────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^mchnls_prev:(wel|gb):(-?\d+)$"))
async def cb_mchnls_preview(client: Client, q: CallbackQuery):
    kind = q.matches[0].group(1)
    chat_id = int(q.matches[0].group(2))
    uid = q.from_user.id

    if not await can_manage_chat(uid, chat_id, client):
        await q.answer("❌ Permission denied.", show_alert=True)
        return

    cfg = await get_cached_chat(chat_id, db.get_chat) or {"chat_id": chat_id}

    if kind == "wel":
        mcfg = cfg.get("welcome", {})
        default_txt = "<b>Welcome {mention}</b> to <b>{chat_title}</b>!"
        label = "Welcome"
    else:
        mcfg = cfg.get("goodbye", {})
        default_txt = "<b>Goodbye {first_name} 👋</b> from <b>{chat_title}</b>!"
        label = "Goodbye"

    raw_template = mcfg.get("text", default_txt)
    rendered = fmt.render(
        raw_template,
        user=q.from_user,
        chat=q.message.chat,
        invite_link="https://t.me/+PreviewInviteLink",
    )
    clean_text, markup = fmt.parse_buttons(rendered)

    media_id = mcfg.get("media_id")
    media_type = mcfg.get("media_type")
    if kind == "wel":
        images = mcfg.get("welcome_images") or []
        if images:
            import random
            media_id = random.choice(images)
            media_type = "photo"

    await q.answer(f"Sending live {label.lower()} preview to your DM...")
    try:
        if media_id:
            if media_type == "photo":
                await client.send_photo(uid, photo=media_id, caption=f"👁️ <b>Live Preview ({label})</b>\n\n{clean_text}", reply_markup=markup)
            elif media_type == "video":
                await client.send_video(uid, video=media_id, caption=f"👁️ <b>Live Preview ({label})</b>\n\n{clean_text}", reply_markup=markup)
            elif media_type == "animation":
                await client.send_animation(uid, animation=media_id, caption=f"👁️ <b>Live Preview ({label})</b>\n\n{clean_text}", reply_markup=markup)
            else:
                await client.send_document(uid, document=media_id, caption=f"👁️ <b>Live Preview ({label})</b>\n\n{clean_text}", reply_markup=markup)
        else:
            await client.send_message(uid, text=f"👁️ <b>Live Preview ({label})</b>\n\n{clean_text}", reply_markup=markup)
    except Exception as e:
        await q.message.reply_text(f"{style.h('Preview Error')} <code>{e}</code>")


# ─── Text & Media Editing Flow ──────────────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^mchnls_edit_txt:(welcome|goodbye):(-?\d+)$"))
async def cb_mchnls_edit_txt(client: Client, q: CallbackQuery):
    kind = q.matches[0].group(1)
    chat_id = int(q.matches[0].group(2))
    uid = q.from_user.id

    if not await can_manage_chat(uid, chat_id, client):
        await q.answer("❌ Permission denied.", show_alert=True)
        return

    _editor_states[uid] = {"action": "text", "kind": kind, "chat_id": chat_id}

    target_cb = f"mchnls_wel:{chat_id}" if kind == "welcome" else f"mchnls_gb:{chat_id}"
    await ui.edit(
        q.message,
        f"{style.h(f'Send your new {kind} message text')}\n\n"
        "You can use HTML tags (<code>&lt;b&gt;</code>, <code>&lt;i&gt;</code>) and variables:\n"
        "<code>{mention}</code>, <code>{first_name}</code>, <code>{chat_title}</code>, <code>{date}</code>\n\n"
        "To attach buttons at the bottom, format them as:\n"
        "<code>[Button Text | https://example.com]</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=target_cb)]]),
    )
    await q.answer()


@Client.on_callback_query(filters.regex(r"^mchnls_edit_med:(welcome|goodbye):(-?\d+)$"))
async def cb_mchnls_edit_med(client: Client, q: CallbackQuery):
    kind = q.matches[0].group(1)
    chat_id = int(q.matches[0].group(2))
    uid = q.from_user.id

    if not await can_manage_chat(uid, chat_id, client):
        await q.answer("❌ Permission denied.", show_alert=True)
        return

    _editor_states[uid] = {"action": "media", "kind": kind, "chat_id": chat_id}

    target_cb = f"mchnls_wel:{chat_id}" if kind == "welcome" else f"mchnls_gb:{chat_id}"
    if kind == "welcome":
        cfg = await get_cached_chat(chat_id, db.get_chat) or {}
        n = len(cfg.get("welcome", {}).get("welcome_images") or [])
        body = (
            f"Send <b>Photos</b> — send as many as you like, one at a time; one is "
            f"picked at random each time someone is welcomed"
            f"{f' (<b>{n}</b> already attached)' if n else ''}.\n\n"
            "A <b>Video, GIF, or Document</b> can also be attached (single file, "
            "replaces any previous one of that kind).\n\n"
            "<i>Tap Done when finished, or type <code>remove</code> to clear everything.</i>"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Done", callback_data=target_cb)],
            [InlineKeyboardButton("❌ Cancel", callback_data=target_cb)],
        ])
    else:
        body = (
            "Send any Photo, Video, GIF/Animation, or Document now.\n\n"
            "<i>To remove existing media, type <code>remove</code>.</i>"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=target_cb)]])

    await ui.edit(q.message, f"{style.h(f'Attach Media to {kind.title()} Message')}\n\n{body}", reply_markup=markup)
    await q.answer()


_media_locks: dict = {}  # serializes welcome_images read-modify-write per user


# ─── Private Message Listener for Text/Media Input ─────────────────────────
@Client.on_message(
    filters.private
    & ~filters.command(config.ALL_COMMANDS)
)
async def mchnls_input_handler(client: Client, msg: Message):
    uid = msg.from_user.id
    state = _editor_states.get(uid)
    if not state:
        # Not our flow — let it fall through to the next plugin's catch-all
        # (schedule.py / session.py) instead of eating it.
        raise ContinuePropagation

    action = state["action"]
    kind = state["kind"]
    chat_id = state["chat_id"]

    cfg = await get_cached_chat(chat_id, db.get_chat) or {"chat_id": chat_id}
    mcfg = cfg.setdefault(kind, {})

    if action == "text":
        new_text = msg.text or msg.caption or ""
        mcfg["text"] = new_text
        await db.update_chat_key(chat_id, kind, mcfg)
        invalidate_chat(chat_id)
        del _editor_states[uid]

        back_cb = f"mchnls_wel:{chat_id}" if kind == "welcome" else f"mchnls_gb:{chat_id}"
        await msg.reply_text(
            f"{style.h(f'{kind.title()} message text updated!')}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Editor", callback_data=back_cb)]]),
        )

    elif action == "media":
        back_cb = f"mchnls_wel:{chat_id}" if kind == "welcome" else f"mchnls_gb:{chat_id}"

        if msg.text and msg.text.strip().lower() == "remove":
            mcfg["media_id"] = None
            mcfg["media_type"] = None
            if kind == "welcome":
                mcfg["welcome_images"] = []
            await db.update_chat_key(chat_id, kind, mcfg)
            invalidate_chat(chat_id)
            del _editor_states[uid]
            await msg.reply_text(
                f"{style.h(f'Media removed from {kind} message.')}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Editor", callback_data=back_cb)]]),
            )
            return

        # Welcome photos: multi-image rotation, stays in state for more.
        # Locked to prevent lost updates when several photos arrive close
        # together (e.g. sent as an album) — without this, two near-
        # simultaneous saves can race and the second silently overwrites
        # the first's addition, which looked like "only one ever saves".
        if kind == "welcome" and msg.photo:
            lock = _media_locks.setdefault(uid, asyncio.Lock())
            async with lock:
                fresh_cfg = await get_cached_chat(chat_id, db.get_chat) or {"chat_id": chat_id}
                fresh_mcfg = fresh_cfg.setdefault("welcome", {})
                fresh_mcfg.setdefault("welcome_images", []).append(msg.photo.file_id)
                await db.update_chat_key(chat_id, "welcome", fresh_mcfg)
                invalidate_chat(chat_id)
                n = len(fresh_mcfg["welcome_images"])
            await msg.reply_text(
                f"✅ Added — <b>{n}</b> photo{'s' if n != 1 else ''} now in rotation. Send another, or tap Done.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done", callback_data=back_cb)]]),
            )
            # Stay in this state so the user can keep sending more photos.
            return

        media_id = None
        media_type = None
        if kind == "goodbye" and msg.photo:
            media_id = msg.photo.file_id
            media_type = "photo"
        elif msg.video:
            media_id = msg.video.file_id
            media_type = "video"
        elif msg.animation:
            media_id = msg.animation.file_id
            media_type = "animation"
        elif msg.document:
            media_id = msg.document.file_id
            media_type = "document"

        if media_id:
            mcfg["media_id"] = media_id
            mcfg["media_type"] = media_type
            if msg.caption:
                mcfg["text"] = msg.caption
            await db.update_chat_key(chat_id, kind, mcfg)
            invalidate_chat(chat_id)
            del _editor_states[uid]
            await msg.reply_text(
                f"{style.h(f'Media attached to {kind} message!')}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Editor", callback_data=back_cb)]]),
            )
        else:
            await msg.reply_text("⚠️ Please send a valid Photo, Video, GIF, or Document (or type <code>remove</code>, or tap Done).")