"""
plugins/managechnls.py — Unified Channel Control Center.

Wraps admin toggles, welcome media/button editor, goodbye message builder,
live previews, premium feature gates, and cached channel listings.
"""

from typing import Dict, Optional
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from database import db
from core.cache import get_cached_chat, set_cached_chat, invalidate_chat, get_cached_admin_chats
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
    await _render_channel_list(client, msg, uid, page=1)


async def _render_channel_list(client: Client, target, user_id: int, page: int = 1):
    """Render cached list of channels managed by user_id."""
    all_chats = await get_cached_admin_chats(user_id, db.all_chats)
    
    # Filter chats the user is authorized to manage
    managed_chats = []
    for c in all_chats:
        if await can_manage_chat(user_id, c["chat_id"], client):
            managed_chats.append(c)

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
        if isinstance(target, CallbackQuery):
            await target.message.edit_text(text, reply_markup=markup)
        else:
            await target.reply_text(text, reply_markup=markup)
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

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup)
    else:
        await target.reply_text(text, reply_markup=markup)


# ─── Per-Channel Control Center Menu ────────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^mchnls_chat:(-?\d+)$"))
async def cb_mchnls_chat(client: Client, q: CallbackQuery):
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

    # Feature gate checks via core/quota.py
    has_gb = await is_feature_allowed(chat_id, "goodbye", uid)
    has_pfp = await is_feature_allowed(chat_id, "require_pfp", uid)
    has_cas = await is_feature_allowed(chat_id, "cas_check", uid)

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
        if not await is_feature_allowed(chat_id, "require_pfp" if key == "pfp" else "cas_check", uid):
            await q.answer("⭐ Premium feature locked. Upgrade plan to enable.", show_alert=True)
            return
        filters_d = cfg.setdefault("filters", {})
        target_key = "require_pfp" if key == "pfp" else "cas_check"
        new_val = not filters_d.get(target_key, True if key == "cas" else False)
        filters_d[target_key] = new_val
        await db.update_chat_key(chat_id, "filters", filters_d)

    invalidate_chat(chat_id)
    await q.answer(f"Setting updated!")
    await cb_mchnls_chat(client, q)


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
    has_media = bool(wcfg.get("media_id"))

    rows = [
        [InlineKeyboardButton(f"{style.btn('Status')}  ·  {en}", callback_data=f"mchnls_tgl_wel:{chat_id}")],
        [InlineKeyboardButton(f"{style.btn('Target')}  ·  {pm}", callback_data=f"mchnls_tgl_wel_pm:{chat_id}")],
        [InlineKeyboardButton(style.btn("Edit Welcome Text"), callback_data=f"mchnls_edit_txt:welcome:{chat_id}")],
        [InlineKeyboardButton(style.btn("Change Media") if has_media else style.btn("Attach Media"), callback_data=f"mchnls_edit_med:welcome:{chat_id}")],
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
        f"<i>Format inline buttons as: <code>[Button Text \| https://example.com]</code></i>"
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

    # Check premium gate for Goodbye feature
    if not await is_feature_allowed(chat_id, "goodbye", uid):
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
        [InlineKeyboardButton("⭐ Contact Owner to Upgrade", url="https://t.me/telegram")],
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
    await ui.edit(
        q.message,
        f"{style.h(f'Attach Media to {kind.title()} Message')}\n\n"
        "Send any Photo, Video, GIF/Animation, or Document now.\n\n"
        "<i>To remove existing media, type <code>remove</code>.</i>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=target_cb)]]),
    )
    await q.answer()


# ─── Private Message Listener for Text/Media Input ─────────────────────────
@Client.on_message(
    filters.private
    & ~filters.command([
        "start", "help", "admin", "settings", "ping", "stats", "broadcast",
        "channels", "managechnls", "managechannels", "login", "logout",
        "sessions", "session", "approveall", "queue", "schedule", "schedules"
    ])
)
async def mchnls_input_handler(client: Client, msg: Message):
    uid = msg.from_user.id
    state = _editor_states.get(uid)
    if not state:
        return

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
        if msg.text and msg.text.strip().lower() == "remove":
            mcfg["media_id"] = None
            mcfg["media_type"] = None
            await db.update_chat_key(chat_id, kind, mcfg)
            invalidate_chat(chat_id)
            del _editor_states[uid]

            back_cb = f"mchnls_wel:{chat_id}" if kind == "welcome" else f"mchnls_gb:{chat_id}"
            await msg.reply_text(
                f"{style.h(f'Media removed from {kind} message.')}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Editor", callback_data=back_cb)]]),
            )
            return

        media_id = None
        media_type = None
        if msg.photo:
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

            back_cb = f"mchnls_wel:{chat_id}" if kind == "welcome" else f"mchnls_gb:{chat_id}"
            await msg.reply_text(
                f"{style.h(f'Media attached to {kind} message!')}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Editor", callback_data=back_cb)]]),
            )
        else:
            await msg.reply_text("⚠️ Please send a valid Photo, Video, GIF, or Document (or type <code>remove</code>).")

