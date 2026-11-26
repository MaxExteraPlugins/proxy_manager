import base64
import json
import os
import re
import threading
import time
import urllib.parse
import zlib

from java import cast, dynamic_proxy, jclass, jint, jlong
from java.lang import Long, Runnable, String
from java.util import ArrayList, Locale

from org.telegram.messenger import (
    AndroidUtilities,
    ApplicationLoader,
    LocaleController,
    MessagesController,
    NotificationCenter,
    UserConfig,
)
from org.telegram.messenger.browser import Browser
from org.telegram.tgnet import ConnectionsManager, RequestDelegate, TLRPC
from org.telegram.ui import LaunchActivity

from android.graphics import Color
from android_utils import log, run_on_ui_thread, OnClickListener
from base_plugin import BasePlugin, MethodHook, MenuItemData, MenuItemType
from client_utils import get_last_fragment, send_document, send_request, RequestCallback
from ui.alert import AlertDialogBuilder
from ui.bulletin import BulletinHelper
from ui.settings import Divider, Header, Input, Selector, Switch, Text, Custom

__id__ = "proxy_manager"
__name__ = "Proxy Manager"
__description__ = "Поиск и проверка рабочих MTProto, WebProxy и SOCKS5 прокси из каналов Telegram."
__author__ = "@itNotMax - @MaxExteraPlugins"
__version__ = "1.0.38"
__icon__ = "MaxExteraPluginsStickers/35"
__min_version__ = "12.10.6"

ENTRY_CLASS = "com.exteraplugins.proxy_manager.Main"
DEX_BEGIN = "__DEX_BEGIN__"
DEX_END = "__DEX_END__"


def _get_context_and_fragment(view=None):
    act = None
    frag = None
    if view is not None:
        try:
            ctx = view.getContext()
            while ctx is not None:
                if hasattr(ctx, "getParentActivity") or hasattr(ctx, "presentFragment"):
                    break
                if hasattr(ctx, "getBaseContext"):
                    ctx = ctx.getBaseContext()
                else:
                    break
            act = ctx
        except Exception:
            pass
    if act is None:
        try:
            from org.telegram.ui import LaunchActivity
            act = LaunchActivity.instance
        except Exception:
            pass
    if act is None:
        try:
            f = get_last_fragment()
            if f is not None:
                frag = f
                act = f.getParentActivity()
        except Exception:
            pass
    if frag is None and act is not None:
        try:
            if hasattr(act, "getActionBarLayout") and act.getActionBarLayout():
                frag = act.getActionBarLayout().getLastFragment()
        except Exception:
            pass
    return act, frag


def _get_act():
    try:
        from org.telegram.ui import LaunchActivity
        if LaunchActivity.instance is not None:
            return LaunchActivity.instance
    except Exception:
        pass
    try:
        from client_utils import get_last_fragment
        f = get_last_fragment()
        if f is not None:
            act = f.getParentActivity()
            if act is not None:
                return act
    except Exception:
        pass
    return None


def _set_click_listener(view, callback):
    if view is None or callback is None:
        return
    try:
        from android_utils import OnClickListener
        view.setOnClickListener(OnClickListener(lambda v: callback()))
    except Exception:
        try:
            from android.view import View
            class _Clicker(dynamic_proxy(View.OnClickListener)):
                def onClick(self, v):
                    callback()
            view.setOnClickListener(_Clicker())
        except Exception:
            pass


def _make_runnable(callback):
    if callback is None:
        return None
    try:
        class _Run(dynamic_proxy(Runnable)):
            def run(self):
                try:
                    callback()
                except Exception:
                    pass
        return _Run()
    except Exception:
        return None


def _safe_json_loads(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        cleaned = re.sub(r",\s*([\]\}])", r"\1", str(text))
        return json.loads(cleaned)
    except Exception:
        pass
    return None


def _fetch_url_text(url):
    if dex.loaded:
        try:
            res = dex.call("httpGet", url)
            if res:
                return str(res)
        except Exception:
            pass
    try:
        import urllib.request, ssl
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Android; Telegram)"})
        with urllib.request.urlopen(req, context=ctx, timeout=6.0) as resp:
            return resp.read().decode("utf-8", "ignore")
    except Exception:
        pass
    return None


def _get_channel_depth_label(flag, default_depth_days):
    try:
        flag = int(flag)
    except Exception:
        flag = 1
    if flag == 0:
        return L.g("depth_all")
    elif flag == 101:
        return L.g("depth_1")
    elif flag == 103:
        return L.g("depth_3")
    elif flag == 107:
        return L.g("depth_7")
    else:
        return L.g("depth_1") if default_depth_days == 1 else (L.g("depth_3") if default_depth_days == 3 else L.g("depth_7"))


def _build_msg_link(ch_name, topic_id, m_id):
    if not m_id:
        return ""
    clean_chat = str(ch_name).lstrip("@")
    if clean_chat.startswith("-100"):
        clean_id = clean_chat[4:]
        return f"https://t.me/c/{clean_id}/{topic_id}/{m_id}" if topic_id else f"https://t.me/c/{clean_id}/{m_id}"
    elif clean_chat.lstrip("-").isdigit():
        clean_id = clean_chat.lstrip("-")
        return f"https://t.me/c/{clean_id}/{topic_id}/{m_id}" if topic_id else f"https://t.me/c/{clean_id}/{m_id}"
    else:
        return f"https://t.me/{clean_chat}/{topic_id}/{m_id}" if topic_id else f"https://t.me/{clean_chat}/{m_id}"


def _load_sponsor_icon(avatar, icon_str, title, url):
    if not avatar:
        return
    try:
        from org.telegram.ui.Components import AvatarDrawable
        ph = AvatarDrawable()
        ph.setInfo(jlong(abs(hash(str(title))) % 1000000), str(title or "P"), "")
        avatar.setImageDrawable(ph)
    except Exception:
        pass

    if not icon_str:
        return

    icon_str = str(icon_str).strip()
    if icon_str.startswith("http://") or icon_str.startswith("https://"):
        try:
            avatar.setImage(icon_str, None, None)
            return
        except Exception:
            pass

    if "/" in icon_str:
        parts = icon_str.split("/", 1)
        pack_name = parts[0].strip()
        try:
            raw_idx = int(parts[1].strip())
            sticker_idx = max(0, raw_idx)
        except Exception:
            sticker_idx = 0

        def _apply_doc(doc):
            if doc:
                try:
                    from org.telegram.messenger import ImageLocation
                    avatar.setImage(ImageLocation.getForDocument(doc), "140_140", None, doc)
                except Exception:
                    pass

        try:
            from org.telegram.messenger import MediaDataController, UserConfig
            from org.telegram.tgnet import TLRPC
            account = UserConfig.selectedAccount
            mdc = MediaDataController.getInstance(account)
            st_set = mdc.getStickerSetByName(pack_name)
            if st_set and hasattr(st_set, "documents") and st_set.documents and st_set.documents.size() > 0:
                idx = min(sticker_idx, st_set.documents.size() - 1)
                _apply_doc(st_set.documents.get(idx))
                return

            req = TLRPC.TL_messages_getStickerSet()
            req.stickerset = TLRPC.TL_inputStickerSetShortName()
            req.stickerset.short_name = pack_name
            req.hash = 0

            def _on_sticker_resp(resp, err):
                if resp and not err and hasattr(resp, "documents") and resp.documents and resp.documents.size() > 0:
                    idx = min(sticker_idx, resp.documents.size() - 1)
                    target_doc = resp.documents.get(idx)
                    run_on_ui_thread(lambda: _apply_doc(target_doc))

            send_request(req, _on_sticker_resp)
        except Exception:
            pass


def _get_flag_emoji(country_code):
    if not country_code or len(country_code) != 2:
        return ""
    try:
        return "".join(chr(ord(c.upper()) + 127397) for c in country_code)
    except Exception:
        return ""


_COUNTRY_NAMES_RU = {
    "RU": "Россия", "DE": "Германия", "NL": "Нидерланды", "US": "США", "FI": "Финляндия",
    "FR": "Франция", "GB": "Великобритания", "PL": "Польша", "TR": "Турция", "IR": "Иран",
    "KZ": "Казахстан", "UA": "Украина", "BY": "Беларусь", "SE": "Швеция", "CH": "Швейцария",
    "IT": "Италия", "ES": "Испания", "SG": "Сингапур", "JP": "Япония", "KR": "Южная Корея",
    "IN": "Индия", "AE": "ОАЭ", "HK": "Гонконг", "CA": "Канада", "AT": "Австрия",
    "CZ": "Чехия", "NO": "Норвегия", "RO": "Румыния", "MD": "Молдова", "GE": "Грузия",
    "AM": "Армения", "UZ": "Узбекистан", "AZ": "Азербайджан", "KG": "Кыргызстан", "TJ": "Таджикистан",
    "LT": "Литва", "LV": "Латвия", "EE": "Эстония", "BG": "Болгария", "HU": "Венгрия",
    "RS": "Сербия", "GR": "Греция", "IL": "Израиль", "BR": "Бразилия", "AR": "Аргентина",
    "AU": "Австралия", "NZ": "Новая Зеландия", "IE": "Ирландия", "BE": "Бельгия", "DK": "Дания",
    "PT": "Португалия", "SK": "Словакия", "SI": "Словения", "HR": "Хорватия", "IS": "Исландия",
    "CY": "Кипр", "LU": "Люксембург", "ZA": "ЮАР", "MX": "Мексика", "CL": "Чили",
}

_GEO_CACHE = {}

def _get_country_name_ru(code):
    if not code:
        return ""
    c = code.strip().upper()
    return _COUNTRY_NAMES_RU.get(c, c)

def _resolve_single_host(host):
    if not host:
        return ("Unknown", "", "")
    h = host.strip().lower()
    if h in _GEO_CACHE:
        return _GEO_CACHE[h]

    c_name = ""
    c_code = ""

    # 1. Try DEX pyResolveCountry (uses Java ConcurrentHashMap + api.country.is + freeipapi)
    if dex.loaded:
        try:
            res = dex.call("pyResolveCountry", h)
            if res:
                geo = json.loads(str(res))
                c_name = geo.get("country", "")
                c_code = geo.get("country_code", "")
        except Exception:
            pass

    # 2. Python fallback: query api.country.is
    if not c_name or c_name == "Unknown" or not c_code:
        try:
            import socket, urllib.request
            ip = socket.gethostbyname(h)
            req = urllib.request.Request(
                f"https://api.country.is/{ip}",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                geo = json.loads(resp.read().decode("utf-8"))
                code = geo.get("country", "").strip().upper()
                if code and len(code) == 2 and code != "ZZ":
                    c_code = code
                    c_name = _get_country_name_ru(code)
        except Exception:
            pass

    # 3. Python fallback: query freeipapi
    if not c_name or c_name == "Unknown" or not c_code:
        try:
            import socket, urllib.request
            ip = socket.gethostbyname(h)
            req = urllib.request.Request(
                f"https://freeipapi.com/api/json/{ip}",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                geo = json.loads(resp.read().decode("utf-8"))
                code = geo.get("countryCode", "").strip().upper()
                if code and len(code) == 2:
                    c_code = code
                    c_name = _get_country_name_ru(code) or geo.get("countryName", code)
        except Exception:
            pass

    if not c_name or c_name == "Unknown":
        if c_code:
            c_name = _get_country_name_ru(c_code)
        else:
            c_name = "Unknown"
            c_code = ""

    flag = _get_flag_emoji(c_code) if c_code else ""
    result = (c_name, c_code, flag)
    _GEO_CACHE[h] = result
    return result

def _enrich_proxies_with_country(proxies):
    if not proxies:
        return
    import concurrent.futures
    hosts_to_resolve = []
    for p in proxies:
        h = (p.get("server") or "").strip().lower()
        if h and (not p.get("country") or p.get("country") == "Unknown"):
            hosts_to_resolve.append((p, h))

    if not hosts_to_resolve:
        for p in proxies:
            c_code = p.get("country_code", "")
            if c_code and not p.get("flag"):
                p["flag"] = _get_flag_emoji(c_code)
        return

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_resolve_single_host, h): p for p, h in hosts_to_resolve}
        for fut in concurrent.futures.as_completed(futures):
            p = futures[fut]
            try:
                name, code, flag = fut.result()
                p["country"] = name
                p["country_code"] = code
                p["flag"] = flag
            except Exception:
                p["country"] = "Unknown"
                p["country_code"] = ""
                p["flag"] = ""


def _iter_java_list(java_list):
    if java_list is None:
        return
    try:
        size = java_list.size()
        for i in range(size):
            yield java_list.get(i)
    except Exception:
        try:
            for item in java_list:
                yield item
        except Exception:
            pass


class L:
    @classmethod
    def is_ru(cls):
        try:
            lc = LocaleController.getInstance()
            loc = lc.getCurrentLocale()
            if loc:
                lang = str(loc.getLanguage() or "").lower()
                if lang.startswith(("ru", "uk", "be")):
                    return True
                if lang:
                    return False
        except Exception:
            pass
        try:
            sys_lang = str(Locale.getDefault().getLanguage() or "").lower()
            if sys_lang.startswith(("ru", "uk", "be")):
                return True
            if sys_lang:
                return False
        except Exception:
            pass
        return False

    T = {
        "title": {
            "ru": "Proxy Manager",
            "en": "Proxy Manager",
        },
        "settings_err": {
            "ru": "Ошибка загрузки настроек",
            "en": "Settings load error",
        },
        "header_settings": {
            "ru": "Параметры поиска",
            "en": "Search Settings",
        },
        "channels_input": {
            "ru": "Каналы для сканирования",
            "en": "Channels to Scan",
        },
        "channels_input_sub": {
            "ru": "Список каналов (через запятую или пробел, @канал или t.me/канал)",
            "en": "Comma/space-separated channels (@channel or t.me/channel)",
        },
        "channels_manage": {
            "ru": "Управление каналами",
            "en": "Manage Channels",
        },
        "channels_manage_sub": {
            "ru": "Просмотр, удаление и выбор из своих подписок",
            "en": "View, delete, or pick from joined channels",
        },
        "search_depth": {
            "ru": "Глубина поиска по умолчанию",
            "en": "Default search depth",
        },
        "search_depth_sub": {
            "ru": "Применяется к новым каналам и каналам без индивидуальной глубины",
            "en": "Applies to new channels and channels without custom depth",
        },
        "country_whitelist_title": {
            "ru": "Белый список стран",
            "en": "Country whitelist",
        },
        "country_whitelist_all": {
            "ru": "Все страны (без фильтра)",
            "en": "All countries (no filter)",
        },
        "country_whitelist_dialog_title": {
            "ru": "Белый список стран",
            "en": "Country whitelist",
        },
        "country_whitelist_reset": {
            "ru": "Сбросить (Все страны)",
            "en": "Reset (All countries)",
        },
        "only_tls_title": {
            "ru": "Только TLS / Fake-TLS",
            "en": "Only TLS / Fake-TLS",
        },
        "only_tls_sub": {
            "ru": "Искать только защищенные MTProto на 443 порту",
            "en": "Search only protected MTProto on port 443",
        },
        "triggers_title": {
            "ru": "Условия срабатывания",
            "en": "Trigger conditions",
        },
        "trigger_after_scan": {
            "ru": "После сканирования",
            "en": "After scan",
        },
        "trigger_on_startup": {
            "ru": "При запуске клиента",
            "en": "On client startup",
        },
        "trigger_on_switch": {
            "ru": "При переключении прокси",
            "en": "On proxy switch",
        },
        "trigger_periodic": {
            "ru": "По расписанию",
            "en": "Periodically",
        },
        "notification_mode_title": {
            "ru": "Режим уведомлений",
            "en": "Notification mode",
        },
        "notification_mode_important": {
            "ru": "Только важные (рекомендуется)",
            "en": "Important only (recommended)",
        },
        "notification_mode_all": {
            "ru": "Все уведомления",
            "en": "All notifications",
        },
        "notification_mode_silent": {
            "ru": "Тихий режим",
            "en": "Silent mode",
        },
        "depth_1": {
            "ru": "1 день",
            "en": "1 day",
        },
        "depth_3": {
            "ru": "3 дня",
            "en": "3 days",
        },
        "depth_7": {
            "ru": "7 дней",
            "en": "7 days",
        },
        "auto_add": {
            "ru": "Автодобавление в Telegram",
            "en": "Auto-add to Telegram",
        },
        "auto_add_sub": {
            "ru": "Автоматически добавлять найденные рабочие прокси в клиент",
            "en": "Automatically add found working proxies to client",
        },
        "auto_connect_enable": {
            "ru": "Автоподключение при включении",
            "en": "Auto-connect on enable",
        },
        "auto_connect_enable_sub": {
            "ru": "Подключает рабочий прокси, если текущий не отвечает",
            "en": "Connects a working proxy if current is unreachable",
        },
        "auto_enable_no_conn_title": {
            "ru": "Авто-поиск при отсутствии связи",
            "en": "Auto-search when disconnected",
        },
        "auto_enable_no_conn_sub": {
            "ru": "Если нет связи с Telegram и прокси выключены — включить и найти рабочий",
            "en": "If Telegram has no connection and proxy is OFF — enable and find a working one",
        },
        "auto_enable_no_conn_triggered": {
            "ru": "Нет связи с Telegram — включаем прокси и подключаем рабочий...",
            "en": "No Telegram connection — enabling proxy and connecting...",
        },
        "auto_enable_no_conn_delay_title": {
            "ru": "Задержка перед авто-поиском",
            "en": "Delay before auto-search",
        },
        "auto_enable_no_conn_delay_sub": {
            "ru": "Сколько секунд ждать (например, пока загрузится VPN)",
            "en": "Seconds to wait (e.g. for VPN to connect)",
        },
        "proxy_limit_title": {
            "ru": "Лимит размера списка",
            "en": "Proxy list limit",
        },
        "proxy_limit_sub": {
            "ru": "Ограничивать количество хранимых прокси в Telegram",
            "en": "Limit maximum proxies saved in Telegram",
        },
        "proxy_max_count_title": {
            "ru": "Максимум прокси",
            "en": "Max proxies count",
        },
        "proxy_limit_policy_title": {
            "ru": "Режим замены при превышении",
            "en": "Replacement policy",
        },
        "policy_replace_dead": {
            "ru": "Заменять нерабочие",
            "en": "Replace dead proxies",
        },
        "policy_replace_oldest": {
            "ru": "Заменять старые новыми",
            "en": "Replace oldest with new",
        },
        "auto_switch_strategy_title": {
            "ru": "Стратегия переключения",
            "en": "Switching strategy",
        },
        "strategy_lowest_ping": {
            "ru": "Наименьший пинг",
            "en": "Lowest ping",
        },
        "strategy_random": {
            "ru": "Случайный рабочий",
            "en": "Random working",
        },
        "strategy_sequential": {
            "ru": "По порядку в списке",
            "en": "Sequential in list",
        },
        "proxy_cooldown_title": {
            "ru": "Кулдаун сбойных прокси",
            "en": "Failed proxy cooldown",
        },
        "proxy_cooldown_sub": {
            "ru": "Временно исключать упавшие прокси из выбора",
            "en": "Temporarily exclude failed proxies",
        },
        "proxy_cooldown_duration_title": {
            "ru": "Время кулдауна",
            "en": "Cooldown duration",
        },
        "channel_manage_btn": {
            "ru": "Настройка каналов (глубина / удаление)",
            "en": "Channel settings (depth / delete)",
        },
        "channel_manage_sub": {
            "ru": "Выбрать канал для изменения глубины поиска или удаления",
            "en": "Select channel to change search depth or delete",
        },
        "all_channels_disabled": {
            "ru": "Все каналы отключены в настройках!",
            "en": "All channels are disabled!",
        },
        "custom_number": {
            "ru": "Ввести свое число",
            "en": "Enter custom number",
        },
        "enter_number": {
            "ru": "Введите число:",
            "en": "Enter number:",
        },
        "header_proxy_limit": {
            "ru": "Лимит количества прокси",
            "en": "Proxy list limit",
        },
        "add_options_title": {
            "ru": "Добавление прокси",
            "en": "Add Proxies",
        },
        "add_all_count": {
            "ru": "Добавить все ({0} шт.)",
            "en": "Add all ({0} pcs.)",
        },
        "add_top_ping": {
            "ru": "Лучшие по пингу (с минимальной задержкой)...",
            "en": "Best ping (lowest latency)...",
        },
        "add_by_country": {
            "ru": "Выбрать по странам...",
            "en": "Select by country...",
        },
        "add_by_ping_title": {
            "ru": "Добавить лучшие по пингу",
            "en": "Add best ping proxies",
        },
        "fastest_count": {
            "ru": "{0} самых быстрых",
            "en": "{0} fastest",
        },
        "enter_custom_count": {
            "ru": "Ввести свое число...",
            "en": "Enter custom number...",
        },
        "how_many_add": {
            "ru": "Сколько прокси добавить?",
            "en": "How many proxies to add?",
        },
        "select_countries_title": {
            "ru": "Выбор стран для добавления",
            "en": "Select countries to add",
        },
        "add_selected": {
            "ru": "Добавить выбранные",
            "en": "Add selected",
        },
        "no_countries_selected": {
            "ru": "Страны не выбраны",
            "en": "No countries selected",
        },
        "smart_analysis_title": {
            "ru": "Умный анализ прокси",
            "en": "Smart proxy analysis",
        },
        "smart_analysis_sub": {
            "ru": "Приоритет прокси с надежными портами, протоколами и подсетями",
            "en": "Prioritize proxies with reliable ports, protocols and subnets",
        },
        "smart_stats_title": {
            "ru": "Статистика анализа",
            "en": "Analysis statistics",
        },
        "smart_stats_dialog_title": {
            "ru": "Умный анализ: статистика",
            "en": "Smart analysis: statistics",
        },
        "smart_stats_reset": {
            "ru": "Сбросить статистику",
            "en": "Reset statistics",
        },
        "smart_stats_reset_done": {
            "ru": "Статистика умного анализа сброшена",
            "en": "Smart analysis statistics reset",
        },
        "smart_stats_empty": {
            "ru": "Обучение... Статистика собирается при работе",
            "en": "Learning... Stats collected during usage",
        },
        "header_notifications": {
            "ru": "Уведомления",
            "en": "Notifications",
        },
        "notifications_master": {
            "ru": "Уведомления",
            "en": "Notifications",
        },
        "notifications_master_sub": {
            "ru": "Показывать всплывающие уведомления о работе плагина",
            "en": "Show toast/bulletin notifications",
        },
        "notify_on_switch": {
            "ru": "При переключении прокси",
            "en": "On proxy switch",
        },
        "notify_on_switch_sub": {
            "ru": "Уведомлять при авто-переключении прокси",
            "en": "Notify when proxy is switched automatically",
        },
        "notify_on_clean": {
            "ru": "При очистке прокси",
            "en": "On proxy clean",
        },
        "notify_on_clean_sub": {
            "ru": "Уведомлять после удаления нерабочих прокси",
            "en": "Notify after dead proxies are removed",
        },
        "notify_on_add": {
            "ru": "При добавлении прокси",
            "en": "On proxy add",
        },
        "notify_on_add_sub": {
            "ru": "Уведомлять при добавлении новых прокси",
            "en": "Notify when new proxies are added",
        },
        "auto_switch_master": {
            "ru": "Авто-переключение прокси",
            "en": "Auto-switch proxies",
        },
        "auto_switch_master_sub": {
            "ru": "Автоматически менять прокси при сбое связи или медиа",
            "en": "Automatically switch proxies on connection or media issues",
        },
        "start_scan": {
            "ru": "Начать сканирование",
            "en": "Start Scanning",
        },
        "start_scan_sub": {
            "ru": "Поиск и проверка рабочих прокси в каналах",
            "en": "Find and verify working proxies in channels",
        },
        "open_tg_proxy": {
            "ru": "Настройки прокси Telegram",
            "en": "Telegram Proxy Settings",
        },
        "open_tg_proxy_sub": {
            "ru": "Открыть встроенный список прокси клиента",
            "en": "Open built-in client proxy list",
        },
        "export_title": {
            "ru": "Экспорт прокси Telegram",
            "en": "Export Telegram Proxies",
        },
        "export_sub": {
            "ru": "Экспорт добавленных прокси в файл, буфер или отправка",
            "en": "Export saved proxies to .txt file, clipboard or share",
        },
        "export_dialog_msg": {
            "ru": "В Telegram найдено {0} добавленных прокси.\nВыберите вариант экспорта:",
            "en": "Found {0} proxies in Telegram.\nChoose export option:",
        },
        "export_opt_copy": {
            "ru": "Скопировать ссылки",
            "en": "Copy Links",
        },
        "export_opt_txt": {
            "ru": "Сохранить в файл (.txt)",
            "en": "Save to .txt File",
        },
        "export_opt_saved": {
            "ru": "Отправить в Избранное (.txt)",
            "en": "Send to Saved Messages (.txt)",
        },
        "export_opt_share": {
            "ru": "Поделиться",
            "en": "Share via...",
        },
        "export_copied": {
            "ru": "Скопировано {0} прокси в буфер обмена!",
            "en": "Copied {0} proxies to clipboard!",
        },
        "file_saved_success": {
            "ru": "Файл успешно сохранен в Загрузки: {0}",
            "en": "File saved to Downloads: {0}",
        },
        "saved_msgs_success": {
            "ru": "Файл с прокси отправлен в Избранное!",
            "en": "Proxy file sent to Saved Messages!",
        },
        "export_caption": {
            "ru": "Экспорт прокси ({0} шт.) от Proxy Manager",
            "en": "Proxy Manager export ({0} proxies)",
        },
        "no_tg_proxies": {
            "ru": "В Telegram нет добавленных прокси!",
            "en": "No proxies found in Telegram!",
        },
        "clean_proxies_title": {
            "ru": "Очистка прокси Telegram",
            "en": "Clean Telegram Proxies",
        },
        "clean_proxies_sub": {
            "ru": "Удалить только нерабочие или оставить X с минимальным пингом",
            "en": "Remove dead proxies or keep top X by ping",
        },
        "clean_dialog_title": {
            "ru": "Очистка списка прокси",
            "en": "Clean Proxy List",
        },
        "clean_opt_remove_dead": {
            "ru": "Удалить только нерабочие",
            "en": "Remove dead proxies only",
        },
        "clean_opt_keep_top": {
            "ru": "Оставить X лучших по пингу",
            "en": "Keep top X by ping",
        },
        "clean_opt_delete_all": {
            "ru": "Удалить все прокси",
            "en": "Delete all proxies",
        },
        "clean_dead_btn": {
            "ru": "Очистка нерабочих",
            "en": "Clean dead proxies",
        },
        "clean_dead_btn_sub": {
            "ru": "Проверить и удалить неработающие прокси",
            "en": "Check and remove dead proxies",
        },
        "header_search_params": {
            "ru": "Параметры поиска",
            "en": "Search Parameters",
        },
        "header_start": {
            "ru": "Пуск",
            "en": "Run",
        },
        "header_auto_add": {
            "ru": "Авто-добавление",
            "en": "Auto-Add",
        },
        "header_auto_clean": {
            "ru": "Авто-удаление",
            "en": "Auto-Clean",
        },
        "header_auto_switch": {
            "ru": "Авто-переключение",
            "en": "Auto-Switch",
        },
        "header_proxies": {
            "ru": "Прокси",
            "en": "Proxies",
        },
        "auto_add_master": {
            "ru": "Авто-добавление прокси",
            "en": "Auto-add proxies",
        },
        "auto_add_master_sub": {
            "ru": "Автоматически сохранять рабочие прокси в Telegram",
            "en": "Automatically add found working proxies to Telegram",
        },
        "auto_add_after_scan": {
            "ru": "После сканирования",
            "en": "After scan",
        },
        "auto_add_after_scan_sub": {
            "ru": "Добавлять найденные рабочие прокси после сканирования",
            "en": "Add found working proxies after scanning",
        },
        "auto_add_on_startup": {
            "ru": "При запуске клиента",
            "en": "On client startup",
        },
        "auto_add_on_startup_sub": {
            "ru": "Искать и добавлять рабочие прокси при запуске",
            "en": "Find and add working proxies on client startup",
        },
        "auto_add_on_switch": {
            "ru": "При переключении между прокси",
            "en": "On proxy switch",
        },
        "auto_add_on_switch_sub": {
            "ru": "Искать новые при смене текущего прокси",
            "en": "Find new proxies when current proxy is switched",
        },
        "auto_add_periodic": {
            "ru": "Раз в некоторое время",
            "en": "Periodically",
        },
        "auto_add_interval_title": {
            "ru": "Интервал авто-добавления",
            "en": "Auto-add interval",
        },
        "auto_clean_master": {
            "ru": "Авто-удаление нерабочих",
            "en": "Auto-clean dead",
        },
        "auto_clean_master_sub": {
            "ru": "Автоматически очищать список от неработающих прокси",
            "en": "Automatically remove dead proxies from client",
        },
        "safe_clean_title": {
            "ru": "Безопасное удаление",
            "en": "Safe clean",
        },
        "safe_clean_sub": {
            "ru": "Не удалять недавно добавленные и защищать список при смене сети (Wi-Fi/моб./VPN)",
            "en": "Protect recently added proxies and prevent mass wipe on network switch",
        },
        "auto_clean_after_scan": {
            "ru": "После сканирования",
            "en": "After scan",
        },
        "auto_clean_after_scan_sub": {
            "ru": "Удалять нерабочие прокси после завершения сканирования",
            "en": "Remove dead proxies after scan completes",
        },
        "auto_clean_on_startup": {
            "ru": "При запуске клиента",
            "en": "On client startup",
        },
        "auto_clean_on_startup_sub": {
            "ru": "Удалять нерабочие прокси при запуске клиента",
            "en": "Remove dead proxies on client startup",
        },
        "auto_clean_on_switch": {
            "ru": "При переключении между прокси",
            "en": "On proxy switch",
        },
        "auto_clean_on_switch_sub": {
            "ru": "Удалять нерабочие при смене текущего прокси",
            "en": "Remove dead proxies when switching proxy",
        },
        "auto_clean_periodic": {
            "ru": "Раз в некоторое время",
            "en": "Periodically",
        },
        "auto_clean_interval_title": {
            "ru": "Интервал авто-удаления",
            "en": "Auto-clean interval",
        },
        "auto_clean_aborted_mass": {
            "ru": "Очистка отменена: массовый сбой или смена сети (защита списка)",
            "en": "Clean aborted: mass failure or network switch (list protected)",
        },
        "clean_checking": {
            "ru": "Проверка прокси Telegram...",
            "en": "Checking Telegram proxies...",
        },
        "clean_removed_dead_result": {
            "ru": "Удалено нерабочих прокси: {0}. Оставлено рабочих: {1}.",
            "en": "Removed {0} dead proxies. Working kept: {1}.",
        },
        "clean_kept_top_result": {
            "ru": "Оставлено {0} лучших прокси (удалено {1}).",
            "en": "Kept {0} best proxies (removed {1}).",
        },
        "clean_prompt_keep_count": {
            "ru": "Сколько лучших прокси оставить?",
            "en": "How many top proxies to keep?",
        },
        "clean_confirm_delete_all": {
            "ru": "Вы точно хотите удалить ВСЕ добавленные прокси ({0} шт.)?",
            "en": "Are you sure you want to delete ALL {0} proxies?",
        },
        "clean_all_deleted": {
            "ru": "Все добавленные прокси удалены из Telegram.",
            "en": "All proxies removed from Telegram.",
        },
        "header_results": {
            "ru": "Результаты сканирования",
            "en": "Scan Results",
        },
        "results_title": {
            "ru": "Рабочие прокси ({0})",
            "en": "Working Proxies ({0})",
        },
        "results_sub": {
            "ru": "Нажмите для просмотра, копирования или подключения",
            "en": "Tap to view, copy, or connect",
        },
        "scan_results_ui_title": {
            "ru": "Результаты сканирования",
            "en": "Scan Results",
        },
        "sorted_by_ping": {
            "ru": "Отсортировано по возрастанию пинга",
            "en": "Sorted by ping (lowest first)",
        },
        "source_channel": {
            "ru": "Источник: {0}",
            "en": "Source: {0}",
        },
        "country_label": {
            "ru": "Страна: {0}",
            "en": "Country: {0}",
        },
        "btn_add": {
            "ru": "Добавить",
            "en": "Add",
        },
        "btn_added": {
            "ru": "Добавлено",
            "en": "Added",
        },
        "btn_delete": {
            "ru": "Удалить",
            "en": "Delete",
        },
        "proxy_added_toast": {
            "ru": "Прокси добавлен в Telegram",
            "en": "Proxy added to Telegram",
        },
        "proxy_removed_toast": {
            "ru": "Прокси удален из Telegram",
            "en": "Proxy removed from Telegram",
        },
        "no_cached_results": {
            "ru": "Нет сохраненных результатов сканирования",
            "en": "No saved scan results",
        },
        "added_all_toast": {
            "ru": "Добавлено {0} прокси в Telegram",
            "en": "Added {0} proxies to Telegram",
        },
        "auto_switch_media_title": {
            "ru": "Автопереключение при зависании медиа",
            "en": "Auto-switch on stalled media",
        },
        "auto_switch_media_sub": {
            "ru": "Переключает прокси при зависании скачивания или отправки медиа (фото, видео, файлы)",
            "en": "Switches proxy if media download or upload gets stuck (photos, videos, files)",
        },
        "media_stall_timeout_title": {
            "ru": "Время ожидания при зависании",
            "en": "Stall timeout duration",
        },
        "media_stall_timeout_sub": {
            "ru": "Через сколько секунд зависания медиа переключать прокси",
            "en": "Seconds of stalled media before switching proxy",
        },
        "quick_search_proxy": {
            "ru": "⚡ Быстрый поиск прокси",
            "en": "⚡ Quick Proxy Search",
        },
        "quick_search_in_progress": {
            "ru": "Поиск прокси уже выполняется...",
            "en": "Proxy search already in progress...",
        },
        "quick_search_searching": {
            "ru": "⚡ Поиск лучшего рабочего прокси...",
            "en": "⚡ Searching for best working proxy...",
        },
        "quick_search_success": {
            "ru": "⚡ Подключено: {0} ({1} ms)",
            "en": "⚡ Connected: {0} ({1} ms)",
        },
        "quick_search_none_found": {
            "ru": "Рабочих прокси не найдено",
            "en": "No working proxies found",
        },
        "media_stalled_switched": {
            "ru": "Зависла загрузка или отправка медиа. Переключено на следующий рабочий прокси",
            "en": "Media download or upload stalled. Switched to next working proxy",
        },
        "auto_scan_title": {
            "ru": "Авто-поиск прокси",
            "en": "Auto-search proxies",
        },
        "auto_scan_sub": {
            "ru": "Поиск и добавление рабочих прокси при запуске и переключении",
            "en": "Search and add working proxies on startup and auto-switch",
        },
        "auto_scan_added": {
            "ru": "Авто-поиск: добавлено {0} рабочих прокси в Telegram",
            "en": "Auto-search: added {0} working proxies to Telegram",
        },
        "auto_clean_dead_title": {
            "ru": "Авто-удаление нерабочих",
            "en": "Auto-remove dead proxies",
        },
        "auto_clean_dead_sub": {
            "ru": "Удаление неработающих прокси при запуске и переключении",
            "en": "Remove unreachable proxies on startup and auto-switch",
        },
        "auto_cleaned_dead": {
            "ru": "Авто-очистка: удалено {0} нерабочих (осталось {1})",
            "en": "Auto-clean: removed {0} dead proxies ({1} kept)",
        },
        "clean_aborted": {
            "ru": "Все прокси недоступны (нет сети?). Очистка отменена для защиты списка.",
            "en": "All proxies unreachable. Cleaning aborted to protect list.",
        },
        "clean_proxy_disabled": {
            "ru": "Прокси отключены в Telegram. Включите прокси перед очисткой.",
            "en": "Proxy is disabled in Telegram. Enable proxy before cleaning.",
        },
        "btn_connect": {
            "ru": "Подключить",
            "en": "Connect",
        },
        "btn_copy": {
            "ru": "Копировать",
            "en": "Copy",
        },
        "share_btn": {
            "ru": "Поделиться",
            "en": "Share",
        },
        "scan_in_progress": {
            "ru": "Сканирование уже выполняется...",
            "en": "Scan is already in progress...",
        },
        "scan_progress_title": {
            "ru": "Сканирование каналов...",
            "en": "Scanning channels...",
        },
        "scan_progress_msg": {
            "ru": "Канал {0}/{1}: @{2}\nПроверено сообщений: {3}\nНайдено кандидатов: {4}",
            "en": "Channel {0}/{1}: @{2}\nMessages checked: {3}\nFound candidates: {4}",
        },
        "scan_progress_ping": {
            "ru": "Проверка {0} найденных прокси на пинг и доступность...",
            "en": "Testing {0} found proxies for latency and availability...",
        },
        "scan_cancelled": {
            "ru": "Сканирование отменено пользователем",
            "en": "Scan cancelled by user",
        },
        "no_channels": {
            "ru": "Укажите хотя бы один канал в настройках!",
            "en": "Please specify at least one channel in settings!",
        },
        "no_proxies_found": {
            "ru": "В сообщениях за выбранный период прокси не найдены.",
            "en": "No proxies found in messages for the selected period.",
        },
        "no_alive_proxies": {
            "ru": "Найдено ссылок: {0}, но ни один прокси не ответил.",
            "en": "Found {0} links, but none of the proxies responded.",
        },
        "scan_dialog_title": {
            "ru": "Сканирование завершено",
            "en": "Scan Complete",
        },
        "scan_dialog_msg": {
            "ru": "Каналов: {0}\nНайдено ссылок: {1}\nРабочих прокси: {2}\n\nСамый быстрый: {3} • {4}:{5} ({6} мс)",
            "en": "Channels: {0}\nLinks found: {1}\nWorking proxies: {2}\n\nFastest: {3} • {4}:{5} ({6} ms)",
        },
        "add_all_to_tg": {
            "ru": "Добавить все в Telegram",
            "en": "Add All to Telegram",
        },
        "copy_all_links": {
            "ru": "Скопировать ссылки",
            "en": "Copy Links",
        },
        "close": {
            "ru": "Закрыть",
            "en": "Close",
        },
        "copied_clipboard": {
            "ru": "Ссылки скопированы в буфер обмена!",
            "en": "Links copied to clipboard!",
        },
        "added_success": {
            "ru": "Добавлено {0} прокси в список Telegram!",
            "en": "Added {0} proxies to Telegram list!",
        },
        "one_added_success": {
            "ru": "Прокси успешно добавлен в Telegram!",
            "en": "Proxy added to Telegram!",
        },
        "connect": {
            "ru": "Подключить сейчас",
            "en": "Connect Now",
        },
        "add_one": {
            "ru": "Добавить в список Telegram",
            "en": "Add to Telegram List",
        },
        "copy_one": {
            "ru": "Скопировать ссылку",
            "en": "Copy Link",
        },
        "connected_success": {
            "ru": "Прокси успешно подключен!",
            "en": "Proxy successfully connected!",
        },
        "ping_ms": {
            "ru": "Пинг: {0} мс",
            "en": "Ping: {0} ms",
        },
        "clear_results": {
            "ru": "Очистить список результатов",
            "en": "Clear Results List",
        },
        "results_cleared": {
            "ru": "Результаты сканирования очищены",
            "en": "Scan results cleared",
        },
        "auto_finding_proxy": {
            "ru": "Поиск рабочего прокси...",
            "en": "Searching for working proxy...",
        },
        "auto_connected_saved": {
            "ru": "Автоподключение: подключен прокси {0}:{1} ({2} мс)",
            "en": "Auto-connect: connected to {0}:{1} ({2} ms)",
        },
        "auto_connected_found": {
            "ru": "Автоподключение: найден и подключен {0}:{1} ({2} мс)",
            "en": "Auto-connect: found and connected to {0}:{1} ({2} ms)",
        },
        "auto_connect_failed": {
            "ru": "Не удалось найти рабочий прокси для автоподключения",
            "en": "Could not find a working proxy for auto-connect",
        },
        "pick_channels": {
            "ru": "Выбрать каналы из подписок",
            "en": "Pick Channels from Subscriptions",
        },
        "pick_channels_sub": {
            "ru": "Открыть диалог выбора публичных каналов",
            "en": "Open public channel selector dialog",
        },
        "clear_channels": {
            "ru": "Очистить весь список каналов",
            "en": "Clear All Channels in List",
        },
        "channels_cleared": {
            "ru": "Список каналов очищен",
            "en": "Channels list cleared",
        },
        "channel_count_header": {
            "ru": "Добавленные каналы ({0})",
            "en": "Configured Channels ({0})",
        },
        "no_channels_in_list": {
            "ru": "Список каналов пуст. Добавьте ссылки или выберите из подписок.",
            "en": "No channels in list. Add usernames or pick from subscriptions.",
        },
        "channel_removed": {
            "ru": "Канал @{0} удален из списка",
            "en": "Channel @{0} removed from list",
        },
        "remove_channel_confirm": {
            "ru": "Удалить канал @{0} из списка сканирования?",
            "en": "Remove channel @{0} from scan list?",
        },
        "delete": {
            "ru": "Удалить",
            "en": "Delete",
        },
        "cancel": {
            "ru": "Отмена",
            "en": "Cancel",
        },
        "manage_channels_title": {
            "ru": "Управление каналами",
            "en": "Manage Channels",
        },
        "manage_channels_sub": {
            "ru": "Добавление, удаление каналов и тем форумов",
            "en": "Add, remove channels and forum topics",
        },
        "add_channel_title": {
            "ru": "Добавить канал или тему",
            "en": "Add Channel or Topic",
        },
        "add_channel_hint": {
            "ru": "Введите @канал, ссылку или @чат:тема (например @proxy, @chat:42, https://t.me/c/123/45):",
            "en": "Enter @channel, @chat:topic or link (e.g. @proxy, @chat:42):",
        },
        "add_btn": {
            "ru": "Добавить",
            "en": "Add",
        },
        "channel_added_success": {
            "ru": "Канал добавлен: {0}",
            "en": "Channel added: {0}",
        },
        "import_title": {
            "ru": "Импорт прокси в Telegram",
            "en": "Import Telegram Proxies",
        },
        "import_sub": {
            "ru": "Импорт из буфера обмена или .txt файла",
            "en": "Import from clipboard or .txt file",
        },
        "import_from_clip": {
            "ru": "Импортировать из буфера обмена",
            "en": "Import from Clipboard",
        },
        "import_from_file": {
            "ru": "Импортировать из файла (.txt)",
            "en": "Import from File (.txt)",
        },
        "no_proxies_in_clip": {
            "ru": "В буфере обмена не найдены прокси",
            "en": "No proxies found in clipboard",
        },
        "no_txt_files_found": {
            "ru": "В папке Загрузки не найдено .txt файлов с прокси",
            "en": "No .txt files found in Downloads",
        },
        "choose_file_to_import": {
            "ru": "Выберите файл для импорта:",
            "en": "Choose file to import:",
        },
        "imported_success": {
            "ru": "Успешно импортировано {0} прокси в Telegram!",
            "en": "Successfully imported {0} proxies to Telegram!",
        },
        "menu_proxy": {
            "ru": "Прокси",
            "en": "Proxies",
        },
        "no_proxies_in_message": {
            "ru": "В сообщении не найдены ссылки на прокси",
            "en": "No proxy links found in message",
        },
        "checking_msg_proxies": {
            "ru": "Найдено {0} ссылок на прокси, проверка доступности...",
            "en": "Found {0} proxy links, testing latency...",
        },
        "sponsors_header": {
            "ru": "Полезные каналы",
            "en": "Useful Channels",
        },
        "open_channel": {
            "ru": "Перейти в канал",
            "en": "Open Channel",
        },
        "depth_all": {
            "ru": "Все сообщения",
            "en": "All messages",
        },
        "depth_default": {
            "ru": "По умолчанию",
            "en": "Default",
        },
        "channel_depth_title": {
            "ru": "Глубина поиска",
            "en": "Search Depth",
        },
        "channel_action_depth": {
            "ru": "Глубина поиска",
            "en": "Search depth",
        },
        "channel_action_delete": {
            "ru": "Удалить канал",
            "en": "Delete channel",
        },
        "saved": {
            "ru": "Сохранено",
            "en": "Saved",
        },
    }

    @classmethod
    def g(cls, key, *args):
        lang = "ru" if cls.is_ru() else "en"
        entry = cls.T.get(key, {})
        val = entry.get(lang, entry.get("en", key))
        if args:
            try:
                return val.format(*args)
            except Exception:
                return val
        return val


def _to_int(val):
    if val is None:
        return 0
    try:
        return int(val)
    except Exception:
        try:
            return int(str(val))
        except Exception:
            return 0


class Dex:
    def __init__(self, entry_class_name):
        self._class_name = entry_class_name
        self._class = None
        self._loaded = False

    @property
    def loaded(self):
        return self._loaded

    def extract_and_load(self, script_path):
        try:
            with open(script_path, "r", encoding="utf-8") as f:
                content = f.read()

            if ("# " + DEX_BEGIN) not in content or ("# " + DEX_END) not in content:
                return False

            body = content.split("# " + DEX_BEGIN, 1)[1].split("# " + DEX_END, 1)[0]
            clean_b64 = "".join(
                line.strip()[1:].strip()
                for line in body.splitlines()
                if line.strip().startswith("#")
            )
            if not clean_b64:
                return False

            compressed = base64.b64decode(clean_b64)
            dex_bytes = zlib.decompress(compressed)

            from java.nio import ByteBuffer
            bb = ByteBuffer.wrap(dex_bytes)

            ctx = ApplicationLoader.applicationContext
            p_loader = ctx.getClassLoader()
            InMemoryDexClassLoader = jclass("dalvik.system.InMemoryDexClassLoader")
            loader = InMemoryDexClassLoader(bb, p_loader)

            self._class = loader.loadClass(self._class_name)
            try:
                m_load = self._class.getMethod("load")
                m_load.invoke(None)
            except Exception:
                pass
            self._loaded = True
            log(f"Dex loaded successfully: {self._class_name}")
            return True
        except Exception as e:
            log(f"Dex load error: {e}")
            return False

    def unload(self):
        if not self._loaded or not self._class:
            return
        try:
            m_unload = self._class.getMethod("unload")
            m_unload.invoke(None)
        except Exception:
            pass
        self._class = None
        self._loaded = False

    def call(self, method_name, *args):
        if not self._loaded or not self._class:
            return None
        try:
            for m in self._class.getMethods():
                if m.getName() == method_name and len(m.getParameterTypes()) == len(args):
                    param_types = m.getParameterTypes()
                    coerced = []
                    for pt, a in zip(param_types, args):
                        pt_name = pt.getName()
                        if pt_name in ("int", "java.lang.Integer"):
                            coerced.append(jint(_to_int(a)))
                        elif pt_name in ("long", "java.lang.Long"):
                            coerced.append(jlong(_to_int(a)))
                        elif pt_name in ("boolean", "java.lang.Boolean"):
                            coerced.append(bool(a))
                        elif pt_name == "java.lang.String":
                            coerced.append(str(a) if a is not None else None)
                        else:
                            coerced.append(a)
                    return m.invoke(None, *coerced)
        except Exception as e:
            log(f"Dex.call({method_name}) error: {e}")
        return None


dex = Dex(ENTRY_CLASS)


def _call_dex(method_name, *args):
    if dex.loaded:
        try:
            return dex.call(method_name, *args)
        except Exception as e:
            log(f"_call_dex({method_name}) error: {e}")
    return None


def _send_sync_request(req, timeout=12.0):
    event = threading.Event()
    res_box = [None, None]

    def _cb(resp, err):
        res_box[0] = resp
        res_box[1] = err
        event.set()

    try:
        send_request(req, RequestCallback(_cb))
        event.wait(timeout=timeout)
    except Exception as e:
        res_box[1] = str(e)
    return res_box[0], res_box[1]


def _parse_proxy_link(raw_url):
    if not raw_url:
        return None
    raw = str(raw_url).strip().strip("'\"<>`()")
    try:
        if raw.lower().startswith(("socks5://", "socks://")):
            parsed = urllib.parse.urlsplit(raw)
            host = parsed.hostname
            port = parsed.port
            if host and port:
                user = urllib.parse.unquote(parsed.username) if parsed.username else ""
                pwd = urllib.parse.unquote(parsed.password) if parsed.password else ""
                tg_url = f"tg://socks?server={host}&port={port}"
                if user:
                    tg_url += f"&user={urllib.parse.quote(user)}"
                if pwd:
                    tg_url += f"&pass={urllib.parse.quote(pwd)}"
                return {
                    "type": "SOCKS5",
                    "server": host,
                    "port": int(port),
                    "user": user,
                    "pass": pwd,
                    "secret": "",
                    "url": tg_url,
                }
            return None

        norm = raw
        if norm.lower().startswith("tg:proxy"):
            norm = "tg://proxy" + norm[8:]
        elif norm.lower().startswith("tg:socks"):
            norm = "tg://socks" + norm[8:]
        elif norm.lower().startswith("tg:webproxy"):
            norm = "tg://webproxy" + norm[11:]
        elif norm.lower().startswith(("t.me/", "telegram.me/")):
            norm = "https://" + norm

        parsed = urllib.parse.urlsplit(norm)
        path = parsed.path.lower()
        netloc = parsed.netloc.lower()
        query = urllib.parse.parse_qs(parsed.query)

        server = query.get("server", [None])[0] or query.get("host", [None])[0]
        port_s = query.get("port", [None])[0]
        secret = query.get("secret", [""])[0]
        user = query.get("user", [""])[0]
        pwd = query.get("pass", [""])[0]

        if not server:
            return None

        is_webproxy = ("webproxy" in path) or (netloc == "webproxy") or norm.lower().startswith(("tg://webproxy", "tg:webproxy"))

        if port_s:
            try:
                port = int(port_s)
            except ValueError:
                port = 443 if (is_webproxy or secret) else None
        else:
            if is_webproxy or secret:
                port = 443
            else:
                port = None

        if port is None or port <= 0:
            return None

        if is_webproxy:
            if raw.lower().startswith("http"):
                tg_url = f"https://t.me/webproxy?server={server}&secret={secret}"
                if port != 443:
                    tg_url += f"&port={port}"
            else:
                tg_url = f"tg://webproxy?server={server}&secret={secret}"
                if port != 443:
                    tg_url += f"&port={port}"
            return {
                "type": "WebProxy",
                "server": server,
                "port": port,
                "secret": secret,
                "user": "",
                "pass": "",
                "url": tg_url,
                "link": tg_url,
            }

        is_mtproto = bool(secret) or ("proxy" in path) or (netloc == "proxy")
        if is_mtproto:
            tg_url = f"tg://proxy?server={server}&port={port}&secret={secret}"
            return {
                "type": "MTProto",
                "server": server,
                "port": port,
                "secret": secret,
                "user": "",
                "pass": "",
                "url": tg_url,
                "link": tg_url,
            }
        else:
            tg_url = f"tg://socks?server={server}&port={port}"
            if user:
                tg_url += f"&user={urllib.parse.quote(user)}"
            if pwd:
                tg_url += f"&pass={urllib.parse.quote(pwd)}"
            return {
                "type": "SOCKS5",
                "server": server,
                "port": port,
                "secret": "",
                "user": user,
                "pass": pwd,
                "url": tg_url,
                "link": tg_url,
            }
    except Exception:
        return None


def _extract_proxy_candidates_from_message(msg):
    candidates = []
    text = getattr(msg, "message", None) or ""

    reply_markup = getattr(msg, "reply_markup", None)
    if reply_markup is not None:
        rows = getattr(reply_markup, "rows", None)
        if rows is not None:
            for r in _iter_java_list(rows):
                buttons = getattr(r, "buttons", None)
                if buttons is not None:
                    for b in _iter_java_list(buttons):
                        b_url = getattr(b, "url", None)
                        if not b_url:
                            b_type = getattr(b, "type", None)
                            if b_type:
                                b_url = getattr(b_type, "url", None)
                        if b_url:
                            p = _parse_proxy_link(b_url)
                            if p:
                                candidates.append(p)

    entities = getattr(msg, "entities", None)
    if entities is not None:
        for ent in _iter_java_list(entities):
            ent_url = getattr(ent, "url", None)
            if ent_url:
                p = _parse_proxy_link(ent_url)
                if p:
                    candidates.append(p)
            cls_name = getattr(getattr(ent, "__class__", None), "__name__", "")
            if ("messageEntityUrl" in cls_name or "messageEntityTextUrl" in cls_name) and text:
                offset = getattr(ent, "offset", 0)
                length = getattr(ent, "length", 0)
                if length > 0 and offset >= 0 and (offset + length) <= len(text):
                    p = _parse_proxy_link(text[offset : offset + length])
                    if p:
                        candidates.append(p)

    if text:
        matches = re.finditer(
            r"(?:(?:https?://)?(?:t\.me/|telegram\.me/)|tg://|tg:)(?:proxy|socks|webproxy)\?[^\s\"\'<>`()]+",
            text,
            re.IGNORECASE,
        )
        for m in matches:
            p = _parse_proxy_link(m.group(0))
            if p:
                candidates.append(p)

        socks_matches = re.finditer(r"socks5?://[^\s\"\'<>`()]+", text, re.IGNORECASE)
        for m in socks_matches:
            p = _parse_proxy_link(m.group(0))
            if p:
                candidates.append(p)

    return candidates


def _extract_proxies_from_raw_text(text):
    if not text:
        return []
    candidates = []
    seen = set()

    matches = re.finditer(
        r"(?:(?:https?://)?(?:t\.me/|telegram\.me/)|tg://|tg:)(?:proxy|socks|webproxy)\?[^\s\"\'<>`()]+",
        text,
        re.IGNORECASE,
    )
    for m in matches:
        p = _parse_proxy_link(m.group(0))
        if p:
            k = (p["server"].lower(), p["port"], p["secret"])
            if k not in seen:
                seen.add(k)
                candidates.append(p)

    socks_matches = re.finditer(r"socks5?://[^\s\"\'<>`()]+", text, re.IGNORECASE)
    for m in socks_matches:
        p = _parse_proxy_link(m.group(0))
        if p:
            k = (p["server"].lower(), p["port"], p["secret"])
            if k not in seen:
                seen.add(k)
                candidates.append(p)

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        if "://" in line:
            continue
        parts = line.split(":")
        if len(parts) >= 2:
            srv = parts[0].strip()
            prt_str = parts[1].strip()
            if not prt_str.isdigit():
                continue
            prt = int(prt_str)
            if not (1 <= prt <= 65535):
                continue

            if len(parts) == 3:
                sec = parts[2].strip()
                k = (srv.lower(), prt, sec)
                if k not in seen:
                    seen.add(k)
                    candidates.append({
                        "type": "MTProto",
                        "server": srv,
                        "port": prt,
                        "secret": sec,
                        "user": "",
                        "pass": "",
                        "url": f"tg://proxy?server={srv}&port={prt}&secret={sec}",
                    })
            elif len(parts) == 4:
                u = parts[2].strip()
                pwd = parts[3].strip()
                k = (srv.lower(), prt, "")
                if k not in seen:
                    seen.add(k)
                    candidates.append({
                        "type": "SOCKS5",
                        "server": srv,
                        "port": prt,
                        "secret": "",
                        "user": u,
                        "pass": pwd,
                        "url": f"tg://socks?server={srv}&port={prt}&user={urllib.parse.quote(u)}&pass={urllib.parse.quote(pwd)}",
                    })
            elif len(parts) == 2:
                k = (srv.lower(), prt, "")
                if k not in seen:
                    seen.add(k)
                    candidates.append({
                        "type": "SOCKS5",
                        "server": srv,
                        "port": prt,
                        "secret": "",
                        "user": "",
                        "pass": "",
                        "url": f"tg://socks?server={srv}&port={prt}",
                    })

    return candidates


def _parse_channel_token(raw, default_flag=1):
    flag = default_flag
    if isinstance(raw, dict):
        flag = raw.get("flag", default_flag)
        raw = raw.get("url") or raw.get("chat") or ""
    raw = str(raw).strip()
    if not raw:
        return None

    res = None
    m_c = re.match(r"^(?:https?://)?(?:t\.me|telegram\.me)/c/(\d+)/(\d+)", raw, re.IGNORECASE)
    if m_c:
        cid = int(m_c.group(1))
        tid = int(m_c.group(2))
        clean_chat = str(-1000000000000 - cid if cid < 1000000000000 else -cid)
        res = {
            "chat": clean_chat,
            "topic_id": tid,
            "display": f"ID:{cid} (тема #{tid})",
        }
    else:
        m_pub = re.match(r"^(?:https?://)?(?:t\.me|telegram\.me)/([^/?#]+)/(\d+)", raw, re.IGNORECASE)
        if m_pub and m_pub.group(1).lower() not in ("proxy", "socks", "joinchat", "addstickers", "c"):
            ch = m_pub.group(1).lstrip("@")
            tid = int(m_pub.group(2))
            res = {
                "chat": ch,
                "topic_id": tid,
                "display": f"@{ch} (тема #{tid})",
            }
        elif ":" in raw:
            parts = raw.split(":", 1)
            ch = parts[0].strip().lstrip("@")
            try:
                tid = int(parts[1].strip())
                res = {
                    "chat": ch,
                    "topic_id": tid,
                    "display": f"@{ch} (тема #{tid})" if not ch.startswith("-") else f"ID:{ch} (тема #{tid})",
                }
            except ValueError:
                pass
        elif "/" in raw and not raw.startswith("http"):
            parts = raw.split("/", 1)
            ch = parts[0].strip().lstrip("@")
            try:
                tid = int(parts[1].strip())
                res = {
                    "chat": ch,
                    "topic_id": tid,
                    "display": f"@{ch} (тема #{tid})" if not ch.startswith("-") else f"ID:{ch} (тема #{tid})",
                }
            except ValueError:
                pass

        if not res:
            m_simple = re.match(r"^(?:https?://)?(?:t\.me|telegram\.me)/(?:joinchat/|\+)?([a-zA-Z0-9_+]+)", raw, re.IGNORECASE)
            if m_simple:
                ch = m_simple.group(1).lstrip("+")
            else:
                ch = raw.lstrip("@").strip()

            if not ch:
                return None

            res = {
                "chat": ch,
                "topic_id": None,
                "display": f"@{ch}" if not ch.startswith("-") else f"ID: {ch}",
            }

    try:
        res["flag"] = int(flag)
    except Exception:
        res["flag"] = 1
    return res


def _py_add_proxy(link):
    if dex.loaded:
        res = dex.call("pyAddProxy", link)
        if res is not None:
            return bool(res)
    try:
        from android.net import Uri
        from org.telegram.utils.proxy import ProxySettings
        from com.exteragram.messenger.proxy import ProxyController

        uri = Uri.parse(link)
        settings = ProxySettings.fromUri(uri)
        if settings is not None and settings.isValid():
            pi_class = jclass("org.telegram.messenger.SharedConfig$ProxyInfo")
            proxy_info = pi_class(settings)
            pc = ProxyController.getInstance()
            pc.addProxy(proxy_info)
            pc.saveProxyList()
            return True
    except Exception as e:
        log(f"proxy_manager add_proxy error: {e}")
    return False


def _py_apply_proxy(link):
    if dex.loaded:
        res = dex.call("pyApplyProxy", link)
        if res is not None:
            return bool(res)
    try:
        from android.net import Uri
        from org.telegram.utils.proxy import ProxySettings
        from com.exteragram.messenger.proxy import ProxyController

        uri = Uri.parse(link)
        settings = ProxySettings.fromUri(uri)
        if settings is not None and settings.isValid():
            pi_class = jclass("org.telegram.messenger.SharedConfig$ProxyInfo")
            proxy_info = pi_class(settings)
            pc = ProxyController.getInstance()
            added = pc.addProxy(proxy_info)
            pc.setCurrentProxy(added if added is not None else proxy_info)
            pc.saveProxyList()
            ConnectionsManager.setProxySettings(True, settings)
            return True
    except Exception as e:
        log(f"proxy_manager apply_proxy error: {e}")
    return False


def _batch_check_candidates(candidates_list, timeout_ms=3000):
    if dex.loaded:
        try:
            res_str = dex.call("pyBatchCheck", json.dumps(candidates_list), timeout_ms)
            if res_str:
                return json.loads(str(res_str))
        except Exception as e:
            log(f"dex pyBatchCheck error: {e}")
    return []


class AuthorChannelHelper:
    CONFIG_URL = "https://raw.githubusercontent.com/MaxExteraPlugins/config/main/links.json"

    DEFAULT_LINKS = {
        "channel_username": "MaxExteraPlugins",
        "channel_url": "https://t.me/MaxExteraPlugins",
        "header": {"ru": "MaxExteraPlugins", "en": "MaxExteraPlugins"},
        "buttons": [
            {"title": {"ru": "Канал автора", "en": "Author's Channel"}, "icon": "msg_channel", "url": "https://t.me/MaxExteraPlugins"},
            {"title": {"ru": "Поддержать автора", "en": "Support author"}, "icon": "msg_share", "url": "https://t.me/itNotMax"},
            {"title": {"ru": "Сообщить о баге", "en": "Report a bug"}, "icon": "msg_help", "url": "https://t.me/itNotMax"}
        ]
    }

    _TEXTS = {
        "install_card_title": {"ru": "Подпишитесь на канал", "en": "Subscribe to channel"},
        "install_card_msg": {
            "ru": (
                "Мы делаем плагины бесплатными и доступными для всех: пожалуйста, "
                "поддержите нас подпиской на официальный канал @MaxExteraPlugins.\n\n"
                "Там публикуются обновления, важные новости и анонсы новых плагинов."
            ),
            "en": (
                "We keep our plugins free and accessible for everyone: please "
                "support us by subscribing to our official channel @MaxExteraPlugins.\n\n"
                "Updates, news, and new plugin announcements are published there."
            )
        },
        "open_channel_btn": {"ru": "Перейти на канал", "en": "Open channel"},
        "remind_later_btn": {"ru": "Напомнить позже", "en": "Remind later"},
        "never_show_btn": {"ru": "Больше не показывать", "en": "Don't show again"},
        "sub_thank_you": {"ru": "Спасибо за подписку на наш канал!", "en": "Thank you for subscribing to our channel!"},
        "settings_channel": {"ru": "Канал автора", "en": "Author's Channel"},
        "settings_donate": {"ru": "Поддержать автора", "en": "Support author"},
        "settings_bug_report": {"ru": "Сообщить о баге", "en": "Report a bug"}
    }

    @classmethod
    def is_ru(cls) -> bool:
        try:
            from org.telegram.messenger import LocaleController
            lang = str(LocaleController.getInstance().getCurrentLocale().getLanguage() or "").lower()
            return lang in ("ru", "be", "uk", "kk")
        except Exception:
            return True

    @classmethod
    def tr(cls, key: str) -> str:
        entry = cls._TEXTS.get(key, {})
        return entry.get("ru" if cls.is_ru() else "en", entry.get("en", key))

    def __init__(self, plugin, icon_sticker=None):
        self.plugin = plugin
        self.icon_sticker = icon_sticker or getattr(plugin, "__icon__", "MaxExteraPluginsStickers/1")
        self._stopped = False
        self._active_dialog = None

    def on_load(self):
        self._stopped = False
        self._active_dialog = None
        self._load_remote_links_async()
        self._check_initial_subscription_flow()

    def on_event(self, event_type):
        try:
            name = str(event_type).upper()
            if name.endswith("START") or name.endswith("RESUME"):
                if self.is_subscribed() and not self.plugin.get_setting("was_subscribed", False):
                    self.plugin.set_setting("was_subscribed", True)
                    self._dismiss_active_dialog()
                    self._notify_subscribed()
        except Exception:
            pass

    def on_unload(self):
        self._stopped = True
        self._dismiss_active_dialog()

    def is_allowed(self) -> bool:
        return True

    def _get_links(self) -> dict:
        raw = self.plugin.get_setting("remote_links_cache", None)
        if raw:
            try:
                import json
                data = json.loads(raw)
                if isinstance(data, dict): return data
            except Exception:
                pass
        return self.DEFAULT_LINKS

    def _load_remote_links_async(self):
        def worker():
            try:
                import urllib.request, json
                req = urllib.request.Request(self.CONFIG_URL, headers={"User-Agent": "MaxExteraPlugins"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if isinstance(data, dict):
                            self.plugin.set_setting("remote_links_cache", json.dumps(data))
                            self._resolve_channel_in_telegram(data.get("channel_username", "MaxExteraPlugins"))
            except Exception:
                pass
        import threading
        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _open_url(self, url: str, view=None):
        try:
            from org.telegram.messenger.browser import Browser
            from org.telegram.messenger import ApplicationLoader
            from client_utils import get_last_fragment
            ctx = None
            if view is not None:
                ctx = view.getContext()
            if ctx is None:
                frag = get_last_fragment()
                if frag is not None:
                    ctx = frag.getParentActivity()
            if ctx is None:
                ctx = ApplicationLoader.applicationContext
            Browser.openUrl(ctx, url)
        except Exception:
            pass

    def _resolve_channel_in_telegram(self, username: str):
        try:
            from org.telegram.messenger import UserConfig, MessagesController
            from org.telegram.tgnet import TLRPC, ConnectionsManager, RequestDelegate
            clean_user = username.lstrip("@")
            acc = int(UserConfig.selectedAccount)
            mc = MessagesController.getInstance(acc)
            if not mc: return
            req = TLRPC.TL_contacts_resolveUsername()
            req.username = clean_user
            helper_ref = self
            class Delegate(RequestDelegate):
                def run(self, response, error):
                    if error is None and response is not None:
                        chats = getattr(response, "chats", None)
                        if chats is not None and chats.size() > 0:
                            for a in range(UserConfig.MAX_ACCOUNT_COUNT):
                                try:
                                    if UserConfig.getInstance(a).isClientActivated():
                                        MessagesController.getInstance(a).putChats(chats, False)
                                except Exception:
                                    pass
                            c = chats.get(0)
                            cid = getattr(c, "id", 0)
                            if cid > 0:
                                helper_ref.plugin.set_setting("resolved_channel_id", int(cid))
            ConnectionsManager.getInstance(acc).sendRequest(req, Delegate())
        except Exception:
            pass

    def is_subscribed(self) -> bool:
        try:
            from org.telegram.messenger import UserConfig, MessagesController, MessagesStorage
            from java.lang import Long as JLong
            links = self._get_links()
            target = str(links.get("channel_username", "MaxExteraPlugins") or "MaxExteraPlugins").lower().lstrip("@")
            saved_cid = int(self.plugin.get_setting("resolved_channel_id", 0) or 0)

            for acc in range(UserConfig.MAX_ACCOUNT_COUNT):
                try:
                    uconf = UserConfig.getInstance(acc)
                    if not uconf.isClientActivated():
                        continue

                    mc = MessagesController.getInstance(acc)
                    storage = MessagesStorage.getInstance(acc)

                    # 1. In-memory check by resolved_channel_id
                    if saved_cid and mc is not None:
                        try:
                            c = mc.getChat(JLong(saved_cid))
                            if c is not None:
                                left = bool(getattr(c, "left", True))
                                kicked = bool(getattr(c, "kicked", False))
                                if not left and not kicked:
                                    return True
                        except Exception:
                            pass

                    # 2. In-memory check by username
                    if mc is not None:
                        try:
                            chat = mc.getUserOrChat(target)
                            if chat is not None:
                                cid = int(getattr(chat, "id", 0) or 0)
                                if cid and not saved_cid:
                                    saved_cid = cid
                                    self.plugin.set_setting("resolved_channel_id", cid)
                                left = bool(getattr(chat, "left", True))
                                kicked = bool(getattr(chat, "kicked", False))
                                if not left and not kicked:
                                    return True
                        except Exception:
                            pass

                    # 3. In-memory dialogs check
                    if mc is not None:
                        try:
                            dialog_lists = []
                            d_chan = getattr(mc, "dialogsChannelsOnly", None)
                            if d_chan is not None and d_chan.size() > 0:
                                dialog_lists.append(d_chan)
                            d_all = getattr(mc, "allDialogs", None)
                            if d_all is not None and d_all.size() > 0:
                                dialog_lists.append(d_all)

                            for d_list in dialog_lists:
                                limit = min(d_list.size(), 300)
                                for i in range(limit):
                                    d = d_list.get(i)
                                    did = getattr(d, "id", 0)
                                    if did < 0:
                                        cid = -did
                                        if saved_cid and cid == saved_cid:
                                            c = mc.getChat(JLong(cid))
                                            if c is not None:
                                                left = bool(getattr(c, "left", True))
                                                kicked = bool(getattr(c, "kicked", False))
                                                if not left and not kicked:
                                                    return True
                                        else:
                                            c = mc.getChat(JLong(cid))
                                            if c is not None:
                                                uname = str(getattr(c, "username", "") or "").lower()
                                                if uname == target:
                                                    if not saved_cid:
                                                        saved_cid = cid
                                                        self.plugin.set_setting("resolved_channel_id", cid)
                                                    left = bool(getattr(c, "left", True))
                                                    kicked = bool(getattr(c, "kicked", False))
                                                    if not left and not kicked:
                                                        return True
                        except Exception:
                            pass

                    # 4. Storage check (SQLite on disk)
                    if storage is not None:
                        if saved_cid:
                            try:
                                if storage.containsLocalDialog(JLong(-saved_cid)):
                                    c_sync = storage.getChatSync(JLong(saved_cid))
                                    if c_sync is not None:
                                        left = bool(getattr(c_sync, "left", True))
                                        kicked = bool(getattr(c_sync, "kicked", False))
                                        if not left and not kicked:
                                            return True
                                    else:
                                        return True
                            except Exception:
                                pass

                        try:
                            db = storage.getDatabase()
                            if db is not None:
                                if saved_cid:
                                    try:
                                        cur = db.queryFinalized("SELECT did FROM dialogs WHERE did = ?", [JLong(-saved_cid)])
                                        if cur is not None:
                                            has_row = bool(cur.next())
                                            cur.dispose()
                                            if has_row:
                                                return True
                                    except Exception:
                                        pass

                                try:
                                    cur = db.queryFinalized("SELECT uid, name FROM chats WHERE LOWER(name) LIKE ?", ["%" + target + "%"])
                                    if cur is not None:
                                        while cur.next():
                                            c_id = cur.longValue(0)
                                            if c_id > 0:
                                                if not saved_cid:
                                                    saved_cid = int(c_id)
                                                    self.plugin.set_setting("resolved_channel_id", saved_cid)
                                                cur_d = db.queryFinalized("SELECT did FROM dialogs WHERE did = ?", [JLong(-c_id)])
                                                if cur_d is not None:
                                                    in_dlg = bool(cur_d.next())
                                                    cur_d.dispose()
                                                    if in_dlg:
                                                        cur.dispose()
                                                        return True
                                        cur.dispose()
                                except Exception:
                                    pass
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass
        return False

    def prepend_settings(self, original_items=None) -> list:
        from ui.settings import Header, Text, Divider

        if original_items and any(getattr(x, "text", "") == "MaxExteraPlugins" for x in original_items if type(x).__name__ == "Header"):
            return original_items

        links = self._get_links()
        buttons_config = links.get("buttons")

        author_items = []
        if isinstance(buttons_config, list):
            for btn in buttons_config:
                if not isinstance(btn, dict): continue
                raw_url = btn.get("url")
                if isinstance(raw_url, dict):
                    keys = [
                        getattr(self.plugin, "id", None),
                        globals().get("__id__", None),
                        globals().get("__name__", None),
                        self.plugin.__class__.__name__
                    ]
                    keys += [k.lower() for k in keys if isinstance(k, str)]
                    resolved_url = ""
                    for k in keys:
                        if k and k in raw_url:
                            resolved_url = str(raw_url[k] or "").strip()
                            if resolved_url:
                                break
                    url = resolved_url
                elif isinstance(raw_url, str):
                    url = raw_url.strip()
                else:
                    url = ""

                if not url: continue

                title_data = btn.get("title")
                if isinstance(title_data, dict):
                    title = title_data.get("ru" if self.is_ru() else "en") or title_data.get("en") or title_data.get("ru") or ""
                elif isinstance(title_data, str) and title_data:
                    title = title_data
                else:
                    title = btn.get("title_ru" if self.is_ru() else "title_en") or btn.get("title_ru") or btn.get("title_en") or ""

                if not title: continue
                icon = str(btn.get("icon") or "msg_channel").strip()

                def make_click(u):
                    return lambda v=None: self._open_url(u, v)

                author_items.append(Text(text=title, icon=icon, on_click=make_click(url)))
        else:
            for k, text_key, icon_name in [
                ("channel_url", "settings_channel", "msg_channel"),
                ("donate_url", "settings_donate", "msg_share"),
                ("bug_report_url", "settings_bug_report", "msg_help")
            ]:
                u = str(links.get(k) or "").strip()
                if u:
                    author_items.append(Text(text=self.tr(text_key), icon=icon_name, on_click=lambda v=None, target_u=u: self._open_url(target_u, v)))

        res = []
        if author_items:
            hdr = links.get("header") or "MaxExteraPlugins"
            if isinstance(hdr, dict):
                hdr_text = hdr.get("ru" if self.is_ru() else "en") or hdr.get("ru") or hdr.get("en") or "MaxExteraPlugins"
            else:
                hdr_text = str(hdr)
            res.append(Header(text=hdr_text))
            res.extend(author_items)
            res.append(Divider())

        cleaned_items = []
        if original_items:
            skipping_info = False
            for item in original_items:
                item_cls = type(item).__name__
                if item_cls == "Header":
                    txt = str(getattr(item, "text", "") or getattr(item, "title", "") or "").lower()
                    if "информ" in txt or "inform" in txt:
                        skipping_info = True
                        continue
                    else:
                        skipping_info = False
                elif skipping_info and item_cls == "Divider":
                    skipping_info = False
                    continue

                if not skipping_info:
                    cleaned_items.append(item)

        res.extend(cleaned_items)
        return res

    def _dismiss_active_dialog(self):
        try:
            if self._active_dialog is not None:
                self._active_dialog.dismiss()
        except Exception:
            pass
        self._active_dialog = None

    def _is_dialog_active(self) -> bool:
        try:
            if self._active_dialog is not None:
                return bool(self._active_dialog.isShowing())
        except Exception:
            pass
        return False

    def _notify_subscribed(self):
        try:
            from ui.bulletin import BulletinHelper
            BulletinHelper.show_success(self.tr("sub_thank_you"))
        except Exception:
            pass

    def _check_initial_subscription_flow(self):
        def flow():
            try:
                if self.plugin.get_setting("never_show_sub_prompt", False):
                    return

                current_ver = str(getattr(self.plugin, "__version__", globals().get("__version__", "1.0.0")) or "1.0.0")
                last_prompted_ver = self.plugin.get_setting("last_prompted_version", None)

                # Only show on install (last_prompted_ver is None) or update (last_prompted_ver != current_ver)
                if last_prompted_ver is not None and str(last_prompted_ver) == current_ver:
                    return

                if self.is_subscribed():
                    self.plugin.set_setting("last_prompted_version", current_ver)
                    self.plugin.set_setting("was_subscribed", True)
                    return

                self._show_subscription_prompt(current_ver)
            except Exception:
                pass
        from android_utils import run_on_ui_thread
        run_on_ui_thread(flow, 1500)

    def _create_card_content(self, act, title_text, message_text):
        try:
            from android.widget import LinearLayout, TextView, ImageView
            from android.view import Gravity
            from org.telegram.messenger import AndroidUtilities
            from org.telegram.ui.ActionBar import Theme
            from client_utils import create_sticker_drawable

            dp = AndroidUtilities.dp

            layout = LinearLayout(act)
            layout.setOrientation(LinearLayout.VERTICAL)
            layout.setGravity(Gravity.CENTER_HORIZONTAL)
            layout.setPadding(dp(24), dp(20), dp(24), dp(10))

            sticker_drawable = None
            if self.icon_sticker:
                try:
                    sticker_drawable = create_sticker_drawable(self.icon_sticker, dp(120), dp(120))
                except Exception:
                    pass

            if sticker_drawable is not None:
                iv = ImageView(act)
                iv.setImageDrawable(sticker_drawable)
                lp_iv = LinearLayout.LayoutParams(dp(120), dp(120))
                lp_iv.gravity = Gravity.CENTER_HORIZONTAL
                lp_iv.bottomMargin = dp(14)
                layout.addView(iv, lp_iv)

            if title_text:
                tt = TextView(act)
                tt.setText(title_text)
                tt.setTextSize(19.0)
                tt.setTypeface(AndroidUtilities.bold())
                tt.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
                tt.setGravity(Gravity.CENTER_HORIZONTAL)
                lp_t = LinearLayout.LayoutParams(-1, -2)
                lp_t.bottomMargin = dp(8)
                layout.addView(tt, lp_t)

            if message_text:
                mt = TextView(act)
                mt.setText(message_text)
                mt.setTextSize(15.0)
                mt.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
                mt.setLineSpacing(float(dp(3)), 1.0)
                mt.setGravity(Gravity.CENTER_HORIZONTAL)
                lp_m = LinearLayout.LayoutParams(-1, -2)
                mt.setLayoutParams(lp_m)
                layout.addView(mt)

            return layout
        except Exception:
            return None

    def _show_subscription_prompt(self, current_ver: str):
        def show():
            try:
                if self._is_dialog_active(): return
                from client_utils import get_last_fragment
                frag = get_last_fragment()
                if not frag: return
                act = frag.getParentActivity()
                if not act: return

                links = self._get_links()
                url = links.get("channel_url", "https://t.me/MaxExteraPlugins")

                from ui.alert import AlertDialogBuilder
                b = AlertDialogBuilder(act)
                content = self._create_card_content(act, self.tr("install_card_title"), self.tr("install_card_msg"))
                if content is not None:
                    b.set_view(content)
                else:
                    b.set_title(self.tr("install_card_title"))
                    b.set_message(self.tr("install_card_msg"))

                def on_open_channel(dialog, which):
                    self._dismiss_active_dialog()
                    self.plugin.set_setting("last_prompted_version", current_ver)
                    self._open_url(url)

                def on_remind_later(dialog, which):
                    self._dismiss_active_dialog()
                    self.plugin.set_setting("last_prompted_version", current_ver)

                def on_never_show(dialog, which):
                    self._dismiss_active_dialog()
                    self.plugin.set_setting("never_show_sub_prompt", True)
                    self.plugin.set_setting("last_prompted_version", current_ver)

                b.set_positive_button(self.tr("open_channel_btn"), on_open_channel)
                b.set_neutral_button(self.tr("remind_later_btn"), on_remind_later)
                b.set_negative_button(self.tr("never_show_btn"), on_never_show)

                def on_dismiss(builder):
                    self.plugin.set_setting("last_prompted_version", current_ver)
                try:
                    b.set_on_dismiss_listener(on_dismiss)
                except Exception:
                    pass

                self._active_dialog = b.show()
                self.plugin.set_setting("last_prompted_version", current_ver)
            except Exception:
                pass
        from android_utils import run_on_ui_thread
        run_on_ui_thread(show)

class Plugin(BasePlugin):
    def __init__(self):
        super().__init__()
        self._channel_helper = AuthorChannelHelper(self)
        self._last_results = []
        self._is_scanning = False
        self._cancel_scan = False
        self._is_auto_connecting = False
        self._loading_dialog = None
        self._loading_builder = None
        self._progress = 0

    def on_plugin_load(self):
        if hasattr(self, "_channel_helper"):
            self._channel_helper.on_load()
        dex.extract_and_load(__file__)
        self._load_saved_results()
        self._preload_remote_configs()

        auto_conn = bool(self.get_setting("auto_switch_enabled", True)) and bool(self.get_setting("auto_connect_on_enable", True))
        if dex.loaded:
            dex.call("pySetAutoConnect", auto_conn)

            class AutoConnectCallback(dynamic_proxy(Runnable)):
                def __init__(self, pl):
                    super().__init__()
                    self.pl = pl

                def run(self):
                    threading.Thread(target=self.pl._auto_connect_worker, daemon=True).start()

            dex.call("pySetAutoConnectFallback", AutoConnectCallback(self))

            class QuickSearchCallback(dynamic_proxy(Runnable)):
                def __init__(self, pl):
                    super().__init__()
                    self.pl = pl

                def run(self):
                    self.pl._quick_search_and_connect()

            dex.call("pySetQuickSearchCallback", QuickSearchCallback(self))

        try:
            self.add_menu_item(
                MenuItemData(
                    menu_type=MenuItemType.MESSAGE_CONTEXT_MENU,
                    text=L.g("menu_proxy"),
                    icon="msg_link2",
                    item_id="proxy_from_message",
                    on_click=self._on_message_proxy_click,
                )
            )
        except Exception as e:
            self.log(f"add_menu_item error: {e}")

        self._start_media_watchdog()
        self._start_startup_tasks()
        self._start_periodic_worker()

    def on_plugin_unload(self):
        if hasattr(self, "_channel_helper"):
            self._channel_helper.on_unload()
        self._periodic_worker_started = False
        self._stop_media_watchdog()
        dex.unload()

    def _start_media_watchdog(self):
        if hasattr(self, "_media_watchdog_thread") and self._media_watchdog_thread and self._media_watchdog_thread.is_alive():
            return
        self._watchdog_running = True

        def loop():
            last_bytes = -1
            stalled_seconds = 0
            no_conn_seconds = 0
            last_switch_time = 0.0
            last_stat_time = 0.0
            last_auto_enable_time = 0.0

            while getattr(self, "_watchdog_running", False):
                time.sleep(1.0)
                try:
                    now = time.time()
                    from org.telegram.messenger import SharedConfig
                    proxy_enabled = bool(SharedConfig.isProxyEnabled())

                    # Auto-enable proxy & search when Telegram has no connection and proxy is OFF
                    if not proxy_enabled:
                        stalled_seconds = 0
                        last_bytes = -1
                        if bool(self.get_setting("auto_enable_no_conn", True)) and not getattr(self, "_is_quick_searching", False):
                            if now - last_auto_enable_time >= 60.0:
                                conn_state = _call_dex("pyGetConnectionState")
                                # 1 == ConnectionStateConnecting (has network, failing to reach DC directly)
                                if conn_state == 1:
                                    no_conn_seconds += 1
                                    delay_sec = int(self.get_setting("auto_enable_no_conn_delay", 3))
                                    if no_conn_seconds >= delay_sec:
                                        no_conn_seconds = 0
                                        last_auto_enable_time = now
                                        threading.Thread(
                                            target=self._auto_enable_on_no_connection_worker,
                                            daemon=True,
                                            name="ProxyAutoEnableNoConn",
                                        ).start()
                                else:
                                    no_conn_seconds = 0
                            else:
                                no_conn_seconds = 0
                        else:
                            no_conn_seconds = 0
                        continue

                    no_conn_seconds = 0
                    if not bool(self.get_setting("auto_switch_enabled", True)) or not bool(self.get_setting("auto_switch_media", True)):
                        stalled_seconds = 0
                        last_bytes = -1
                        continue

                    if now - last_switch_time < 15.0:
                        stalled_seconds = 0
                        last_bytes = -1
                        continue

                    bytes_sum = _call_dex("pyGetMediaTransferBytes")
                    if bytes_sum is None:
                        bytes_sum = _call_dex("pyGetDownloadingBytes")
                    if bytes_sum is None or bytes_sum < 0:
                        stalled_seconds = 0
                        last_bytes = -1
                        continue

                    timeout_sec = int(self.get_setting("media_stall_timeout_seconds", 8))
                    if last_bytes >= 0 and bytes_sum == last_bytes:
                        stalled_seconds += 1
                        if stalled_seconds >= timeout_sec:
                            stalled_seconds = 0
                            last_bytes = -1
                            last_switch_time = time.time()
                            cur_key = self._get_current_active_proxy_key()
                            if cur_key:
                                self._add_proxy_cooldown(cur_key)
                                self._record_proxy_stat(cur_key, success=False)
                            cooldown_json = json.dumps(self._get_active_cooldown_set())
                            strategy = int(self.get_setting("auto_switch_strategy", 0))
                            smart_json = json.dumps(self._get_smart_scores_dict()) if self.get_setting("smart_analysis_enabled", True) else ""
                            switched = _call_dex("pySwitchToNextProxy", cooldown_json, strategy, smart_json)
                            if switched:
                                if self._should_notify("notify_on_switch"):
                                    run_on_ui_thread(
                                        lambda: BulletinHelper.show_info(L.g("media_stalled_switched"))
                                    )
                                self._on_proxy_switched()
                    else:
                        if last_bytes >= 0 and bytes_sum > last_bytes:
                            if now - last_stat_time >= 30.0:
                                last_stat_time = now
                                cur_key = self._get_current_active_proxy_key()
                                if cur_key:
                                    self._record_proxy_stat(cur_key, success=True)
                        last_bytes = bytes_sum
                        stalled_seconds = 0
                except Exception:
                    pass

        t = threading.Thread(target=loop, daemon=True, name="ProxyMediaWatchdog")
        self._media_watchdog_thread = t
        t.start()

    def _stop_media_watchdog(self):
        self._watchdog_running = False

    def _start_startup_tasks(self):
        def worker():
            time.sleep(10.0)
            try:
                from org.telegram.messenger import SharedConfig
                if not SharedConfig.isProxyEnabled():
                    return
                if self.get_setting("auto_clean_enabled", False) and self.get_setting("auto_clean_on_startup", False):
                    self._trigger_background_auto_clean()
                    time.sleep(3.0)
                if self.get_setting("auto_add_enabled", False) and self.get_setting("auto_add_on_startup", False):
                    self._trigger_background_auto_scan(auto_add_on_finish=True)
            except Exception as e:
                self.log(f"Startup tasks error: {e}")

        threading.Thread(target=worker, daemon=True, name="ProxyStartupWorker").start()

    def _on_proxy_switched(self):
        try:
            from org.telegram.messenger import SharedConfig
            if not SharedConfig.isProxyEnabled():
                return
            if self.get_setting("auto_clean_enabled", False) and self.get_setting("auto_clean_on_switch", False):
                self._trigger_background_auto_clean()
            if self.get_setting("auto_add_enabled", False) and self.get_setting("auto_add_on_switch", False):
                self._trigger_background_auto_scan(auto_add_on_finish=True)
        except Exception as e:
            self.log(f"_on_proxy_switched error: {e}")

    def _trigger_background_auto_clean(self):
        if getattr(self, "_is_auto_cleaning", False):
            return
        try:
            from org.telegram.messenger import SharedConfig
            if not SharedConfig.isProxyEnabled():
                return
        except Exception:
            return

        self._is_auto_cleaning = True

        def worker():
            try:
                from org.telegram.messenger import SharedConfig
                if not SharedConfig.isProxyEnabled():
                    return
                if dex.loaded:
                    safe = bool(self.get_setting("safe_clean_enabled", True))
                    prot_keys = self._get_recently_added_keys() if safe else []
                    res_str = dex.call("pyRemoveDeadProxies", 2000, safe, json.dumps(prot_keys))
                    if res_str:
                        data = json.loads(str(res_str))
                        deleted = data.get("deleted", 0)
                        kept = data.get("kept", 0)
                        aborted = data.get("aborted", False)
                        if not aborted and deleted > 0 and self._should_notify("notify_on_clean"):
                            run_on_ui_thread(
                                lambda: BulletinHelper.show_info(L.g("auto_cleaned_dead", deleted, kept))
                            )
            except Exception as e:
                self.log(f"Auto clean error: {e}")
            finally:
                self._is_auto_cleaning = False

        threading.Thread(target=worker, daemon=True, name="AutoCleanDeadWorker").start()

    def _trigger_background_auto_scan(self, auto_add_on_finish=True):
        if getattr(self, "_is_auto_scanning", False) or getattr(self, "_is_scanning", False):
            return
        try:
            from org.telegram.messenger import SharedConfig
            if not SharedConfig.isProxyEnabled():
                return
        except Exception:
            return

        self._is_auto_scanning = True

        def worker():
            try:
                from org.telegram.messenger import SharedConfig
                if not SharedConfig.isProxyEnabled():
                    return
                channels = [c for c in self._get_channels_config() if c.get("enabled", True)]
                if not channels:
                    return
                depth = self._get_depth_days()
                alive = self._perform_scan(channels, depth, max_batches=6)
                if alive:
                    whitelist = self._get_whitelist()
                    if whitelist:
                        alive = [p for p in alive if p.get("country_code", "").upper() in whitelist]
                if alive and auto_add_on_finish:
                    from org.telegram.messenger import SharedConfig
                    if not SharedConfig.isProxyEnabled():
                        return
                    links = [p.get("link") or p.get("url") for p in alive if (p.get("link") or p.get("url"))]
                    if links:
                        added = dex.call("pyAddAllProxies", json.dumps(links))
                        if added is not None and int(added) > 0:
                            self._record_added_proxies(alive)
                            self._enforce_proxy_limit()
                            if self._should_notify("notify_on_add"):
                                cnt = int(added)
                                run_on_ui_thread(
                                    lambda: BulletinHelper.show_info(L.g("auto_scan_added", cnt))
                                )
            except Exception as e:
                self.log(f"Auto scan error: {e}")
            finally:
                self._is_auto_scanning = False

        threading.Thread(target=worker, daemon=True, name="AutoScanWorker").start()

    def _load_saved_results(self):
        try:
            raw = self.get_setting("last_scan_results", "[]")
            self._last_results = json.loads(raw)
        except Exception:
            self._last_results = []

    def _save_results(self, results):
        self._last_results = results
        try:
            self.set_setting("last_scan_results", json.dumps(results))
        except Exception:
            pass

    DEFAULT_BASE_CHANNELS = [
        {"url": "https://t.me/webproxy", "flag": 1},
        {"url": "https://t.me/ProxyMTProto", "flag": 1},
        {"url": "https://t.me/ProxyFree_Ru", "flag": 1},
        {"url": "https://t.me/RaViraNet/16", "flag": 1},
        {"url": "https://t.me/wildVF/65", "flag": 1},
        {"url": "https://t.me/urlsources/43266", "flag": 1},
        {"url": "https://t.me/urlsources/33189", "flag": 0},
    ]

    def _load_base_channels(self):
        cached = self.get_setting("cached_base_channels", None)
        if cached:
            try:
                data = _safe_json_loads(cached)
                if isinstance(data, list) and data:
                    return data
            except Exception:
                pass
        return list(self.DEFAULT_BASE_CHANNELS)

    def _preload_remote_configs(self):
        def _bg():
            # 1. Base channels
            try:
                urls = [
                    "https://raw.githubusercontent.com/MaxExteraPlugins/proxy_manager/main/base_proxy.json",
                    "https://github.com/MaxExteraPlugins/proxy_manager/raw/main/base_proxy.json",
                ]
                for url in urls:
                    try:
                        text = _fetch_url_text(url)
                        if text:
                            data = _safe_json_loads(text)
                            if data:
                                chs = data.get("base_channels", [])
                                if chs and isinstance(chs, list):
                                    self.set_setting("cached_base_channels", json.dumps(chs))
                                    if not self.get_setting("channels_customized", False):
                                        new_cfg = []
                                        for t in chs:
                                            p = _parse_channel_token(t)
                                            if p and not any(c.get("chat") == p["chat"] and c.get("topic_id") == p["topic_id"] for c in new_cfg):
                                                new_cfg.append(p)
                                        if new_cfg:
                                            self.set_setting("channels_config", json.dumps(new_cfg))
                                    break
                    except Exception as e:
                        self.log(f"Base channels fetch error for {url}: {e}")
            except Exception as e:
                self.log(f"Preload base channels error: {e}")

            # 2. Sponsors
            try:
                urls = [
                    "https://raw.githubusercontent.com/MaxExteraPlugins/proxy_manager/main/proxy_sponsors.json",
                    "https://github.com/MaxExteraPlugins/proxy_manager/raw/main/proxy_sponsors.json",
                ]
                for url in urls:
                    try:
                        text = _fetch_url_text(url)
                        if text:
                            data = _safe_json_loads(text)
                            if data and "channels" in data:
                                chs = data.get("channels", [])
                                if chs and isinstance(chs, list):
                                    self._cached_sponsors = chs
                                    self.set_setting("cached_sponsors", json.dumps(chs))
                                    self.log(f"Sponsors updated successfully: {len(chs)} channels")
                                    break
                    except Exception as e:
                        self.log(f"Sponsors fetch error for {url}: {e}")
            except Exception as e:
                self.log(f"Preload sponsors error: {e}")

        threading.Thread(target=_bg, daemon=True).start()

    def _get_channels_config(self):
        raw = self.get_setting("channels_config", None)
        if raw:
            try:
                data = _safe_json_loads(raw)
                if isinstance(data, list) and data:
                    return data
            except Exception:
                pass

        base = self._load_base_channels()
        res = []
        for t in base:
            parsed = _parse_channel_token(t)
            if parsed and not any(c.get("chat") == parsed["chat"] and c.get("topic_id") == parsed["topic_id"] for c in res):
                res.append(parsed)
        if res:
            try:
                self.set_setting("channels_config", json.dumps(res))
            except Exception:
                pass
            return res

        legacy_str = self.get_setting("channels_input", "https://t.me/webproxy, https://t.me/ProxyMTProto, https://t.me/ProxyFree_Ru")
        tokens = re.split(r"[\s,;\n]+", legacy_str.strip())
        res = []
        for t in tokens:
            parsed = _parse_channel_token(t)
            if parsed and not any(c.get("chat") == parsed["chat"] and c.get("topic_id") == parsed["topic_id"] for c in res):
                res.append(parsed)
        return res

    def _set_channels_config(self, cfg_list):
        self.set_setting("channels_customized", True)
        self.set_setting("channels_config", json.dumps(cfg_list))
        tokens = []
        for c in cfg_list:
            if c.get("topic_id"):
                tokens.append(f"{c['chat']}:{c['topic_id']}")
            else:
                tokens.append(c["chat"])
        self.set_setting("channels_input", ", ".join(tokens))

    def _get_channel_tokens(self):
        return self._get_channels_config()

    def _set_channel_tokens(self, tokens):
        cfg = []
        for t in tokens:
            if isinstance(t, dict):
                cfg.append(t)
            else:
                p = _parse_channel_token(t)
                if p:
                    cfg.append(p)
        self._set_channels_config(cfg)

    def _get_depth_days(self):
        depth_idx = self.get_setting("search_depth", 0)
        depth_map = {0: 1, 1: 3, 2: 7}
        return depth_map.get(depth_idx, 1)

    def _show_loading_dialog(self, act):
        def show():
            try:
                alert_type = getattr(AlertDialogBuilder, "ALERT_TYPE_LOADING", None)
                if alert_type is not None:
                    builder = AlertDialogBuilder(act, alert_type)
                else:
                    builder = AlertDialogBuilder(act)
                builder.set_title(L.g("scan_progress_title"))
                builder.set_negative_button(L.g("cancel"), lambda bld, w: self._cancel_check(bld))
                dialog = builder.show()
                try:
                    dialog.set_progress(self._progress)
                except Exception:
                    pass
                try:
                    dialog.setCancelable(False)
                except Exception:
                    pass
                self._loading_dialog = dialog
                self._loading_builder = builder
            except Exception as e:
                self.log(f"Error showing loading dialog: {e}")
        run_on_ui_thread(show)

    def _cancel_check(self, dialog):
        self._cancel_scan = True
        try:
            if dialog:
                dialog.dismiss()
        except Exception:
            pass
        self._loading_dialog = None
        self._loading_builder = None
        BulletinHelper.show_info(L.g("scan_cancelled"))

    def _set_progress(self, percent, message):
        self._progress = max(0, min(int(percent), 100))
        def update():
            try:
                if self._loading_dialog:
                    try:
                        self._loading_dialog.set_progress(self._progress)
                    except Exception:
                        pass
                    try:
                        self._loading_dialog.set_message(message)
                    except Exception:
                        try:
                            self._loading_builder.set_message(message)
                        except Exception:
                            pass
                    try:
                        tv = getattr(self._loading_dialog, "messageTextView", None)
                        if tv is None and hasattr(self._loading_dialog, "getMessageTextView"):
                            tv = self._loading_dialog.getMessageTextView()
                        if tv is not None:
                            tv.setText(message)
                    except Exception:
                        pass
            except Exception:
                pass
        run_on_ui_thread(update)

    def _dismiss_loading_dialog(self):
        def _dismiss():
            try:
                if self._loading_dialog:
                    self._loading_dialog.dismiss()
                self._loading_dialog = None
                self._loading_builder = None
            except Exception:
                pass
        run_on_ui_thread(_dismiss)

    def create_settings(self):
        try:
            items = self._inner_create_settings()
            if hasattr(self, "_channel_helper"):
                items = self._channel_helper.prepend_settings(items)
            return items
        except Exception as e:
            self.log(f"proxy_manager create_settings error: {e}")
            return [
                Header(text=L.g("title")),
                Text(
                    text=L.g("settings_err"),
                    subtext=str(e),
                    icon="msg_info",
                ),
            ]

    INTERVAL_CHOICES = [
        (60, "1 час"),
        (120, "2 часа"),
        (360, "6 часов"),
        (720, "12 часов"),
        (1440, "1 день"),
        (4320, "3 дня"),
        (7200, "5 дней"),
        (10080, "7 дней"),
    ]

    COOLDOWN_CHOICES = [
        (300, "5 минут"),
        (900, "15 минут"),
        (1800, "30 минут"),
        (3600, "1 час"),
    ]

    MAX_COUNT_CHOICES = [10, 20, 30, 50, 100, 150, 200, 300]

    STRATEGY_CHOICES = [
        (0, "Наименьший пинг"),
        (1, "Случайный рабочий"),
        (2, "По порядку в списке"),
    ]

    LIMIT_POLICY_CHOICES = [
        (0, "Заменять нерабочие"),
        (1, "Заменять старые новыми"),
    ]

    MEDIA_STALL_CHOICES = [
        (3, "3 сек (Очень быстро)"),
        (5, "5 сек (Быстро)"),
        (8, "8 сек (По умолчанию)"),
        (12, "12 сек (Умеренно)"),
        (15, "15 сек (Медленно)"),
        (20, "20 сек (Для медленной сети)"),
    ]

    NO_CONN_DELAY_CHOICES = [
        (2, "2 сек (Быстро)"),
        (3, "3 сек (По умолчанию)"),
        (5, "5 сек"),
        (8, "8 сек"),
        (12, "12 сек"),
        (15, "15 сек"),
    ]

    def _format_interval(self, mins):
        try:
            mins = int(mins)
        except Exception:
            mins = 360
        for val, label in self.INTERVAL_CHOICES:
            if val == mins:
                return label
        if mins < 60:
            return f"{mins} мин"
        elif mins < 1440:
            return f"{mins // 60} ч"
        else:
            return f"{mins // 1440} дн"

    def _show_interval_picker(self, setting_key, title, *args):
        view = args[0] if args else None
        act, frag = _get_context_and_fragment(view)
        if not act:
            act = _get_act()
        if not act:
            return
        cur_val = self.get_setting(setting_key, 360)
        options = [c[1] for c in self.INTERVAL_CHOICES]

        b = AlertDialogBuilder(act)
        b.set_title(title)

        plugin = self
        def on_choice(d, which):
            d.dismiss()
            if 0 <= which < len(plugin.INTERVAL_CHOICES):
                val = plugin.INTERVAL_CHOICES[which][0]
                plugin.set_setting(setting_key, val)
                try:
                    from client_utils import get_last_fragment
                    f = get_last_fragment()
                    if f and hasattr(f, "rebuildUI"):
                        f.rebuildUI()
                except Exception:
                    pass
                run_on_ui_thread(lambda: BulletinHelper.show_info(f"{title}: {plugin.INTERVAL_CHOICES[which][1]}"))

        b.set_items(options, on_choice)
        b.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
        b.show()

    def _show_cooldown_picker(self, *args):
        view = args[0] if args else None
        act, frag = _get_context_and_fragment(view)
        if not act:
            act = _get_act()
        if not act:
            return
        options = [c[1] for c in self.COOLDOWN_CHOICES]

        b = AlertDialogBuilder(act)
        b.set_title(L.g("proxy_cooldown_duration_title"))

        plugin = self
        def on_choice(d, which):
            d.dismiss()
            if 0 <= which < len(plugin.COOLDOWN_CHOICES):
                val = plugin.COOLDOWN_CHOICES[which][0]
                plugin.set_setting("proxy_cooldown_duration", val)
                try:
                    from client_utils import get_last_fragment
                    f = get_last_fragment()
                    if f and hasattr(f, "rebuildUI"):
                        f.rebuildUI()
                except Exception:
                    pass
                run_on_ui_thread(lambda: BulletinHelper.show_info(f"{L.g('proxy_cooldown_duration_title')}: {plugin.COOLDOWN_CHOICES[which][1]}"))

        b.set_items(options, on_choice)
        b.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
        b.show()

    def _get_no_conn_delay_label(self):
        val = int(self.get_setting("auto_enable_no_conn_delay", 3))
        for sec, label in self.NO_CONN_DELAY_CHOICES:
            if sec == val:
                return label
        return f"{val} сек"

    def _show_no_conn_delay_picker(self, *args):
        view = args[0] if args else None
        act, frag = _get_context_and_fragment(view)
        if not act:
            act = _get_act()
        if not act:
            return
        options = [label for _, label in self.NO_CONN_DELAY_CHOICES]
        b = AlertDialogBuilder(act)
        b.set_title(L.g("auto_enable_no_conn_delay_title"))
        plugin = self
        def on_choice(d, which):
            d.dismiss()
            if 0 <= which < len(plugin.NO_CONN_DELAY_CHOICES):
                val, label = plugin.NO_CONN_DELAY_CHOICES[which]
                plugin.set_setting("auto_enable_no_conn_delay", val)
                try:
                    from client_utils import get_last_fragment
                    f = get_last_fragment()
                    if f and hasattr(f, "rebuildUI"):
                        f.rebuildUI()
                except Exception:
                    pass
                run_on_ui_thread(lambda: BulletinHelper.show_info(f"{L.g('auto_enable_no_conn_delay_title')}: {label}"))
        b.set_items(options, on_choice)
        b.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
        b.show()

    def _get_media_stall_timeout_label(self):
        val = int(self.get_setting("media_stall_timeout_seconds", 8))
        for sec, label in self.MEDIA_STALL_CHOICES:
            if sec == val:
                return label
        return f"{val} сек"

    def _show_media_stall_timeout_picker(self, *args):
        view = args[0] if args else None
        act, frag = _get_context_and_fragment(view)
        if not act:
            act = _get_act()
        if not act:
            return
        options = [label for _, label in self.MEDIA_STALL_CHOICES]
        b = AlertDialogBuilder(act)
        b.set_title(L.g("media_stall_timeout_title"))
        plugin = self
        def on_choice(d, which):
            d.dismiss()
            if 0 <= which < len(plugin.MEDIA_STALL_CHOICES):
                val = plugin.MEDIA_STALL_CHOICES[which][0]
                plugin.set_setting("media_stall_timeout_seconds", val)
                try:
                    from client_utils import get_last_fragment
                    f = get_last_fragment()
                    if f and hasattr(f, "rebuildUI"):
                        f.rebuildUI()
                except Exception:
                    pass
                run_on_ui_thread(lambda: BulletinHelper.show_info(f"{L.g('media_stall_timeout_title')}: {plugin.MEDIA_STALL_CHOICES[which][1]}"))

        b.set_items(options, on_choice)
        b.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
        b.show()

    def _show_strategy_picker(self, *args):
        view = args[0] if args else None
        act, frag = _get_context_and_fragment(view)
        if not act:
            act = _get_act()
        if not act:
            return
        options = [
            L.g("strategy_lowest_ping"),
            L.g("strategy_random"),
            L.g("strategy_sequential"),
        ]

        b = AlertDialogBuilder(act)
        b.set_title(L.g("auto_switch_strategy_title"))

        plugin = self
        def on_choice(d, which):
            d.dismiss()
            if 0 <= which < len(options):
                plugin.set_setting("auto_switch_strategy", which)
                try:
                    from client_utils import get_last_fragment
                    f = get_last_fragment()
                    if f and hasattr(f, "rebuildUI"):
                        f.rebuildUI()
                except Exception:
                    pass
                run_on_ui_thread(lambda: BulletinHelper.show_info(f"{L.g('auto_switch_strategy_title')}: {options[which]}"))

        b.set_items(options, on_choice)
        b.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
        b.show()

    def _show_max_count_picker(self, *args):
        view = args[0] if args else None
        act, frag = _get_context_and_fragment(view)
        if not act:
            act = _get_act()
        if not act:
            return

        options = [f"{c} прокси" for c in self.MAX_COUNT_CHOICES]
        options.append(L.g("custom_number"))

        b = AlertDialogBuilder(act)
        b.set_title(L.g("proxy_max_count_title"))

        plugin = self
        def on_choice(d, which):
            d.dismiss()
            if which < len(plugin.MAX_COUNT_CHOICES):
                val = plugin.MAX_COUNT_CHOICES[which]
                plugin.set_setting("proxy_max_count", val)
                plugin._enforce_proxy_limit()
                try:
                    from client_utils import get_last_fragment
                    f = get_last_fragment()
                    if f and hasattr(f, "rebuildUI"):
                        f.rebuildUI()
                except Exception:
                    pass
                run_on_ui_thread(lambda: BulletinHelper.show_info(f"{L.g('proxy_max_count_title')}: {val}"))
            else:
                plugin._show_custom_max_count_input(act)

        b.set_items(options, on_choice)
        b.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
        b.show()

    def _show_custom_max_count_input(self, act):
        from android.widget import EditText as AndroidEditText, LinearLayout
        from org.telegram.ui.ActionBar import Theme

        lay = LinearLayout(act)
        lay.setOrientation(LinearLayout.VERTICAL)
        lay.setPadding(AndroidUtilities.dp(24), AndroidUtilities.dp(10), AndroidUtilities.dp(24), AndroidUtilities.dp(10))

        field = AndroidEditText(act)
        field.setHint("30")
        field.setInputType(2)
        field.setText(str(self.get_setting("proxy_max_count", 30)))
        try:
            field.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
            field.setHintTextColor(Theme.getColor(Theme.key_dialogTextHint))
        except Exception:
            pass
        lay.addView(field)

        b = AlertDialogBuilder(act)
        b.set_title(L.g("enter_number"))
        b.set_view(lay)

        plugin = self
        def on_ok(d, w):
            d.dismiss()
            try:
                num = int(str(field.getText().toString()).strip())
                if num > 0:
                    plugin.set_setting("proxy_max_count", num)
                    plugin._enforce_proxy_limit()
                    try:
                        from client_utils import get_last_fragment
                        f = get_last_fragment()
                        if f and hasattr(f, "rebuildUI"):
                            f.rebuildUI()
                    except Exception:
                        pass
                    run_on_ui_thread(lambda: BulletinHelper.show_info(f"{L.g('proxy_max_count_title')}: {num}"))
            except Exception as e:
                plugin.log(f"custom max count error: {e}")

        b.set_positive_button(L.g("save"), on_ok)
        b.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
        b.show()

    def _show_limit_policy_picker(self, *args):
        view = args[0] if args else None
        act, frag = _get_context_and_fragment(view)
        if not act:
            act = _get_act()
        if not act:
            return
        options = [
            L.g("policy_replace_dead"),
            L.g("policy_replace_oldest"),
        ]

        b = AlertDialogBuilder(act)
        b.set_title(L.g("proxy_limit_policy_title"))

        plugin = self
        def on_choice(d, which):
            d.dismiss()
            if 0 <= which < len(options):
                plugin.set_setting("proxy_limit_policy", which)
                plugin._enforce_proxy_limit()
                try:
                    from client_utils import get_last_fragment
                    f = get_last_fragment()
                    if f and hasattr(f, "rebuildUI"):
                        f.rebuildUI()
                except Exception:
                    pass
                run_on_ui_thread(lambda: BulletinHelper.show_info(f"{L.g('proxy_limit_policy_title')}: {options[which]}"))

        b.set_items(options, on_choice)
        b.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
        b.show()

    def _enforce_proxy_limit(self, recently_added_keys=None, **kwargs):
        if not bool(self.get_setting("proxy_limit_enabled", False)):
            return 0
        try:
            max_count = int(self.get_setting("proxy_max_count", 50))
            if not isinstance(recently_added_keys, (list, set, tuple)):
                recently_added_keys = self._get_recently_added_keys()
            prot_json = json.dumps(list(recently_added_keys) if recently_added_keys else [])
            if dex.loaded:
                removed = dex.call("pyEnforceProxyLimit", max_count, 0, prot_json)
                if removed and int(removed) > 0:
                    self.log(f"Enforced proxy limit: removed {removed} proxies (max: {max_count})")
                    return int(removed)
        except Exception as e:
            self.log(f"enforce_proxy_limit error: {e}")
        return 0

    def _get_proxy_key(self, proxy_obj):
        if not proxy_obj:
            return ""
        if isinstance(proxy_obj, str):
            return proxy_obj.strip().lower()
        if isinstance(proxy_obj, dict):
            s = proxy_obj.get("server") or proxy_obj.get("address") or proxy_obj.get("ip") or ""
            p = proxy_obj.get("port", 443)
            return f"{str(s).lower()}:{p}" if s else ""
        try:
            s = getattr(proxy_obj, "settings", proxy_obj)
            addr = getattr(s, "getAddress", lambda: "")()
            port = getattr(s, "getPort", lambda: 443)()
            if addr:
                return f"{str(addr).lower()}:{port}"
        except Exception:
            pass
        return ""

    def _get_current_active_proxy_key(self):
        try:
            from com.exteragram.messenger.proxy import ProxyController
            pc = ProxyController.getInstance()
            cur = pc.getCurrentProxy()
            if cur:
                return self._get_proxy_key(cur)
        except Exception:
            pass
        return ""

    def _add_proxy_cooldown(self, proxy_obj, duration=None):
        if not bool(self.get_setting("proxy_cooldown_enabled", True)):
            return
        if duration is None:
            duration = int(self.get_setting("proxy_cooldown_duration", 900))
        key = self._get_proxy_key(proxy_obj)
        if not key:
            return
        if not hasattr(self, "_cooldown_dict") or not isinstance(self._cooldown_dict, dict):
            self._cooldown_dict = {}
        now = time.time()
        self._cooldown_dict[key] = now + duration
        self.log(f"Proxy added to cooldown ({duration}s): {key}")

    def _is_proxy_in_cooldown(self, proxy_obj):
        if not bool(self.get_setting("proxy_cooldown_enabled", True)):
            return False
        if not hasattr(self, "_cooldown_dict") or not isinstance(self._cooldown_dict, dict):
            return False
        key = self._get_proxy_key(proxy_obj)
        if not key or key not in self._cooldown_dict:
            return False
        now = time.time()
        if now > self._cooldown_dict[key]:
            del self._cooldown_dict[key]
            return False
        return True

    def _get_active_cooldown_set(self):
        if not bool(self.get_setting("proxy_cooldown_enabled", True)):
            return []
        if not hasattr(self, "_cooldown_dict") or not isinstance(self._cooldown_dict, dict):
            return []
        now = time.time()
        expired = [k for k, v in self._cooldown_dict.items() if now > v]
        for k in expired:
            del self._cooldown_dict[k]
        return list(self._cooldown_dict.keys())

    def _get_smart_stats(self):
        try:
            stats = self.get_setting("smart_proxy_stats", {})
            if not isinstance(stats, dict):
                stats = {}
            if "protocols" not in stats: stats["protocols"] = {}
            if "ports" not in stats: stats["ports"] = {}
            if "subnets" not in stats: stats["subnets"] = {}
            if "proxies" not in stats: stats["proxies"] = {}
            return stats
        except Exception:
            return {"protocols": {}, "ports": {}, "subnets": {}, "proxies": {}}

    def _extract_proxy_features(self, proxy_obj):
        if not proxy_obj:
            return None
        proto = "MTProto"
        server = ""
        port = 443
        secret = ""

        if isinstance(proxy_obj, dict):
            proto = proxy_obj.get("type") or "MTProto"
            server = proxy_obj.get("server") or proxy_obj.get("address") or proxy_obj.get("ip") or ""
            port = proxy_obj.get("port", 443)
            secret = proxy_obj.get("secret") or ""
        elif isinstance(proxy_obj, str):
            s = proxy_obj.strip()
            if "://" in s or "tg://proxy" in s or "t.me/proxy" in s:
                p_dict = self._parse_proxy_link(s)
                if p_dict:
                    return self._extract_proxy_features(p_dict)
            elif ":" in s:
                parts = s.split(":")
                server = parts[0]
                try: port = int(parts[1])
                except: port = 443
            else:
                server = s
        else:
            try:
                ps = getattr(proxy_obj, "settings", proxy_obj)
                server = getattr(ps, "getAddress", lambda: "")() or ""
                port = getattr(ps, "getPort", lambda: 443)() or 443
                secret = getattr(ps, "getSecret", lambda: "")() or ""
                pt = getattr(ps, "getType", lambda: None)()
                if pt and "SOCKS" in str(pt).upper():
                    proto = "SOCKS5"
                elif pt and "HTTP" in str(pt).upper():
                    proto = "HTTP"
                else:
                    proto = "MTProto"
            except Exception:
                pass

        server = str(server).strip().lower()
        try:
            port = int(port)
        except Exception:
            port = 443
        port_str = str(port)

        if "socks" in str(proto).lower():
            proto_key = "SOCKS5"
        elif "http" in str(proto).lower():
            proto_key = "HTTP"
        else:
            proto_key = "MTProto"

        subnet = ""
        if server:
            ip_parts = server.split(".")
            if len(ip_parts) == 4 and all(p.isdigit() for p in ip_parts):
                subnet = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}"
            else:
                domain_parts = server.split(".")
                if len(domain_parts) >= 2:
                    subnet = ".".join(domain_parts[-2:])
                else:
                    subnet = server

        key = f"{server}:{port}" if server else ""
        return {
            "proto": proto_key,
            "port": port_str,
            "subnet": subnet,
            "key": key,
            "server": server,
            "secret": str(secret or "")
        }

    def _record_proxy_stat(self, proxy_obj, success=True):
        if not bool(self.get_setting("smart_analysis_enabled", True)):
            return
        features = self._extract_proxy_features(proxy_obj)
        if not features or not features.get("key"):
            return
        try:
            stats = self._get_smart_stats()
            tag = "s" if success else "f"

            def inc(category, k):
                if not k: return
                k = str(k)
                if k not in stats[category]:
                    stats[category][k] = {"s": 0, "f": 0}
                entry = stats[category][k]
                entry[tag] = entry.get(tag, 0) + 1
                if entry[tag] > 100:
                    entry["s"] = int(entry["s"] * 0.8)
                    entry["f"] = int(entry["f"] * 0.8)

            inc("protocols", features["proto"])
            inc("ports", features["port"])
            inc("subnets", features["subnet"])
            inc("proxies", features["key"])

            self.set_setting("smart_proxy_stats", stats)
        except Exception as e:
            self.log(f"record_proxy_stat error: {e}")

    def _calculate_smart_score(self, proxy_obj, stats=None):
        if not bool(self.get_setting("smart_analysis_enabled", True)):
            return 1.0
        features = self._extract_proxy_features(proxy_obj)
        if not features:
            return 1.0
        if stats is None:
            stats = self._get_smart_stats()

        def get_multiplier(category, k, weight_range=1.0, base=0.5):
            if not k: return 1.0
            data = stats.get(category, {}).get(str(k))
            if not data: return 1.0
            s = data.get("s", 0)
            f = data.get("f", 0)
            if s + f == 0: return 1.0
            ratio = float(s + 1.0) / float(s + f + 2.0)
            return base + (ratio * weight_range)

        w_proto = get_multiplier("protocols", features["proto"], weight_range=1.0, base=0.5)
        w_port = get_multiplier("ports", features["port"], weight_range=1.2, base=0.4)
        w_sub = get_multiplier("subnets", features["subnet"], weight_range=0.8, base=0.6)

        w_direct = 1.0
        proxy_data = stats.get("proxies", {}).get(features["key"])
        if proxy_data:
            ps = proxy_data.get("s", 0)
            pf = proxy_data.get("f", 0)
            if ps > pf:
                w_direct = 1.25
            elif pf > ps:
                w_direct = 0.6

        if features["proto"] == "MTProto" and features["port"] == "443":
            sec = features.get("secret", "").lower()
            if sec.startswith("ee") or len(sec) > 32:
                w_port *= 1.15

        score = w_proto * w_port * w_sub * w_direct
        score = max(0.1, min(3.5, score))
        return round(score, 2)

    def _get_smart_scores_dict(self):
        scores = {}
        try:
            from com.exteragram.messenger.proxy import ProxyController
            pc = ProxyController.getInstance()
            saved = pc.getProxyList()
            if saved and saved.size() > 0:
                stats = self._get_smart_stats()
                for pi in _iter_java_list(saved):
                    ps = getattr(pi, "settings", None)
                    if ps:
                        addr = str(ps.getAddress() or "").strip().lower()
                        port = ps.getPort()
                        if addr:
                            k1 = f"{addr}:{port}"
                            sc = self._calculate_smart_score(pi, stats=stats)
                            scores[k1] = sc
                            scores[addr] = sc
        except Exception as e:
            self.log(f"get_smart_scores_dict error: {e}")
        return scores

    def _get_smart_stats_summary(self):
        try:
            stats = self._get_smart_stats()
            ports_data = stats.get("ports", {})
            best_port = None
            best_port_ratio = -1
            for p, d in ports_data.items():
                s, f = d.get("s", 0), d.get("f", 0)
                if s > 0:
                    r = s / (s + f)
                    if r > best_port_ratio or (r == best_port_ratio and s > ports_data.get(str(best_port), {}).get("s", 0)):
                        best_port = p
                        best_port_ratio = r

            proto_data = stats.get("protocols", {})
            total_s = sum(d.get("s", 0) for d in proto_data.values())
            total_f = sum(d.get("f", 0) for d in proto_data.values())

            if total_s + total_f == 0:
                return L.g("smart_stats_empty")

            parts = []
            if total_s + total_f > 0:
                pct = int((total_s / (total_s + total_f)) * 100)
                parts.append(f"Успех: {pct}% ({total_s})")
            if best_port:
                parts.append(f"Топ-порт: {best_port}")
            sub_count = len([k for k, v in stats.get("subnets", {}).items() if v.get("s", 0) > 0])
            if sub_count > 0:
                parts.append(f"Подсетей: {sub_count}")

            return " | ".join(parts) if parts else L.g("smart_stats_empty")
        except Exception:
            return ""

    def _show_smart_stats_dialog(self, *args):
        try:
            from client_utils import get_last_fragment
            frag = get_last_fragment()
            act = frag.getParentActivity() if frag else None
            if not act:
                return

            stats = self._get_smart_stats()
            proto_data = stats.get("protocols", {})
            ports_data = stats.get("ports", {})
            subnets_data = stats.get("subnets", {})

            lines = []
            lines.append("Протоколы:")
            if proto_data:
                for pr, d in proto_data.items():
                    s, f = d.get("s", 0), d.get("f", 0)
                    tot = s + f
                    pct = int((s / tot) * 100) if tot > 0 else 0
                    lines.append(f" • {pr}: {pct}% ({s} раб. / {f} сбоев)")
            else:
                lines.append(" • Нет данных")

            lines.append("\nПорты:")
            sorted_ports = sorted(ports_data.items(), key=lambda x: (x[1].get("s", 0), -x[1].get("f", 0)), reverse=True)[:5]
            if sorted_ports:
                for pt, d in sorted_ports:
                    s, f = d.get("s", 0), d.get("f", 0)
                    tot = s + f
                    pct = int((s / tot) * 100) if tot > 0 else 0
                    lines.append(f" • Порт {pt}: {pct}% ({s} раб. / {f} сбоев)")
            else:
                lines.append(" • Нет данных")

            lines.append("\nЛучшие подсети:")
            sorted_subs = sorted(subnets_data.items(), key=lambda x: x[1].get("s", 0), reverse=True)[:5]
            good_subs = [x for x in sorted_subs if x[1].get("s", 0) > 0]
            if good_subs:
                for sb, d in good_subs:
                    s, f = d.get("s", 0), d.get("f", 0)
                    lines.append(f" • {sb}: {s} раб. / {f} сбоев")
            else:
                lines.append(" • Нет данных")

            b = AlertDialogBuilder(act)
            b.set_title(L.g("smart_stats_dialog_title"))
            b.set_message("\n".join(lines))

            plugin = self
            def on_reset(d, w):
                d.dismiss()
                plugin.set_setting("smart_proxy_stats", {})
                run_on_ui_thread(lambda: BulletinHelper.show_info(L.g("smart_stats_reset_done")))
                try:
                    if frag and hasattr(frag, "rebuildUI"):
                        frag.rebuildUI()
                except Exception:
                    pass

            b.set_neutral_button(L.g("smart_stats_reset"), on_reset)
            b.set_positive_button(L.g("close"), lambda d, w: d.dismiss())
            b.show()
        except Exception as e:
            self.log(f"show_smart_stats_dialog error: {e}")

    def _record_added_proxies(self, proxies_list):
        if not proxies_list:
            return
        try:
            history = self.get_setting("added_proxies_history", {})
            if not isinstance(history, dict):
                history = {}
            now = int(time.time())
            history = {k: v for k, v in history.items() if now - v < 86400}
            for p in proxies_list:
                if not p:
                    continue
                server = p.get("server") or p.get("address") or p.get("ip") or ""
                port = p.get("port", 443)
                if server:
                    key = f"{str(server).lower()}:{port}"
                    history[key] = now
                    history[str(server).lower()] = now
            self.set_setting("added_proxies_history", history)
        except Exception as e:
            self.log(f"record_added_proxies error: {e}")

    def _get_recently_added_keys(self):
        try:
            history = self.get_setting("added_proxies_history", {})
            if not isinstance(history, dict):
                return []
            now = int(time.time())
            return [k for k, v in history.items() if now - v < 7200]
        except Exception:
            return []

    def _start_periodic_worker(self):
        if getattr(self, "_periodic_worker_started", False):
            return
        self._periodic_worker_started = True

        plugin = self
        def periodic_loop():
            while getattr(plugin, "_periodic_worker_started", False):
                try:
                    time.sleep(30)
                    now = int(time.time())

                    # Periodic auto-add
                    if plugin.get_setting("auto_add_enabled", False) and plugin.get_setting("auto_add_periodic_enabled", False):
                        interval = int(plugin.get_setting("auto_add_interval_minutes", 360)) * 60
                        last_add = int(plugin.get_setting("last_periodic_add_time", 0))
                        if now - last_add >= interval:
                            plugin.set_setting("last_periodic_add_time", now)
                            plugin._trigger_background_auto_scan(auto_add_on_finish=True)

                    # Periodic auto-clean
                    if plugin.get_setting("auto_clean_enabled", False) and plugin.get_setting("auto_clean_periodic_enabled", False):
                        interval = int(plugin.get_setting("auto_clean_interval_minutes", 360)) * 60
                        last_clean = int(plugin.get_setting("last_periodic_clean_time", 0))
                        if now - last_clean >= interval:
                            plugin.set_setting("last_periodic_clean_time", now)
                            plugin._trigger_background_auto_clean()

                except Exception as e:
                    plugin.log(f"Periodic worker error: {e}")

        threading.Thread(target=periodic_loop, daemon=True, name="ProxyManagerPeriodic").start()

    def _should_notify(self, specific_key):
        mode = int(self.get_setting("notification_mode", 1))
        if mode == 2: # Silent
            return False
        if mode == 0: # All
            return True
        # mode == 1: Important only (media stall, proxy fail, switch errors)
        return specific_key in ("notify_on_switch", "notify_switch_stall")
    def _inner_create_settings(self):
        if not hasattr(self, "_last_results") or self._last_results is None:
            self._last_results = []
            self._load_saved_results()
        if not hasattr(self, "_is_scanning"):
            self._is_scanning = False

        plugin = self

        def make_master_toggle(key, extra_action=None):
            def on_toggle(val):
                plugin.set_setting(key, bool(val))
                if extra_action:
                    try:
                        extra_action(val)
                    except Exception:
                        pass
                try:
                    from client_utils import get_last_fragment
                    f = get_last_fragment()
                    if f and hasattr(f, "rebuildUI"):
                        f.rebuildUI()
                except Exception:
                    pass
            return on_toggle

        items = []

        # 1. Параметры поиска
        items.append(Header(text=L.g("header_search_params")))
        items.append(
            Text(
                text=L.g("manage_channels_title"),
                icon="msg_channel",
                on_click=lambda *args: self._on_manage_channels_click(*args),
                create_sub_fragment=lambda: self._create_channels_sub_fragment(),
            )
        )
        cur_depth = self._get_depth_days()
        depth_sub = L.g("depth_1") if cur_depth == 1 else (L.g("depth_3") if cur_depth == 3 else L.g("depth_7"))
        items.append(
            Text(
                text=L.g("search_depth"),
                subtext=f"По умолчанию для новых каналов ({depth_sub})",
                icon="msg_calendar2",
                on_click=lambda *args: self._on_depth_click(*args),
            )
        )
        # Белый список стран
        items.append(
            Text(
                text=L.g("country_whitelist_title"),
                subtext=self._get_whitelist_summary(),
                on_click=self._show_whitelist_picker,
            )
        )
        # Фильтр только TLS
        items.append(
            Switch(
                key="only_tls_enabled",
                text=L.g("only_tls_title"),
                default=False,
                subtext=L.g("only_tls_sub"),
            )
        )

        items.append(Divider())

        # 2. Пуск
        items.append(Header(text=L.g("header_start")))
        items.append(
            Text(
                text=L.g("start_scan"),
                subtext=L.g("start_scan_sub"),
                icon="msg_search",
                accent=True,
                on_click=lambda *args: self._on_start_scan_clicked(*args),
            )
        )
        items.append(
            Text(
                text=L.g("clean_dead_btn"),
                subtext=L.g("clean_dead_btn_sub"),
                icon="msg_clear",
                on_click=lambda *args: self._on_clean_click(*args),
            )
        )

        items.append(Divider())

        # 3. Авто-добавление
        auto_add_active = bool(self.get_setting("auto_add_enabled", False))
        items.append(Header(text=L.g("header_auto_add")))
        items.append(
            Switch(
                key="auto_add_enabled",
                text=L.g("auto_add_master"),
                default=False,
                subtext=L.g("auto_add_master_sub"),
                icon="msg_download",
                on_change=make_master_toggle("auto_add_enabled"),
            )
        )
        if auto_add_active:
            items.append(
                Text(
                    text=L.g("triggers_title"),
                    subtext=self._get_auto_add_triggers_summary(),
                    on_click=self._show_auto_add_triggers_picker,
                )
            )
            if self.get_setting("auto_add_periodic_enabled", False):
                add_mins = self.get_setting("auto_add_interval_minutes", 360)
                add_interval_sub = self._format_interval(add_mins)
                items.append(
                    Text(
                        text=L.g("auto_add_interval_title"),
                        subtext=add_interval_sub,
                        on_click=lambda *args: self._show_interval_picker("auto_add_interval_minutes", L.g("auto_add_interval_title"), *args),
                    )
                )



        items.append(Divider())

        # 4. Авто-удаление
        auto_clean_active = bool(self.get_setting("auto_clean_enabled", False))
        items.append(Header(text=L.g("header_auto_clean")))
        items.append(
            Switch(
                key="auto_clean_enabled",
                text=L.g("auto_clean_master"),
                default=False,
                subtext=L.g("auto_clean_master_sub"),
                icon="msg_clear",
                on_change=make_master_toggle("auto_clean_enabled"),
            )
        )
        if auto_clean_active:
            items.append(
                Switch(
                    key="safe_clean_enabled",
                    text=L.g("safe_clean_title"),
                    default=True,
                    subtext=L.g("safe_clean_sub"),
                )
            )
            items.append(
                Text(
                    text=L.g("triggers_title"),
                    subtext=self._get_auto_clean_triggers_summary(),
                    on_click=self._show_auto_clean_triggers_picker,
                )
            )
            if self.get_setting("auto_clean_periodic_enabled", False):
                clean_mins = self.get_setting("auto_clean_interval_minutes", 360)
                clean_interval_sub = self._format_interval(clean_mins)
                items.append(
                    Text(
                        text=L.g("auto_clean_interval_title"),
                        subtext=clean_interval_sub,
                        on_click=lambda *args: self._show_interval_picker("auto_clean_interval_minutes", L.g("auto_clean_interval_title"), *args),
                    )
                )

        items.append(Divider())

        # 5. Лимит количества прокси
        limit_active = bool(self.get_setting("proxy_limit_enabled", False))
        def on_limit_master_change(val):
            if val:
                threading.Thread(target=self._enforce_proxy_limit, daemon=True).start()

        items.append(Header(text=L.g("header_proxy_limit")))
        items.append(
            Switch(
                key="proxy_limit_enabled",
                text=L.g("proxy_limit_title"),
                default=False,
                subtext=L.g("proxy_limit_sub"),
                icon="msg_list",
                on_change=make_master_toggle("proxy_limit_enabled", on_limit_master_change),
            )
        )
        if limit_active:
            max_cnt = int(self.get_setting("proxy_max_count", 50))

            alt_seekbar_view = None
            try:
                from com.exteragram.messenger.preferences.components import AltSeekbar
                class _LimitDrag(dynamic_proxy(AltSeekbar.OnDrag)):
                    def run(self, val):
                        v = int(val)
                        if v < 5: v = 5
                        if v > 300: v = 300
                        plugin.set_setting("proxy_max_count", v)

                from client_utils import get_last_fragment
                f = get_last_fragment()
                act = f.getParentActivity() if f else None
                if act:
                    alt_seekbar_view = AltSeekbar(
                        act,
                        _LimitDrag(),
                        5,
                        300,
                        L.g("proxy_max_count_title"),
                        "5",
                        "300",
                    )
                    alt_seekbar_view.setProgress(float(max_cnt))
            except Exception:
                alt_seekbar_view = None

            if alt_seekbar_view is not None:
                items.append(Custom(view=alt_seekbar_view))
            else:
                items.append(
                    Text(
                        text=L.g("proxy_max_count_title"),
                        subtext=f"{max_cnt} прокси",
                        on_click=lambda *args: self._show_max_count_picker(*args),
                    )
                )

        items.append(Divider())

        # 5. Авто-переключение
        auto_switch_active = bool(self.get_setting("auto_switch_enabled", True))

        def on_auto_switch_master_change(val):
            if dex.loaded:
                dex.call("pySetAutoConnect", bool(val) and bool(plugin.get_setting("auto_connect_on_enable", True)))

        def on_auto_conn_sub_change(val):
            if dex.loaded:
                dex.call("pySetAutoConnect", bool(val) and bool(plugin.get_setting("auto_switch_enabled", True)))

        items.append(Header(text=L.g("header_auto_switch")))
        items.append(
            Switch(
                key="auto_switch_enabled",
                text=L.g("auto_switch_master"),
                default=True,
                subtext=L.g("auto_switch_master_sub"),
                icon="msg_autodownload",
                on_change=make_master_toggle("auto_switch_enabled", on_auto_switch_master_change),
            )
        )
        if auto_switch_active:
            items.append(
                Switch(
                    key="auto_connect_on_enable",
                    text=L.g("auto_connect_enable"),
                    default=True,
                    subtext=L.g("auto_connect_enable_sub"),
                    on_change=on_auto_conn_sub_change,
                )
            )
            no_conn_active = bool(self.get_setting("auto_enable_no_conn", True))
            items.append(
                Switch(
                    key="auto_enable_no_conn",
                    text=L.g("auto_enable_no_conn_title"),
                    default=True,
                    subtext=L.g("auto_enable_no_conn_sub"),
                    on_change=make_master_toggle("auto_enable_no_conn"),
                )
            )
            if no_conn_active:
                items.append(
                    Text(
                        text=L.g("auto_enable_no_conn_delay_title"),
                        subtext=self._get_no_conn_delay_label(),
                        on_click=lambda *args: self._show_no_conn_delay_picker(*args),
                    )
                )
            media_switch_active = bool(self.get_setting("auto_switch_media", True))
            items.append(
                Switch(
                    key="auto_switch_media",
                    text=L.g("auto_switch_media_title"),
                    default=True,
                    subtext=L.g("auto_switch_media_sub"),
                    on_change=make_master_toggle("auto_switch_media"),
                )
            )
            if media_switch_active:
                items.append(
                    Text(
                        text=L.g("media_stall_timeout_title"),
                        subtext=self._get_media_stall_timeout_label(),
                        on_click=lambda *args: self._show_media_stall_timeout_picker(*args),
                    )
                )

            # Стратегия переключения
            cur_strat = int(self.get_setting("auto_switch_strategy", 0))
            strat_label = L.g("strategy_lowest_ping") if cur_strat == 0 else (L.g("strategy_random") if cur_strat == 1 else L.g("strategy_sequential"))
            items.append(
                Text(
                    text=L.g("auto_switch_strategy_title"),
                    subtext=strat_label,
                    on_click=lambda *args: self._show_strategy_picker(*args),
                )
            )

            # Кулдаун сбойных прокси
            cooldown_active = bool(self.get_setting("proxy_cooldown_enabled", True))
            items.append(
                Switch(
                    key="proxy_cooldown_enabled",
                    text=L.g("proxy_cooldown_title"),
                    default=True,
                    subtext=L.g("proxy_cooldown_sub"),
                    on_change=make_master_toggle("proxy_cooldown_enabled"),
                )
            )
            if cooldown_active:
                cur_cd = int(self.get_setting("proxy_cooldown_duration", 900))
                cd_label = "15 минут"
                for sec, lab in self.COOLDOWN_CHOICES:
                    if sec == cur_cd:
                        cd_label = lab
                        break
                items.append(
                    Text(
                        text=L.g("proxy_cooldown_duration_title"),
                        subtext=cd_label,
                        on_click=lambda *args: self._show_cooldown_picker(*args),
                    )
                )

            # Умный анализ прокси
            items.append(
                Switch(
                    key="smart_analysis_enabled",
                    text=L.g("smart_analysis_title"),
                    default=True,
                    subtext=L.g("smart_analysis_sub"),
                )
            )

        items.append(Divider())

        # 7. Уведомления
        items.append(Header(text=L.g("header_notifications")))
        items.append(
            Text(
                text=L.g("notification_mode_title"),
                subtext=self._get_notification_mode_label(),
                icon="msg_notifications",
                on_click=self._show_notification_mode_picker,
            )
        )

        items.append(Divider())

        # 7. Прокси
        items.append(Header(text=L.g("header_proxies")))
        items.append(
            Text(
                text=L.g("import_title"),
                subtext=L.g("import_sub"),
                icon="msg_download",
                on_click=lambda *args: self._on_import_click(*args),
            )
        )
        items.append(
            Text(
                text=L.g("export_title"),
                subtext=L.g("export_sub"),
                icon="msg_share",
                on_click=lambda *args: self._on_export_click(*args),
            )
        )
        items.append(
            Text(
                text=L.g("open_tg_proxy"),
                subtext=L.g("open_tg_proxy_sub"),
                icon="msg_settings",
                on_click=lambda *args: self._open_telegram_proxy_settings(*args),
            )
        )
        items.append(
            Text(
                text=L.g("results_title", len(self._last_results)),
                subtext=L.g("results_sub"),
                icon="msg_link2",
                on_click=lambda *args: self._open_cached_results_sheet(*args),
            )
        )

        # 8. Полезные каналы
        try:
            act, _ = _get_context_and_fragment()
            if not act:
                act = _get_act()
            if act:
                sponsors_view = self._build_sponsors_cards_view(act)
                if sponsors_view:
                    items.append(Divider())
                    items.append(Header(text=L.g("sponsors_header")))
                    items.append(Custom(view=sponsors_view))
        except Exception as e:
            self.log(f"sponsors card error: {e}")

        return items

    def _get_sponsors_list(self):
        if hasattr(self, "_cached_sponsors") and self._cached_sponsors:
            return self._cached_sponsors
        raw = self.get_setting("cached_sponsors", None)
        if raw:
            try:
                data = _safe_json_loads(raw)
                if isinstance(data, list) and data:
                    self._cached_sponsors = data
                    return data
            except Exception:
                pass
        return [
            {
                "title": {"ru": "Тест 1", "en": "Test 1"},
                "description": {"ru": "Крутой первый", "en": "Cool first"},
                "icon": "punipuni_ayu/1",
                "url": "https://t.me/MaxExteraPlugins",
            },
            {
                "title": {"ru": "Тест 2", "en": "Test 2"},
                "description": {"ru": "Крутой второй", "en": "Cool second"},
                "icon": "punipuni_ayu/2",
                "url": "https://t.me/itNotMax",
            },
        ]

    def _fetch_sponsors_remote(self):
        urls = [
            "https://raw.githubusercontent.com/MaxExteraPlugins/proxy_manager/main/proxy_sponsors.json",
            "https://github.com/MaxExteraPlugins/proxy_manager/raw/main/proxy_sponsors.json",
        ]
        for url in urls:
            try:
                text = _fetch_url_text(url)
                if text:
                    data = _safe_json_loads(text)
                    if data and "channels" in data:
                        chs = data.get("channels", [])
                        if chs and isinstance(chs, list):
                            self._cached_sponsors = chs
                            self.set_setting("cached_sponsors", json.dumps(chs))
                            return chs
            except Exception as e:
                self.log(f"fetch_sponsors_remote error for {url}: {e}")
        return None

    def _build_sponsors_cards_view(self, act):
        try:
            from android.widget import HorizontalScrollView, LinearLayout
            from android.view import Gravity
            from org.telegram.messenger import AndroidUtilities

            scroll = HorizontalScrollView(act)
            scroll.setHorizontalScrollBarEnabled(False)
            scroll.setClipToPadding(False)

            row = LinearLayout(act)
            row.setOrientation(LinearLayout.HORIZONTAL)
            row.setGravity(Gravity.CENTER_VERTICAL)
            dp = AndroidUtilities.dp
            row.setPadding(dp(16), dp(8), dp(16), dp(8))
            scroll.addView(row, LinearLayout.LayoutParams(-2, -2))

            sponsors = self._get_sponsors_list()
            self._populate_sponsor_cards(row, sponsors, act)

            def _refresh():
                fresh = self._fetch_sponsors_remote()
                if fresh:
                    run_on_ui_thread(lambda: self._populate_sponsor_cards(row, fresh, act))

            threading.Thread(target=_refresh, daemon=True).start()
            return scroll
        except Exception as e:
            self.log(f"build_sponsors_cards_view error: {e}")
            return None

    def _populate_sponsor_cards(self, container, sponsors, act):
        if not container or not act or not sponsors:
            return
        try:
            container.removeAllViews()
            from android.widget import LinearLayout, TextView, ImageView
            from android.view import Gravity, View
            from android.util import TypedValue
            from android.graphics.drawable import GradientDrawable
            from org.telegram.messenger import AndroidUtilities, R
            from org.telegram.ui.ActionBar import Theme
            from org.telegram.ui.Components import BackupImageView, LayoutHelper

            dp = AndroidUtilities.dp

            for item in sponsors:
                if not isinstance(item, dict):
                    continue
                title_obj = item.get("title", {})
                if isinstance(title_obj, dict):
                    title_text = title_obj.get("ru" if L.is_ru() else "en") or title_obj.get("en") or title_obj.get("ru") or ""
                else:
                    title_text = str(title_obj or "")

                desc_obj = item.get("description", {})
                if isinstance(desc_obj, dict):
                    desc_text = desc_obj.get("ru" if L.is_ru() else "en") or desc_obj.get("en") or desc_obj.get("ru") or ""
                else:
                    desc_text = str(desc_obj or "")

                icon_str = str(item.get("icon") or "")
                url = str(item.get("url") or "")

                card = LinearLayout(act)
                card.setOrientation(LinearLayout.VERTICAL)
                card.setGravity(Gravity.CENTER_HORIZONTAL)
                card.setPadding(dp(16), dp(14), dp(16), dp(12))

                try:
                    bg = GradientDrawable()
                    bg.setColor(Theme.getColor(Theme.key_windowBackgroundWhite))
                    bg.setCornerRadius(float(dp(18)))
                    bg.setStroke(dp(1), Theme.getColor(Theme.key_divider))
                    card.setBackground(bg)
                except Exception:
                    pass

                avatar = BackupImageView(act)
                avatar.setRoundRadius(dp(22))
                avatar.setAspectFit(True)
                card.addView(avatar, LayoutHelper.createLinear(130, 130, Gravity.CENTER_HORIZONTAL, 0, 0, 0, 8))
                _load_sponsor_icon(avatar, icon_str, title_text, url)

                name = TextView(act)
                name.setText(title_text)
                name.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 15)
                name.setGravity(Gravity.CENTER)
                name.setSingleLine(True)
                try:
                    name.setTextColor(Theme.getColor(Theme.key_windowBackgroundWhiteBlackText))
                    name.setTypeface(AndroidUtilities.getTypeface(AndroidUtilities.TYPEFACE_ROBOTO_MEDIUM))
                except Exception:
                    pass
                card.addView(name, LayoutHelper.createLinear(-1, -2, Gravity.CENTER_HORIZONTAL, 0, 0, 0, 4))

                d = TextView(act)
                d.setText(desc_text)
                d.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 13)
                d.setGravity(Gravity.CENTER)
                try:
                    d.setTextColor(Theme.getColor(Theme.key_windowBackgroundWhiteGrayText))
                except Exception:
                    pass
                card.addView(d, LayoutHelper.createLinear(-1, -2, Gravity.CENTER_HORIZONTAL, 0, 0, 0, 6))

                try:
                    sep = View(act)
                    sep.setBackgroundColor(Theme.getColor(Theme.key_divider))
                    sep_lp = LinearLayout.LayoutParams(-1, dp(1))
                    sep_lp.setMargins(dp(16), dp(4), dp(16), dp(4))
                    sep_wrap = LinearLayout(act)
                    sep_wrap.addView(sep, sep_lp)
                    card.addView(sep_wrap)
                except Exception:
                    pass

                try:
                    btn_row = LinearLayout(act)
                    btn_row.setOrientation(LinearLayout.HORIZONTAL)
                    btn_row.setGravity(Gravity.CENTER)
                    btn_row.setPadding(dp(8), dp(6), dp(8), dp(6))
                    btn_row.setClickable(True)
                    try:
                        selector = Theme.createSelectorDrawable(Theme.getColor(Theme.key_listSelector), 2)
                        btn_row.setBackground(selector)
                    except Exception:
                        pass

                    icon_v = ImageView(act)
                    icon_v.setScaleType(ImageView.ScaleType.CENTER_INSIDE)
                    try:
                        res_id = getattr(R.drawable, "msg_channel")
                        from androidx.core.content import ContextCompat
                        drawable = ContextCompat.getDrawable(act, res_id)
                        if drawable is not None:
                            drawable.setTint(Theme.getColor(Theme.key_windowBackgroundWhiteGrayIcon))
                            icon_v.setImageDrawable(drawable)
                    except Exception:
                        pass
                    btn_row.addView(icon_v, LayoutHelper.createLinear(20, 20, Gravity.CENTER_VERTICAL, 0, 0, 3, 0))

                    tv = TextView(act)
                    tv.setText(L.g("open_channel"))
                    tv.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 13)
                    tv.setMaxLines(2)
                    tv.setHorizontallyScrolling(False)
                    tv.setGravity(Gravity.CENTER)
                    try:
                        tv.setTextColor(Theme.getColor(Theme.key_windowBackgroundWhiteBlueText))
                    except Exception:
                        pass
                    btn_row.addView(tv, LinearLayout.LayoutParams(-2, -2))

                    def _open(u=url):
                        if u:
                            try:
                                from org.telegram.messenger.browser import Browser
                                Browser.openUrl(act, u)
                            except Exception:
                                pass

                    _set_click_listener(btn_row, _open)
                    _set_click_listener(card, _open)
                    card.addView(btn_row, LayoutHelper.createLinear(-1, -2))
                except Exception:
                    pass

                lp = LinearLayout.LayoutParams(dp(260), -2)
                lp.rightMargin = dp(12)
                container.addView(card, lp)

            container.requestLayout()
        except Exception as e:
            self.log(f"populate_sponsor_cards error: {e}")

    def _open_cached_results_sheet(self, *args):
        try:
            from android.widget import Toast
            view = args[0] if args else None
            act, f = _get_context_and_fragment(view)
            if not act:
                act = _get_act()
            if not act:
                return
            results = self._last_results or self._load_saved_results()
            if not results:
                Toast.makeText(act, L.g("no_cached_results"), Toast.LENGTH_SHORT).show()
                return
            self._show_results_bottom_sheet(
                results, len(self._get_channels_config()), len(results)
            )
        except Exception as e:
            self.log(f"open_cached_results error: {e}")

    def _on_depth_click(self, *args):
        try:
            view = args[0] if args else None
            act, _ = _get_context_and_fragment(view)
            if not act:
                act = _get_act()
            if not act:
                return
            builder = AlertDialogBuilder(act)
            builder.set_title(L.g("search_depth"))
            builder.set_message(L.g("search_depth_sub"))
            opts = [L.g("depth_1"), L.g("depth_3"), L.g("depth_7")]
            cur_depth = self._get_depth_days()
            cur_idx = 0 if cur_depth == 1 else (1 if cur_depth == 3 else 2)

            display_opts = []
            for i, opt in enumerate(opts):
                if i == cur_idx:
                    display_opts.append(f"{opt}  ✓")
                else:
                    display_opts.append(opt)

            def on_selected(d, which):
                d.dismiss()
                self.set_setting("search_depth", which)
                try:
                    from client_utils import get_last_fragment
                    f = get_last_fragment()
                    if f and hasattr(f, "rebuildUI"):
                        f.rebuildUI()
                except Exception:
                    pass

            builder.set_items(display_opts, on_selected)
            builder.set_negative_button(L.g("close"), lambda d, w: d.dismiss())
            builder.show()
        except Exception as e:
            self.log(f"on_depth_click error: {e}")

    def _open_telegram_proxy_settings(self, *args):
        try:
            view = args[0] if args else None
            act, f = _get_context_and_fragment(view)
            if f is not None:
                from org.telegram.ui import ProxyListActivity
                f.presentFragment(ProxyListActivity())
        except Exception as e:
            self.log(f"open_tg_proxy error: {e}")

    def _on_start_scan_clicked(self, *args):
        if self._is_scanning:
            BulletinHelper.show_info(L.g("scan_in_progress"))
            return

        channels = self._get_channels_config()
        if not channels:
            BulletinHelper.show_error(L.g("no_channels"))
            return

        self._is_scanning = True
        self._cancel_scan = False
        self._progress = 5

        view = args[0] if args else None
        act, frag = _get_context_and_fragment(view)
        if act is not None:
            self._show_loading_dialog(act)

        threading.Thread(
            target=self._run_scan_worker,
            args=(channels, self._get_depth_days()),
            daemon=True,
        ).start()

    def _perform_scan(self, channels, depth_days, max_batches=12, progress_cb=None):
        account = int(UserConfig.selectedAccount)
        now = int(time.time())
        cutoff_timestamp = now - depth_days * 86400
        mc = MessagesController.getInstance(account)

        all_candidates = {}
        checked_msgs_total = 0

        for ch_idx, ch_entry in enumerate(channels):
            if self._cancel_scan:
                break

            ch_name = ch_entry.get("chat", "")
            topic_id = ch_entry.get("topic_id")
            display_name = ch_entry.get("display", ch_name)

            ch_flag = ch_entry.get("flag", 1)
            try:
                ch_flag = int(ch_flag)
            except Exception:
                ch_flag = 1

            if ch_flag == 0:
                channel_cutoff = 0
                channel_max_batches = 25
            elif ch_flag == 101:
                channel_cutoff = now - 1 * 86400
                channel_max_batches = max_batches
            elif ch_flag == 103:
                channel_cutoff = now - 3 * 86400
                channel_max_batches = max_batches
            elif ch_flag == 107:
                channel_cutoff = now - 7 * 86400
                channel_max_batches = max_batches
            else:
                channel_cutoff = now - depth_days * 86400
                channel_max_batches = max_batches

            if progress_cb:
                progress_cb(ch_idx + 1, len(channels), display_name, checked_msgs_total, len(all_candidates))

            try:
                peer = None
                chat = None
                try:
                    did = int(ch_name)
                    peer = mc.getInputPeer(jlong(did))
                except Exception:
                    peer = None

                clean_ch = ch_name.lstrip("@").strip()
                if peer is None:
                    req = TLRPC.TL_contacts_resolveUsername()
                    req.username = clean_ch
                    resp, err = _send_sync_request(req, timeout=10.0)
                    if (
                        resp is not None
                        and getattr(resp, "chats", None)
                        and resp.chats.size() > 0
                    ):
                        chat = resp.chats.get(0)
                        mc.putChats(resp.chats, False)
                        peer = mc.getInputPeer(chat)

                if peer is None:
                    continue

                found_single = False
                if topic_id is not None and int(topic_id) > 0:
                    tid = int(topic_id)
                    try:
                        input_chan = None
                        if hasattr(peer, "channel_id") and getattr(peer, "channel_id", 0) != 0:
                            input_chan = TLRPC.TL_inputChannel()
                            input_chan.channel_id = peer.channel_id
                            input_chan.access_hash = getattr(peer, "access_hash", 0)
                        elif chat is not None and hasattr(mc, "getInputChannel"):
                            input_chan = mc.getInputChannel(chat)

                        if input_chan is not None:
                            ch_req = TLRPC.TL_channels_getMessages()
                            ch_req.channel = input_chan
                            from java.lang import Integer
                            ch_req.id = ArrayList()
                            ch_req.id.add(Integer.valueOf(tid))
                            m_resp, m_err = _send_sync_request(ch_req, timeout=10.0)
                            if m_resp is not None and getattr(m_resp, "messages", None):
                                for sm in _iter_java_list(m_resp.messages):
                                    if getattr(sm, "id", None) is not None:
                                        proxies = _extract_proxy_candidates_from_message(sm)
                                        msg_link = _build_msg_link(ch_name, topic_id, getattr(sm, "id", tid))
                                        for p in proxies:
                                            p["source"] = display_name
                                            if msg_link:
                                                p["msg_link"] = msg_link
                                            key = (
                                                p["type"],
                                                p["server"].lower(),
                                                p["port"],
                                                p["secret"],
                                            )
                                            if key not in all_candidates:
                                                all_candidates[key] = p
                                        if proxies:
                                            found_single = True
                    except Exception as ex:
                        self.log(f"Fetch via TL_channels_getMessages error: {ex}")

                    if not found_single:
                        try:
                            single_req = TLRPC.TL_messages_getHistory()
                            single_req.peer = peer
                            single_req.offset_id = tid
                            single_req.offset_date = 0
                            single_req.add_offset = -5
                            single_req.limit = 10
                            single_req.max_id = 0
                            single_req.min_id = 0
                            single_req.hash = 0
                            s_resp, s_err = _send_sync_request(single_req, timeout=10.0)
                            if s_resp is not None and getattr(s_resp, "messages", None):
                                for sm in _iter_java_list(s_resp.messages):
                                    sm_id = getattr(sm, "id", None)
                                    if sm_id == tid or (sm_id is not None and abs(sm_id - tid) <= 1):
                                        proxies = _extract_proxy_candidates_from_message(sm)
                                        msg_link = _build_msg_link(ch_name, topic_id, sm_id)
                                        for p in proxies:
                                            p["source"] = display_name
                                            if msg_link:
                                                p["msg_link"] = msg_link
                                            key = (
                                                p["type"],
                                                p["server"].lower(),
                                                p["port"],
                                                p["secret"],
                                            )
                                            if key not in all_candidates:
                                                all_candidates[key] = p
                                        if proxies:
                                            found_single = True
                        except Exception as ex:
                            self.log(f"Fetch single post around {topic_id} error: {ex}")

                offset_id = 0
                stop_channel = False
                for batch_idx in range(channel_max_batches):
                    if stop_channel or self._cancel_scan:
                        break

                    h_resp = None
                    h_err = None
                    if topic_id is not None and int(topic_id) > 0:
                        hist_req = TLRPC.TL_messages_getReplies()
                        hist_req.peer = peer
                        hist_req.msg_id = int(topic_id)
                        hist_req.offset_id = offset_id
                        hist_req.offset_date = 0
                        hist_req.add_offset = 0
                        hist_req.limit = 50
                        hist_req.max_id = 0
                        hist_req.min_id = 0
                        hist_req.hash = 0
                        h_resp, h_err = _send_sync_request(hist_req, timeout=10.0)

                    if h_err is not None or h_resp is None:
                        if topic_id is not None and int(topic_id) > 0:
                            if ch_flag == 0 or found_single or batch_idx > 0:
                                break
                        hist_req = TLRPC.TL_messages_getHistory()
                        hist_req.peer = peer
                        hist_req.offset_id = offset_id
                        hist_req.offset_date = 0
                        hist_req.add_offset = 0
                        hist_req.limit = 50
                        hist_req.max_id = 0
                        hist_req.min_id = 0
                        hist_req.hash = 0
                        h_resp, h_err = _send_sync_request(hist_req, timeout=12.0)

                    if h_err is not None or h_resp is None:
                        break

                    msgs = getattr(h_resp, "messages", None)
                    if not msgs or msgs.size() == 0:
                        break

                    checked_msgs_total += msgs.size()
                    for m in _iter_java_list(msgs):
                        m_date = getattr(m, "date", 0)
                        if batch_idx > 0 and channel_cutoff > 0 and m_date < channel_cutoff:
                            stop_channel = True
                            break

                        proxies = _extract_proxy_candidates_from_message(m)
                        m_id = getattr(m, "id", None)
                        msg_link = _build_msg_link(ch_name, topic_id, m_id)

                        for p in proxies:
                            p["source"] = display_name
                            if msg_link:
                                p["msg_link"] = msg_link
                            key = (
                                p["type"],
                                p["server"].lower(),
                                p["port"],
                                p["secret"],
                            )
                            if key not in all_candidates:
                                all_candidates[key] = p

                        offset_id = getattr(m, "id", offset_id)

                    if progress_cb:
                        progress_cb(ch_idx + 1, len(channels), display_name, checked_msgs_total, len(all_candidates))

            except Exception as e:
                self.log(f"Scan error for channel {ch_name}: {e}")

        candidates_list = list(all_candidates.values())
        self._last_candidates_count = len(candidates_list)
        if not candidates_list or self._cancel_scan:
            return []

        if bool(self.get_setting("only_tls_enabled", False)):
            filtered = []
            for c in candidates_list:
                p_type = c.get("type", "MTProto")
                port = c.get("port", 443)
                sec = str(c.get("secret", "")).lower()
                is_tls = sec.startswith("ee") or len(sec) > 32
                if "socks" in str(p_type).lower() or port != 443 or not is_tls:
                    continue
                filtered.append(c)
            candidates_list = filtered
            self._last_candidates_count = len(candidates_list)
            if not candidates_list or self._cancel_scan:
                return []

        if progress_cb:
            progress_cb(-1, len(channels), "", checked_msgs_total, len(candidates_list))

        alive_proxies = _batch_check_candidates(candidates_list, timeout_ms=3500)
        alive_proxies.sort(key=lambda x: x.get("ping", 999999))
        _enrich_proxies_with_country(alive_proxies)
        whitelist = self._get_whitelist()
        if whitelist and alive_proxies:
            wl_matched = [p for p in alive_proxies if p.get("country_code", "").upper() in whitelist]
            if wl_matched:
                alive_proxies = wl_matched
            else:
                unknowns = [p for p in alive_proxies if not p.get("country_code")]
                if unknowns:
                    alive_proxies = unknowns
        return alive_proxies

    def _run_scan_worker(self, channels, depth_days):
        try:
            total_ch = len(channels)
            def update_progress(cur, total, ch_name, msgs_cnt, found_cnt):
                if cur == -1:
                    pct = 85
                    text = L.g("scan_progress_ping", found_cnt)
                else:
                    pct = 5 + int(((cur - 1) / max(1, total)) * 75)
                    text = L.g("scan_progress_msg", cur, total, ch_name, msgs_cnt, found_cnt)
                self._set_progress(pct, text)

            first_name = channels[0].get("display", channels[0].get("chat", ""))
            self._set_progress(5, L.g("scan_progress_msg", 1, total_ch, first_name, 0, 0))

            # Start dead proxy cleanup immediately in parallel with scanning
            if self.get_setting("auto_clean_enabled", False) and self.get_setting("auto_clean_after_scan", False):
                self._trigger_background_auto_clean()

            alive_proxies = self._perform_scan(channels, depth_days, progress_cb=update_progress)

            self._set_progress(100, L.g("scan_dialog_title"))
            time.sleep(0.3)
            self._dismiss_loading_dialog()

            if self._cancel_scan:
                return

            if not alive_proxies:
                cand_cnt = getattr(self, "_last_candidates_count", 0)
                err_msg = L.g("no_alive_proxies", cand_cnt) if cand_cnt > 0 else L.g("no_proxies_found")
                run_on_ui_thread(
                    lambda: BulletinHelper.show_error(err_msg)
                )
                return

            self._save_results(alive_proxies)

            auto_add = (self.get_setting("auto_add_enabled", False) and self.get_setting("auto_add_after_scan", False)) or self.get_setting("auto_add_proxies", False)
            if auto_add:
                links = [p.get("link") or p.get("url") for p in alive_proxies if (p.get("link") or p.get("url"))]
                if links and dex.loaded:
                    dex.call("pyAddAllProxies", json.dumps(links))
                    self._record_added_proxies(alive_proxies)
                    self._enforce_proxy_limit()

            run_on_ui_thread(
                lambda: self._show_results_bottom_sheet(
                    alive_proxies, len(channels), len(alive_proxies)
                )
            )

        except Exception as e:
            self.log(f"General scan worker error: {e}")
            self._dismiss_loading_dialog()
        finally:
            self._is_scanning = False

    def _handle_proxy_toggled_on(self, settings):
        needs_working = False
        if settings is None:
            needs_working = True
        else:
            try:
                if not settings.isValid():
                    needs_working = True
                else:
                    addr = str(settings.getAddress())
                    port = int(settings.getPort())
                    stype = "MTProto" if settings.getType() and "MTPROTO" in str(settings.getType()).upper() else "SOCKS5"
                    ping = -1
                    if dex.loaded:
                        ping = dex.call("pyCheckProxy", stype, addr, port, 700)
                    if ping < 0:
                        needs_working = True
            except Exception:
                needs_working = True

        if not needs_working:
            return

        threading.Thread(target=self._auto_connect_worker, daemon=True).start()

    def _auto_enable_on_no_connection_worker(self):
        if getattr(self, "_is_quick_searching", False) or getattr(self, "_is_auto_connecting", False):
            return
        try:
            from org.telegram.messenger import SharedConfig
            if SharedConfig.isProxyEnabled():
                return

            # 1. Try saved proxies in Telegram first (fast TCP ping)
            if dex.loaded and dex.call("pyTryEnableAliveSavedProxy", 1500):
                run_on_ui_thread(lambda: BulletinHelper.show_info(L.g("auto_enable_no_conn_triggered")))
                self._on_proxy_switched()
                return

            # 2. Try cached results from previous scans
            cached = getattr(self, "_last_results", None) or []
            if cached:
                alive_cached = _batch_check_candidates(cached[:40], timeout_ms=1500)
                if alive_cached:
                    alive_cached.sort(key=lambda x: x.get("ping", 999999))
                    best = alive_cached[0]
                    link = best.get("link") or best.get("url", "")
                    if link and _py_apply_proxy(link):
                        run_on_ui_thread(lambda: BulletinHelper.show_info(L.g("auto_enable_no_conn_triggered")))
                        self._on_proxy_switched()
                        return

            # 3. Fallback: trigger quick search (which also handles offline/disabled proxy)
            run_on_ui_thread(lambda: BulletinHelper.show_info(L.g("auto_enable_no_conn_triggered")))
            self._quick_search_and_connect()
        except Exception as e:
            self.log(f"auto_enable_on_no_connection error: {e}")

    def _quick_fetch_channel_candidates(self, ch_item, limit=25, max_batches=1, only_tls=False):
        from org.telegram.messenger import MessagesController, UserConfig
        from org.telegram.tgnet import TLRPC
        from java.util import ArrayList

        mc = MessagesController.getInstance(UserConfig.selectedAccount)
        ch_name = ch_item.get("chat", "") if isinstance(ch_item, dict) else str(ch_item)
        topic_id = ch_item.get("topic_id") if isinstance(ch_item, dict) else None
        display_name = ch_item.get("display", ch_name) if isinstance(ch_item, dict) else ch_name
        if not ch_name:
            return []

        peer = None
        chat = None
        try:
            did = int(ch_name)
            peer = mc.getInputPeer(jlong(did))
        except Exception:
            peer = None

        clean_ch = ch_name.lstrip("@").strip()
        if peer is None:
            req = TLRPC.TL_contacts_resolveUsername()
            req.username = clean_ch
            resp, err = _send_sync_request(req, timeout=6.0)
            if resp is not None and getattr(resp, "chats", None) and resp.chats.size() > 0:
                chat = resp.chats.get(0)
                mc.putChats(resp.chats, False)
                peer = mc.getInputPeer(chat)

        if peer is None:
            return []

        all_candidates = {}
        offset_id = 0
        for batch_idx in range(max_batches):
            h_resp = None
            h_err = None
            if topic_id is not None and int(topic_id) > 0:
                hist_req = TLRPC.TL_messages_getReplies()
                hist_req.peer = peer
                hist_req.msg_id = int(topic_id)
                hist_req.offset_id = offset_id
                hist_req.offset_date = 0
                hist_req.add_offset = 0
                hist_req.limit = limit
                hist_req.max_id = 0
                hist_req.min_id = 0
                hist_req.hash = 0
                h_resp, h_err = _send_sync_request(hist_req, timeout=6.0)

            if h_err is not None or h_resp is None:
                hist_req = TLRPC.TL_messages_getHistory()
                hist_req.peer = peer
                hist_req.offset_id = offset_id
                hist_req.offset_date = 0
                hist_req.add_offset = 0
                hist_req.limit = limit
                hist_req.max_id = 0
                hist_req.min_id = 0
                hist_req.hash = 0
                h_resp, h_err = _send_sync_request(hist_req, timeout=6.0)

            if h_err is not None or h_resp is None:
                break

            msgs = getattr(h_resp, "messages", None)
            if not msgs or msgs.size() == 0:
                break

            for m in _iter_java_list(msgs):
                proxies = _extract_proxy_candidates_from_message(m)
                m_id = getattr(m, "id", None)
                msg_link = _build_msg_link(ch_name, topic_id, m_id)
                for p in proxies:
                    if only_tls:
                        p_type = str(p.get("type", "MTProto")).lower()
                        port = p.get("port", 443)
                        sec = str(p.get("secret", "")).lower()
                        if "socks" in p_type or port != 443 or not (sec.startswith("ee") or len(sec) > 32):
                            continue
                    p["source"] = display_name
                    if msg_link:
                        p["msg_link"] = msg_link
                    key = (p["type"], p["server"].lower(), p["port"], p["secret"])
                    if key not in all_candidates:
                        all_candidates[key] = p
                offset_id = getattr(m, "id", offset_id)

        return list(all_candidates.values())

    def _quick_search_and_connect(self):
        if getattr(self, "_is_quick_searching", False) or getattr(self, "_is_scanning", False):
            run_on_ui_thread(lambda: BulletinHelper.show_info(L.g("quick_search_in_progress")))
            return
        self._is_quick_searching = True

        def run():
            try:
                run_on_ui_thread(lambda: BulletinHelper.show_info(L.g("quick_search_searching")))

                # If proxy is currently OFF and Telegram is stuck connecting, try an alive saved proxy first
                # so Telegram can connect and fetch fresh proxies from channels if needed
                try:
                    from org.telegram.messenger import SharedConfig
                    if not SharedConfig.isProxyEnabled() and dex.loaded:
                        conn_state = dex.call("pyGetConnectionState")
                        if conn_state != 3:
                            dex.call("pyTryEnableAliveSavedProxy", 1200)
                except Exception:
                    pass

                channels = self._get_channel_tokens()
                if not channels:
                    run_on_ui_thread(lambda: BulletinHelper.show_error(L.g("no_channels")))
                    return

                only_tls = bool(self.get_setting("only_tls_enabled", False))
                whitelist = self._get_whitelist()
                smart_enabled = bool(self.get_setting("smart_analysis_enabled", True))
                alive = []

                # Pass 1: fast streaming check (25 latest messages per channel, stop on first working batch)
                for ch_item in channels:
                    cands = self._quick_fetch_channel_candidates(ch_item, limit=25, max_batches=1, only_tls=only_tls)
                    if not cands:
                        continue
                    batch_alive = _batch_check_candidates(cands, timeout_ms=1800)
                    if batch_alive:
                        if whitelist:
                            _enrich_proxies_with_country(batch_alive)
                            wl_matched = [p for p in batch_alive if p.get("country_code", "").upper() in whitelist]
                            if wl_matched:
                                alive = wl_matched
                                break
                        else:
                            alive = batch_alive
                            break

                # Pass 2: deeper fallback (up to 3 batches x 50 msgs per channel, no date cutoff)
                if not alive:
                    for ch_item in channels:
                        cands = self._quick_fetch_channel_candidates(ch_item, limit=50, max_batches=3, only_tls=only_tls)
                        if not cands:
                            continue
                        batch_alive = _batch_check_candidates(cands, timeout_ms=2500)
                        if batch_alive:
                            if whitelist:
                                _enrich_proxies_with_country(batch_alive)
                                wl_matched = [p for p in batch_alive if p.get("country_code", "").upper() in whitelist]
                                alive = wl_matched if wl_matched else batch_alive
                            else:
                                alive = batch_alive
                            break

                # Pass 3: fallback to cached results if channels were unreachable
                if not alive and getattr(self, "_last_results", None):
                    cached_alive = _batch_check_candidates(self._last_results[:40], timeout_ms=2000)
                    if cached_alive:
                        alive = cached_alive

                if not alive:
                    run_on_ui_thread(lambda: BulletinHelper.show_error(L.g("quick_search_none_found")))
                    return

                self._last_results = alive
                self._save_results(alive)

                if smart_enabled:
                    alive.sort(key=lambda x: float(x.get("ping", 999999)) / max(0.1, self._calculate_smart_score(x)))
                else:
                    alive.sort(key=lambda x: x.get("ping", 999999))

                best = alive[0]
                self._record_proxy_stat(best, success=True)
                self._enforce_proxy_limit(to_add_count=1)

                link = best.get("link") or best.get("url", "")
                applied = _py_apply_proxy(link)
                if applied:
                    server = best.get("server", "")
                    port = best.get("port", "")
                    ping = best.get("ping", 0)
                    flag = best.get("flag") or best.get("country_flag", "")
                    label = f"{flag} {server}:{port}".strip()
                    run_on_ui_thread(
                        lambda: BulletinHelper.show_success(
                            L.g("quick_search_success", label, ping)
                        )
                    )
                    self._on_proxy_switched()
                else:
                    run_on_ui_thread(lambda: BulletinHelper.show_error(L.g("auto_connect_failed")))
            except Exception as e:
                self.log(f"quick_search error: {e}")
                run_on_ui_thread(lambda: BulletinHelper.show_error(str(e)))
            finally:
                self._is_quick_searching = False

        threading.Thread(target=run, daemon=True, name="ProxyQuickSearch").start()

    def _auto_connect_worker(self):
        if not bool(self.get_setting("auto_switch_enabled", True)) or not bool(self.get_setting("auto_connect_on_enable", True)):
            return
        if self._is_auto_connecting:
            return
        self._is_auto_connecting = True
        try:
            if self._should_notify("notify_on_switch"):
                run_on_ui_thread(lambda: BulletinHelper.show_info(L.g("auto_finding_proxy")))

            try:
                from com.exteragram.messenger.proxy import ProxyController
                pc = ProxyController.getInstance()
                saved = pc.getProxyList()
                if saved and saved.size() > 0:
                    candidates = []
                    for pi in _iter_java_list(saved):
                        s = getattr(pi, "settings", None)
                        if s and s.isValid():
                            stype = "MTProto" if s.getType() and "MTPROTO" in str(s.getType()).upper() else "SOCKS5"
                            candidates.append({
                                "type": stype,
                                "server": str(s.getAddress()),
                                "port": int(s.getPort()),
                                "secret": str(s.getSecret() or ""),
                                "url": str(s.getLink() or ""),
                            })
                    if candidates:
                        avail = [c for c in candidates if not self._is_proxy_in_cooldown(c)]
                        if not avail:
                            avail = candidates
                        alive = _batch_check_candidates(avail, timeout_ms=1200)
                        if alive:
                            for c in alive:
                                self._record_proxy_stat(c, success=True)
                            alive_set = {self._get_proxy_key(c) for c in alive}
                            for c in avail:
                                if self._get_proxy_key(c) not in alive_set:
                                    self._record_proxy_stat(c, success=False)

                            smart_enabled = bool(self.get_setting("smart_analysis_enabled", True))
                            strategy = int(self.get_setting("auto_switch_strategy", 0))
                            if strategy == 0:
                                if smart_enabled:
                                    alive.sort(key=lambda x: float(x.get("ping", 999999)) / max(0.1, self._calculate_smart_score(x)))
                                else:
                                    alive.sort(key=lambda x: x.get("ping", 999999))
                                best = alive[0]
                            elif strategy == 1:
                                import random
                                if smart_enabled and len(alive) > 1:
                                    weights = [max(0.1, self._calculate_smart_score(x)) for x in alive]
                                    best = random.choices(alive, weights=weights, k=1)[0]
                                else:
                                    best = random.choice(alive)
                            else:
                                if smart_enabled:
                                    best = max(alive, key=lambda x: self._calculate_smart_score(x))
                                else:
                                    best = alive[0]

                            cur_p = self._get_current_active_proxy_key()
                            if cur_p:
                                self._add_proxy_cooldown(cur_p)

                            if _py_apply_proxy(best["url"]):
                                if self._should_notify("notify_on_switch"):
                                    run_on_ui_thread(
                                        lambda: BulletinHelper.show_success(
                                            L.g("auto_connected_saved", best["server"], best["port"], best["ping"])
                                        )
                                    )
                                self._on_proxy_switched()
                                return
            except Exception as e:
                self.log(f"auto_connect saved check error: {e}")

            if self._last_results:
                alive = _batch_check_candidates(self._last_results, timeout_ms=1200)
                if alive:
                    alive.sort(key=lambda x: x.get("ping", 999999))
                    best = alive[0]
                    if _py_apply_proxy(best["url"]):
                        if self._should_notify("notify_on_switch"):
                            run_on_ui_thread(
                                lambda: BulletinHelper.show_success(
                                    L.g("auto_connected_saved", best["server"], best["port"], best["ping"])
                                )
                            )
                        self._on_proxy_switched()
                        return

            channels = self._get_channel_tokens()
            if not channels:
                if self._should_notify("notify_on_switch"):
                    run_on_ui_thread(lambda: BulletinHelper.show_error(L.g("auto_connect_failed")))
                return

            alive = self._perform_scan(channels, depth_days=1, max_batches=4)
            if alive:
                alive.sort(key=lambda x: x.get("ping", 999999))
                best = alive[0]
                if _py_apply_proxy(best["url"]):
                    if self._should_notify("notify_on_switch"):
                        run_on_ui_thread(
                            lambda: BulletinHelper.show_success(
                                L.g("auto_connected_found", best["server"], best["port"], best["ping"])
                            )
                        )
                    self._on_proxy_switched()
                    return

            if self._should_notify("notify_on_switch"):
                run_on_ui_thread(lambda: BulletinHelper.show_error(L.g("auto_connect_failed")))
        finally:
            self._is_auto_connecting = False

    def _get_current_telegram_proxies(self):
        if dex.loaded:
            try:
                res_str = dex.call("pyExportProxies")
                if res_str:
                    return json.loads(str(res_str))
            except Exception as e:
                self.log(f"dex pyExportProxies error: {e}")

        try:
            from com.exteragram.messenger.proxy import ProxyController
            pc = ProxyController.getInstance()
            list_obj = pc.getProxyList()
            if not list_obj or list_obj.size() == 0:
                return []
            res = []
            for pi in _iter_java_list(list_obj):
                if pi is None:
                    continue
                s = getattr(pi, "settings", None)
                if s is None:
                    continue
                stype = "MTProto" if s.getType() and "MTPROTO" in str(s.getType()).upper() else "SOCKS5"
                link = str(s.getLink() or "")
                if not link:
                    srv = str(s.getAddress() or "")
                    prt = int(s.getPort())
                    sec = str(s.getSecret() or "")
                    if stype == "MTProto":
                        link = f"tg://proxy?server={srv}&port={prt}&secret={sec}"
                    else:
                        link = f"tg://socks?server={srv}&port={prt}"
                res.append({
                    "type": stype,
                    "server": str(s.getAddress() or ""),
                    "port": int(s.getPort()),
                    "secret": str(s.getSecret() or ""),
                    "link": link,
                })
            return res
        except Exception as e:
            self.log(f"get_current_telegram_proxies error: {e}")
            return []

    def _on_export_click(self, *args):
        proxies = self._get_current_telegram_proxies()
        if not proxies:
            BulletinHelper.show_error(L.g("no_tg_proxies"))
            return

        view = args[0] if args else None
        act, frag = _get_context_and_fragment(view)
        if act is None:
            links = [p["link"] for p in proxies if p.get("link")]
            AndroidUtilities.addToClipboard("\n".join(links))
            BulletinHelper.show_success(L.g("export_copied", len(links)))
            return

        builder = AlertDialogBuilder(act)
        builder.set_title(L.g("export_title"))
        builder.set_message(L.g("export_dialog_msg", len(proxies)))

        items = [
            L.g("export_opt_copy"),
            L.g("export_opt_txt"),
            L.g("export_opt_saved"),
            L.g("export_opt_share"),
        ]

        def on_select(d, which):
            d.dismiss()
            if which == 0:
                links = [p["link"] for p in proxies if p.get("link")]
                AndroidUtilities.addToClipboard("\n".join(links))
                BulletinHelper.show_success(L.g("export_copied", len(links)))
            elif which == 1:
                self._export_to_txt_file(proxies)
            elif which == 2:
                self._export_to_saved_messages(proxies)
            elif which == 3:
                self._export_share_intent(proxies, act)

        builder.set_items(items, on_select)
        builder.set_negative_button(L.g("close"), lambda d, w: d.dismiss())
        builder.show()

    def _export_to_txt_file(self, proxies):
        try:
            links = [p["link"] for p in proxies if p.get("link")]
            if not links:
                BulletinHelper.show_error(L.g("no_tg_proxies"))
                return

            ts = int(time.time())
            date_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
            lines = [
                f"# Proxy Manager Export",
                f"# Date: {date_str}",
                f"# Proxies count: {len(links)}",
                "",
            ]
            for p in proxies:
                stype = p.get("type", "Proxy")
                server = p.get("server", "")
                port = p.get("port", "")
                link = p.get("link", "")
                lines.append(f"# {stype} • {server}:{port}")
                lines.append(link)
                lines.append("")

            file_data = "\n".join(lines)
            file_name = f"proxies_{ts}.txt"

            saved_path = None
            try:
                from android.os import Environment
                pub_downloads = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
                if pub_downloads and pub_downloads.exists():
                    p = os.path.join(str(pub_downloads.getAbsolutePath()), file_name)
                    with open(p, "w", encoding="utf-8") as f:
                        f.write(file_data)
                    saved_path = p
            except Exception:
                saved_path = None

            if not saved_path:
                ctx = ApplicationLoader.applicationContext
                ext_dir = ctx.getExternalFilesDir(None)
                target_dir = str(ext_dir.getAbsolutePath()) if ext_dir else str(ctx.getCacheDir().getAbsolutePath())
                p = os.path.join(target_dir, file_name)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(file_data)
                saved_path = p

            run_on_ui_thread(
                lambda: BulletinHelper.show_success(L.g("file_saved_success", file_name))
            )
        except Exception as e:
            self.log(f"export_to_txt_file error: {e}")
            run_on_ui_thread(lambda: BulletinHelper.show_error(str(e)))

    def _export_to_saved_messages(self, proxies):
        try:
            links = [p["link"] for p in proxies if p.get("link")]
            if not links:
                BulletinHelper.show_error(L.g("no_tg_proxies"))
                return

            ts = int(time.time())
            file_name = f"proxies_{ts}.txt"
            ctx = ApplicationLoader.applicationContext
            cache_dir = ctx.getCacheDir().getAbsolutePath()
            fpath = os.path.join(str(cache_dir), file_name)

            date_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
            lines = [
                f"# Proxy Manager Export",
                f"# Date: {date_str}",
                f"# Proxies count: {len(links)}",
                "",
            ]
            for p in proxies:
                lines.append(p.get("link", ""))
            with open(fpath, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))

            my_id = int(UserConfig.getInstance(int(UserConfig.selectedAccount)).getClientUserId())
            send_document(my_id, fpath, caption=L.g("export_caption", len(links)))
            run_on_ui_thread(lambda: BulletinHelper.show_success(L.g("saved_msgs_success")))
        except Exception as e:
            self.log(f"export_to_saved_messages error: {e}")
            run_on_ui_thread(lambda: BulletinHelper.show_error(str(e)))

    def _export_share_intent(self, proxies, act):
        links = [p["link"] for p in proxies if p.get("link")]
        links_text = "\n".join(links)
        try:
            from android.content import Intent
            intent = Intent(Intent.ACTION_SEND)
            intent.setType("text/plain")
            intent.putExtra(Intent.EXTRA_TEXT, links_text)
            chooser = Intent.createChooser(intent, L.g("export_title"))
            act.startActivity(chooser)
        except Exception as e:
            self.log(f"Share intent error: {e}")
            AndroidUtilities.addToClipboard(links_text)
            BulletinHelper.show_success(L.g("export_copied", len(proxies)))

    def _on_clean_click(self, *args):
        proxies = self._get_current_telegram_proxies()
        if not proxies:
            BulletinHelper.show_error(L.g("no_tg_proxies"))
            return

        view = args[0] if args else None
        act, frag = _get_context_and_fragment(view)
        if act is None:
            return

        builder = AlertDialogBuilder(act)
        builder.set_title(L.g("clean_dialog_title"))

        items = [
            L.g("clean_opt_remove_dead"),
            L.g("clean_opt_keep_top"),
            "⚠️ " + L.g("clean_opt_delete_all"),
        ]

        def on_select(d, which):
            d.dismiss()
            if which == 0:
                self._start_remove_dead_proxies()
            elif which == 1:
                self._show_keep_top_dialog(act)
            elif which == 2:
                self._confirm_delete_all_proxies(len(proxies), act)

        builder.set_items(items, on_select)
        builder.set_negative_button(L.g("close"), lambda d, w: d.dismiss())
        builder.show()

    def _start_remove_dead_proxies(self):
        BulletinHelper.show_info(L.g("clean_checking"))

        def worker():
            try:
                deleted = 0
                kept = 0
                aborted = False
                reason = ""
                if dex.loaded:
                    safe = bool(self.get_setting("safe_clean_enabled", True))
                    prot_keys = self._get_recently_added_keys() if safe else []
                    res_str = dex.call("pyRemoveDeadProxies", 3500, safe, json.dumps(prot_keys))
                    if res_str:
                        data = json.loads(str(res_str))
                        deleted = data.get("deleted", 0)
                        kept = data.get("kept", 0)
                        aborted = data.get("aborted", False)
                        reason = data.get("reason", "")

                if aborted:
                    err_msg = L.g("auto_clean_aborted_mass") if (reason in ("all_failed", "mass_failure")) else L.g("clean_aborted")
                    run_on_ui_thread(
                        lambda: BulletinHelper.show_error(err_msg)
                    )
                else:
                    run_on_ui_thread(
                        lambda: BulletinHelper.show_success(
                            L.g("clean_removed_dead_result", deleted, kept)
                        )
                    )
            except Exception as e:
                self.log(f"Remove dead error: {e}")
                run_on_ui_thread(lambda: BulletinHelper.show_error(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _show_keep_top_dialog(self, act):
        if act is None:
            return
        builder = AlertDialogBuilder(act)
        builder.set_title(L.g("clean_prompt_keep_count"))
        options = ["3", "5", "10", "15", "20"]

        def on_pick(d, which):
            d.dismiss()
            count = int(options[which])
            self._start_keep_top_proxies(count)

        builder.set_items(options, on_pick)
        builder.set_negative_button(L.g("close"), lambda d, w: d.dismiss())
        builder.show()

    def _start_keep_top_proxies(self, keep_count):
        try:
            from org.telegram.messenger import SharedConfig
            if not SharedConfig.isProxyEnabled():
                BulletinHelper.show_error(L.g("clean_proxy_disabled"))
                return
        except Exception:
            pass

        BulletinHelper.show_info(L.g("clean_checking"))

        def worker():
            try:
                kept = 0
                deleted = 0
                aborted = False
                if dex.loaded:
                    res_str = dex.call("pyKeepTopProxies", keep_count, 3500)
                    if res_str:
                        data = json.loads(str(res_str))
                        deleted = data.get("deleted", 0)
                        kept = data.get("kept", 0)
                        aborted = data.get("aborted", False)

                if aborted:
                    run_on_ui_thread(
                        lambda: BulletinHelper.show_error(L.g("clean_aborted"))
                    )
                else:
                    run_on_ui_thread(
                        lambda: BulletinHelper.show_success(
                            L.g("clean_kept_top_result", kept, deleted)
                        )
                    )
            except Exception as e:
                self.log(f"Keep top error: {e}")
                run_on_ui_thread(lambda: BulletinHelper.show_error(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _confirm_delete_all_proxies(self, count, act):
        if act is None:
            return
        builder = AlertDialogBuilder(act)
        builder.set_title(L.g("clean_opt_delete_all"))

        working_count = 0
        try:
            from com.exteragram.messenger.proxy import ProxyController
            pc = ProxyController.getInstance()
            plist = pc.getProxyList()
            if plist:
                for pi in _iter_java_list(plist):
                    av = getattr(pi, "available", False)
                    avTime = getattr(pi, "availableCheckTime", 0)
                    if av and avTime > 0:
                        working_count += 1
        except Exception:
            pass

        if working_count > 0:
            msg = f"⚠️ Внимание! В Telegram сейчас есть {working_count} РАБОЧИХ прокси (всего {count}).\n\nВы действительно хотите удалить ВСЕ сохраненные прокси, включая рабочие?"
        else:
            msg = L.g("clean_confirm_delete_all", count)

        builder.set_message(msg)

        def on_confirm(d, w):
            d.dismiss()
            try:
                if dex.loaded:
                    dex.call("pyClearAllProxies")
                else:
                    from com.exteragram.messenger.proxy import ProxyController
                    pc = ProxyController.getInstance()
                    pc.clearAll()
                    pc.saveProxyList()
                    try:
                        from org.telegram.messenger import NotificationCenter
                        NotificationCenter.getGlobalInstance().postNotificationName(NotificationCenter.proxySettingsChanged)
                    except Exception:
                        pass
                BulletinHelper.show_success(L.g("clean_all_deleted"))
            except Exception as e:
                self.log(f"Clear all error: {e}")
                BulletinHelper.show_error(str(e))

        builder.set_positive_button(L.g("clean_opt_delete_all"), on_confirm)
        builder.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
        builder.show()

    COMMON_COUNTRIES = [
        ("DE", "Германия"),
        ("NL", "Нидерланды"),
        ("FI", "Финляндия"),
        ("RU", "Россия"),
        ("US", "США"),
        ("GB", "Великобритания"),
        ("FR", "Франция"),
        ("SE", "Швеция"),
        ("CH", "Швейцария"),
        ("PL", "Польша"),
        ("TR", "Турция"),
        ("KZ", "Казахстан"),
        ("UA", "Украина"),
        ("BY", "Беларусь"),
        ("AE", "ОАЭ"),
        ("SG", "Сингапур"),
        ("JP", "Япония"),
        ("KR", "Южная Корея"),
        ("CA", "Канада"),
        ("AT", "Австрия"),
        ("CZ", "Чехия"),
        ("NO", "Норвегия"),
        ("RO", "Румыния"),
    ]

    def _get_whitelist(self):
        wl = self.get_setting("country_whitelist", [])
        if not isinstance(wl, list):
            return []
        return [str(c).upper().strip() for c in wl if str(c).strip()]

    def _get_whitelist_summary(self):
        wl = self._get_whitelist()
        if not wl:
            return L.g("country_whitelist_all")
        parts = []
        for code in wl[:4]:
            flag = _get_flag_emoji(code)
            name = _get_country_name_ru(code)
            parts.append(f"{flag} {name}" if flag else name)
        if len(wl) > 4:
            parts.append(f"(+{len(wl) - 4})")
        return ", ".join(parts)

    def _create_multi_choice_dialog(self, act, title, items, checked_states, on_save_cb, on_reset_cb=None, neutral_btn=None, subtitles=None):
        try:
            from android.widget import ScrollView, LinearLayout
            from org.telegram.ui.ActionBar import Theme
            from ui.alert import AlertDialogBuilder
            from org.telegram.messenger import AndroidUtilities

            scroll = ScrollView(act)
            scroll.setVerticalScrollBarEnabled(True)

            container = LinearLayout(act)
            container.setOrientation(LinearLayout.VERTICAL)
            container.setPadding(0, AndroidUtilities.dp(8), 0, AndroidUtilities.dp(8))

            cells = []
            has_tg_cells = True
            try:
                from org.telegram.ui.Cells import CheckBoxCell
            except Exception:
                has_tg_cells = False

            if has_tg_cells:
                for i, (label, is_checked) in enumerate(zip(items, checked_states)):
                    sub = subtitles[i] if (subtitles and i < len(subtitles)) else ""
                    need_divider = (i < len(items) - 1)
                    try:
                        cell = CheckBoxCell(act, 1)
                        cell.setText(str(label), str(sub) if sub else "", bool(is_checked), need_divider)
                        cell.setChecked(bool(is_checked), False)
                        cell.setBackground(Theme.getSelectorDrawable(False))
                        try:
                            if hasattr(cell, "getCheckBoxRound") and cell.getCheckBoxRound():
                                cell.getCheckBoxRound().setColor(
                                    Theme.key_switch2TrackChecked,
                                    Theme.key_radioBackground,
                                    Theme.key_checkboxCheck
                                )
                        except Exception:
                            pass
                    except Exception:
                        cell = CheckBoxCell(act, 1, 21, True, None)
                        cell.setText(str(label), str(sub) if sub else "", bool(is_checked), need_divider)
                        cell.setChecked(bool(is_checked), False)
                        cell.setBackground(Theme.getSelectorDrawable(False))

                    def make_toggle(target_cell):
                        return lambda: target_cell.setChecked(not target_cell.isChecked(), True)

                    _set_click_listener(cell, make_toggle(cell))
                    container.addView(cell)
                    cells.append(cell)
            else:
                # Fallback to standard views if CheckBoxCell is completely unavailable
                from android.widget import TextView, CheckBox
                from android.view import Gravity
                for i, (label, is_checked) in enumerate(zip(items, checked_states)):
                    row = LinearLayout(act)
                    row.setOrientation(LinearLayout.HORIZONTAL)
                    row.setGravity(Gravity.CENTER_VERTICAL)
                    row.setPadding(AndroidUtilities.dp(16), AndroidUtilities.dp(8), AndroidUtilities.dp(16), AndroidUtilities.dp(8))
                    row.setClickable(True)

                    cb = CheckBox(act)
                    cb.setChecked(bool(is_checked))
                    cb.setClickable(False)
                    cb.setFocusable(False)

                    tv = TextView(act)
                    tv.setText(label)
                    tv.setTextSize(15)
                    tv.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
                    tv.setPadding(AndroidUtilities.dp(10), 0, 0, 0)

                    lp_tv = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1.0)
                    tv.setLayoutParams(lp_tv)

                    row.addView(tv)
                    row.addView(cb)
                    cells.append(cb)

                    def make_toggle_cb(target_cb):
                        return lambda: target_cb.setChecked(not target_cb.isChecked())

                    _set_click_listener(row, make_toggle_cb(cb))
                    container.addView(row)

            scroll.addView(container)

            builder = AlertDialogBuilder(act)
            builder.set_title(title)
            builder.set_view(scroll)

            def on_confirm(d, w):
                d.dismiss()
                final_states = [c.isChecked() for c in cells]
                on_save_cb(final_states)

            builder.set_positive_button(L.g("save"), on_confirm)
            if neutral_btn:
                n_label, n_cb = neutral_btn
                builder.set_neutral_button(n_label, lambda d, w: [d.dismiss(), n_cb()])
            elif on_reset_cb:
                builder.set_neutral_button(L.g("country_whitelist_reset"), lambda d, w: [d.dismiss(), on_reset_cb()])
            builder.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
            builder.show()
        except Exception as e:
            self.log(f"create_multi_choice_dialog error: {e}")

    def _show_whitelist_picker(self, *args):
        view = args[0] if args else None
        act, frag = _get_context_and_fragment(view)
        if not act:
            act = _get_act()
        if not act:
            return

        cur_wl = set(self._get_whitelist())
        labels = []
        checked = []
        codes = []
        for code, name in self.COMMON_COUNTRIES:
            flag = _get_flag_emoji(code)
            label = f"{flag} {name} ({code})".strip() if flag else f"{name} ({code})"
            labels.append(label)
            checked.append(code in cur_wl)
            codes.append(code)

        plugin = self
        def on_save(final_states):
            selected = [codes[i] for i in range(len(final_states)) if final_states[i]]
            plugin.set_setting("country_whitelist", selected)
            try:
                from client_utils import get_last_fragment
                f = get_last_fragment()
                if f and hasattr(f, "rebuildUI"):
                    f.rebuildUI()
            except Exception:
                pass
            run_on_ui_thread(lambda: BulletinHelper.show_info(f"{L.g('country_whitelist_title')}: {plugin._get_whitelist_summary()}"))

        def on_reset():
            plugin.set_setting("country_whitelist", [])
            try:
                from client_utils import get_last_fragment
                f = get_last_fragment()
                if f and hasattr(f, "rebuildUI"):
                    f.rebuildUI()
            except Exception:
                pass
            run_on_ui_thread(lambda: BulletinHelper.show_info(L.g("country_whitelist_all")))

        self._create_multi_choice_dialog(
            act=act,
            title=L.g("country_whitelist_dialog_title"),
            items=labels,
            checked_states=checked,
            on_save_cb=on_save,
            on_reset_cb=on_reset,
        )

    def _get_auto_add_triggers_summary(self):
        parts = []
        if self.get_setting("auto_add_after_scan", False):
            parts.append("После скана")
        if self.get_setting("auto_add_on_startup", False):
            parts.append("При запуске")
        if self.get_setting("auto_add_on_switch", False):
            parts.append("При смене")
        if self.get_setting("auto_add_periodic_enabled", False):
            inv = self._format_interval(self.get_setting("auto_add_interval_minutes", 360))
            parts.append(f"По расписанию ({inv})")
        return ", ".join(parts) if parts else "Не настроено (выключено)"

    def _show_triggers_with_interval_dialog(self, act, title, keys, labels, interval_key, interval_title):
        try:
            from android.widget import ScrollView, LinearLayout
            from org.telegram.ui.ActionBar import Theme
            from ui.alert import AlertDialogBuilder
            from org.telegram.messenger import AndroidUtilities
            from org.telegram.ui.Cells import CheckBoxCell, TextSettingsCell

            scroll = ScrollView(act)
            scroll.setVerticalScrollBarEnabled(True)

            container = LinearLayout(act)
            container.setOrientation(LinearLayout.VERTICAL)
            container.setPadding(0, AndroidUtilities.dp(8), 0, AndroidUtilities.dp(8))

            checked = [bool(self.get_setting(k, False)) for k in keys]
            cur_interval = [int(self.get_setting(interval_key, 360))]
            cells = []
            interval_cell_holder = [None]

            def update_interval_cell():
                ic = interval_cell_holder[0]
                if ic is None:
                    return
                inv_str = self._format_interval(cur_interval[0])
                try:
                    ic.setTextAndValue("Интервал расписания", inv_str, False)
                    ic.setAlpha(1.0 if checked[3] else 0.45)
                except Exception:
                    pass

            for i, label in enumerate(labels):
                try:
                    cell = CheckBoxCell(act, 1)
                    cell.setText(str(label), "", bool(checked[i]), True)
                    cell.setChecked(bool(checked[i]), False)
                    cell.setBackground(Theme.getSelectorDrawable(False))
                    try:
                        if hasattr(cell, "getCheckBoxRound") and cell.getCheckBoxRound():
                            cell.getCheckBoxRound().setColor(
                                Theme.key_switch2TrackChecked,
                                Theme.key_radioBackground,
                                Theme.key_checkboxCheck,
                            )
                    except Exception:
                        pass
                except Exception:
                    cell = CheckBoxCell(act, 1, 21, True, None)
                    cell.setText(str(label), "", bool(checked[i]), True)
                    cell.setChecked(bool(checked[i]), False)
                    cell.setBackground(Theme.getSelectorDrawable(False))

                def make_toggle(idx, target_cell):
                    def on_click():
                        new_val = not target_cell.isChecked()
                        target_cell.setChecked(new_val, True)
                        checked[idx] = new_val
                        if idx == 3:
                            update_interval_cell()
                    return on_click

                _set_click_listener(cell, make_toggle(i, cell))
                container.addView(cell)
                cells.append(cell)

            # Native Telegram TextSettingsCell for picking schedule interval directly
            ic = TextSettingsCell(act)
            ic.setBackground(Theme.getSelectorDrawable(False))
            interval_cell_holder[0] = ic
            update_interval_cell()

            plugin = self
            def on_interval_click():
                b_int = AlertDialogBuilder(act)
                b_int.set_title(interval_title)
                opts = [c[1] for c in plugin.INTERVAL_CHOICES]
                def on_pick(d2, which2):
                    d2.dismiss()
                    if 0 <= which2 < len(plugin.INTERVAL_CHOICES):
                        cur_interval[0] = plugin.INTERVAL_CHOICES[which2][0]
                        if not checked[3]:
                            checked[3] = True
                            try:
                                cells[3].setChecked(True, True)
                            except Exception:
                                pass
                        update_interval_cell()
                b_int.set_items(opts, on_pick)
                b_int.set_negative_button(L.g("cancel"), lambda d2, w2: d2.dismiss())
                b_int.show()

            _set_click_listener(ic, on_interval_click)
            container.addView(ic)

            scroll.addView(container)

            b = AlertDialogBuilder(act)
            b.set_title(title)
            b.set_view(scroll)

            def on_save(d, w):
                d.dismiss()
                for i, k in enumerate(keys):
                    plugin.set_setting(k, bool(checked[i]))
                plugin.set_setting(interval_key, int(cur_interval[0]))
                try:
                    from client_utils import get_last_fragment
                    f = get_last_fragment()
                    if f and hasattr(f, "rebuildUI"):
                        f.rebuildUI()
                except Exception:
                    pass

            b.set_positive_button(L.g("save"), on_save)
            b.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
            b.show()
        except Exception as e:
            self.log(f"triggers dialog error: {e}")

    def _show_auto_add_triggers_picker(self, *args):
        view = args[0] if args else None
        act, frag = _get_context_and_fragment(view)
        if not act:
            act = _get_act()
        if not act:
            return
        keys = ["auto_add_after_scan", "auto_add_on_startup", "auto_add_on_switch", "auto_add_periodic_enabled"]
        labels = [
            L.g("trigger_after_scan"),
            L.g("trigger_on_startup"),
            L.g("trigger_on_switch"),
            L.g("trigger_periodic"),
        ]
        self._show_triggers_with_interval_dialog(
            act, L.g("triggers_title"), keys, labels,
            "auto_add_interval_minutes", L.g("auto_add_interval_title"),
        )

    def _get_auto_clean_triggers_summary(self):
        parts = []
        if self.get_setting("auto_clean_after_scan", False):
            parts.append("После скана")
        if self.get_setting("auto_clean_on_startup", False):
            parts.append("При запуске")
        if self.get_setting("auto_clean_on_switch", False):
            parts.append("При смене")
        if self.get_setting("auto_clean_periodic_enabled", False):
            inv = self._format_interval(self.get_setting("auto_clean_interval_minutes", 360))
            parts.append(f"По расписанию ({inv})")
        return ", ".join(parts) if parts else "Не настроено (выключено)"

    def _show_auto_clean_triggers_picker(self, *args):
        view = args[0] if args else None
        act, frag = _get_context_and_fragment(view)
        if not act:
            act = _get_act()
        if not act:
            return
        keys = ["auto_clean_after_scan", "auto_clean_on_startup", "auto_clean_on_switch", "auto_clean_periodic_enabled"]
        labels = [
            L.g("trigger_after_scan"),
            L.g("trigger_on_startup"),
            L.g("trigger_on_switch"),
            L.g("trigger_periodic"),
        ]
        self._show_triggers_with_interval_dialog(
            act, L.g("triggers_title"), keys, labels,
            "auto_clean_interval_minutes", L.g("auto_clean_interval_title"),
        )

    def _get_notification_mode_label(self):
        cur_mode = int(self.get_setting("notification_mode", 1))
        if cur_mode == 0:
            return L.g("notification_mode_all")
        elif cur_mode == 2:
            return L.g("notification_mode_silent")
        else:
            return L.g("notification_mode_important")

    def _show_notification_mode_picker(self, *args):
        from client_utils import get_last_fragment
        frag = get_last_fragment()
        act = frag.getParentActivity() if frag else None
        if not act:
            return

        opts = [
            L.g("notification_mode_important"),
            L.g("notification_mode_all"),
            L.g("notification_mode_silent"),
        ]
        mode_vals = [1, 0, 2]

        builder = AlertDialogBuilder(act)
        builder.set_title(L.g("notification_mode_title"))

        plugin = self
        def on_pick(d, which):
            d.dismiss()
            if 0 <= which < len(mode_vals):
                plugin.set_setting("notification_mode", mode_vals[which])
                try:
                    if frag and hasattr(frag, "rebuildUI"):
                        frag.rebuildUI()
                except Exception:
                    pass
                run_on_ui_thread(lambda: BulletinHelper.show_info(f"{L.g('notification_mode_title')}: {opts[which]}"))

        builder.set_items(opts, on_pick)
        builder.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
        builder.show()

    def _show_add_proxies_options(self, alive, card_updaters=None):
        if not alive:
            return
        act, _ = _get_context_and_fragment()
        if not act:
            return

        options = [
            L.g("add_all_count", len(alive)),
            L.g("add_top_ping"),
            L.g("add_by_country"),
        ]

        types = sorted(list(set(p.get("type", "MTProto") for p in alive)))
        if len(types) > 1:
            for t in types:
                count_t = len([p for p in alive if p.get("type", "MTProto") == t])
                options.append(f"Только {t} ({count_t} шт.)")

        builder = AlertDialogBuilder(act)
        builder.set_title(L.g("add_options_title"))

        plugin = self
        def on_choice(d, which):
            d.dismiss()
            if which == 0:
                plugin._add_proxies_subset(alive, card_updaters)
            elif which == 1:
                plugin._show_add_by_ping_dialog(alive, card_updaters)
            elif which == 2:
                plugin._show_add_by_country_dialog(alive, card_updaters)
            else:
                chosen_opt = options[which]
                for t in types:
                    if f"Только {t}" in chosen_opt:
                        sub = [p for p in alive if p.get("type", "MTProto") == t]
                        plugin._add_proxies_subset(sub, card_updaters)
                        break

        builder.set_items(options, on_choice)
        builder.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
        builder.show()

    def _show_add_by_ping_dialog(self, alive, card_updaters=None):
        act, _ = _get_context_and_fragment()
        if not act:
            return
        sorted_alive = sorted(alive, key=lambda x: x.get("ping", 99999))
        counts = [5, 10, 20, 30, 50]
        valid_counts = [c for c in counts if c < len(sorted_alive)]
        if not valid_counts:
            valid_counts = [min(len(sorted_alive), 10)]

        options = [L.g("fastest_count", c) for c in valid_counts]
        options.append(L.g("enter_custom_count"))

        builder = AlertDialogBuilder(act)
        builder.set_title(L.g("add_by_ping_title"))

        plugin = self
        def on_pick(d, which):
            d.dismiss()
            if which < len(valid_counts):
                count = valid_counts[which]
                sub = sorted_alive[:count]
                plugin._add_proxies_subset(sub, card_updaters)
            else:
                plugin._show_custom_ping_count_input(sorted_alive, card_updaters)

        builder.set_items(options, on_pick)
        builder.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
        builder.show()

    def _show_custom_ping_count_input(self, sorted_alive, card_updaters=None):
        act, _ = _get_context_and_fragment()
        if not act:
            return
        try:
            from android.widget import EditText, LinearLayout
            from android.text import InputType

            et = EditText(act)
            et.setInputType(InputType.TYPE_CLASS_NUMBER)
            et.setHint(f"1 - {len(sorted_alive)}")

            layout = LinearLayout(act)
            layout.setOrientation(LinearLayout.VERTICAL)
            layout.setPadding(AndroidUtilities.dp(24), AndroidUtilities.dp(10), AndroidUtilities.dp(24), AndroidUtilities.dp(10))
            layout.addView(et)

            builder = AlertDialogBuilder(act)
            builder.set_title(L.g("how_many_add"))
            builder.set_view(layout)

            plugin = self
            def on_ok(d, w):
                d.dismiss()
                try:
                    val = int(str(et.getText()).strip())
                    if val > 0:
                        sub = sorted_alive[:min(val, len(sorted_alive))]
                        plugin._add_proxies_subset(sub, card_updaters)
                except Exception:
                    pass

            builder.set_positive_button(L.g("btn_apply"), on_ok)
            builder.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
            builder.show()
        except Exception as e:
            self.log(f"custom ping input error: {e}")

    def _show_add_by_country_dialog(self, alive, card_updaters=None):
        act, _ = _get_context_and_fragment()
        if not act:
            act = _get_act()
        if not act:
            return

        country_map = {}
        for p in alive:
            c_name = p.get("country") or "Unknown"
            c_code = p.get("country_code", "")
            flag = p.get("flag", "")
            if not flag and c_code:
                flag = _get_flag_emoji(c_code)
            label = f"{flag} {c_name}".strip() if flag else c_name
            if label not in country_map:
                country_map[label] = []
            country_map[label].append(p)

        sorted_countries = sorted(country_map.keys(), key=lambda k: len(country_map[k]), reverse=True)
        items = [f"{c} ({len(country_map[c])} шт.)" for c in sorted_countries]
        checked = [False] * len(sorted_countries)

        plugin = self
        def on_save(final_states):
            selected = []
            for i, c in enumerate(sorted_countries):
                if final_states[i]:
                    selected.extend(country_map[c])
            if selected:
                plugin._add_proxies_subset(selected, card_updaters)
            else:
                run_on_ui_thread(lambda: BulletinHelper.show_error(L.g("no_countries_selected")))

        self._create_multi_choice_dialog(
            act=act,
            title=L.g("select_countries_title"),
            items=items,
            checked_states=checked,
            on_save_cb=on_save,
        )

    def _add_proxies_subset(self, subset, card_updaters=None):
        if not subset:
            return
        links = [p.get("link") or p.get("url") for p in subset if (p.get("link") or p.get("url"))]
        if not links:
            return
        added = _call_dex("pyAddAllProxies", json.dumps(links))
        cnt = int(added) if added is not None else 0
        if cnt > 0:
            self._record_added_proxies(subset)
            self._enforce_proxy_limit()
            if card_updaters:
                subset_keys = {self._get_proxy_key(p) for p in subset}
                for item in card_updaters:
                    try:
                        if isinstance(item, tuple):
                            updater, card_key = item
                            if not card_key or card_key in subset_keys:
                                updater(True)
                        else:
                            item(True)
                    except Exception:
                        pass
        run_on_ui_thread(lambda: BulletinHelper.show_success(L.g("added_success", cnt)))

    def _show_results_bottom_sheet(self, alive, channels_count, candidates_count):
        act, frag = _get_context_and_fragment()
        if act is None:
            self._show_scan_results_dialog(channels_count, candidates_count, alive)
            return

        try:
            from org.telegram.ui.ActionBar import BottomSheet, Theme
            from org.telegram.messenger import R
            from org.telegram.messenger.browser import Browser
            from android.widget import ScrollView, LinearLayout, TextView, ImageView, Toast
            from android.view import Gravity, View
            from android.util import TypedValue
            from android.graphics.drawable import GradientDrawable
            from android.graphics import Color

            builder = BottomSheet.Builder(act)
            builder.setTitle(f"{L.g('scan_results_ui_title')} ({len(alive)})", True)

            scroll = ScrollView(act)
            scroll.setVerticalScrollBarEnabled(True)

            try:
                class _ScrollTouch(dynamic_proxy(View.OnTouchListener)):
                    def onTouch(self, v, ev):
                        try:
                            if v.canScrollVertically(-1) or v.canScrollVertically(1):
                                v.getParent().requestDisallowInterceptTouchEvent(True)
                        except Exception:
                            pass
                        return False
                scroll.setOnTouchListener(_ScrollTouch())
            except Exception:
                pass

            container = LinearLayout(act)
            container.setOrientation(LinearLayout.VERTICAL)
            container.setPadding(
                AndroidUtilities.dp(14),
                AndroidUtilities.dp(6),
                AndroidUtilities.dp(14),
                AndroidUtilities.dp(24),
            )

            sub_tv = TextView(act)
            sub_tv.setText(L.g("sorted_by_ping"))
            sub_tv.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 13)
            sub_tv.setTextColor(Theme.getColor(Theme.key_dialogTextGray))
            sub_tv.setPadding(0, 0, 0, AndroidUtilities.dp(10))
            container.addView(sub_tv)

            actions_row = LinearLayout(act)
            actions_row.setOrientation(LinearLayout.HORIZONTAL)
            actions_row.setPadding(0, 0, 0, AndroidUtilities.dp(12))

            card_updaters = []

            def create_button(text, bg_color, text_color, on_click_fn):
                tv = TextView(act)
                tv.setText(text)
                tv.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 13)
                tv.setTypeface(AndroidUtilities.bold())
                tv.setTextColor(text_color)
                tv.setGravity(Gravity.CENTER)
                tv.setPadding(
                    AndroidUtilities.dp(12),
                    AndroidUtilities.dp(8),
                    AndroidUtilities.dp(12),
                    AndroidUtilities.dp(8),
                )
                shape = GradientDrawable()
                shape.setCornerRadius(AndroidUtilities.dp(8))
                shape.setColor(bg_color)
                tv.setBackground(shape)
                tv.setClickable(True)

                try:
                    tv.setOnClickListener(OnClickListener(lambda v: on_click_fn(tv)))
                except Exception:
                    try:
                        class _JavaClicker(dynamic_proxy(View.OnClickListener)):
                            def onClick(self, v):
                                on_click_fn(tv)
                        tv.setOnClickListener(_JavaClicker())
                    except Exception as ex:
                        log(f"Failed to set button click listener: {ex}")
                return tv

            def create_icon_button(icon_res, bg_color, icon_color, on_click_fn):
                iv = ImageView(act)
                iv.setImageResource(icon_res)
                iv.setColorFilter(icon_color)
                iv.setScaleType(ImageView.ScaleType.CENTER_INSIDE)
                iv.setPadding(
                    AndroidUtilities.dp(6),
                    AndroidUtilities.dp(6),
                    AndroidUtilities.dp(6),
                    AndroidUtilities.dp(6),
                )
                shape = GradientDrawable()
                shape.setCornerRadius(AndroidUtilities.dp(8))
                shape.setColor(bg_color)
                iv.setBackground(shape)
                iv.setClickable(True)

                try:
                    iv.setOnClickListener(OnClickListener(lambda v: on_click_fn(iv)))
                except Exception:
                    try:
                        class _JavaClicker(dynamic_proxy(View.OnClickListener)):
                            def onClick(self, v):
                                on_click_fn(iv)
                        iv.setOnClickListener(_JavaClicker())
                    except Exception:
                        pass
                return iv

            def _open_link(link_url):
                try:
                    Browser.openUrl(act, link_url)
                except Exception:
                    _call_dex("pyOpenProxyInTelegram", act, link_url)

            def on_add_all_click(v):
                self._show_add_proxies_options(alive, card_updaters)

            btn_add_all = create_button(
                L.g("add_all_to_tg"),
                Theme.getColor(Theme.key_featuredStickers_addButton),
                Theme.getColor(Theme.key_featuredStickers_buttonText),
                on_add_all_click,
            )
            actions_row.addView(btn_add_all)

            spacer = TextView(act)
            spacer.setPadding(AndroidUtilities.dp(8), 0, 0, 0)
            actions_row.addView(spacer)

            btn_copy_all = create_button(
                L.g("copy_all_links"),
                Theme.getColor(Theme.key_dialogButtonSelector),
                Theme.getColor(Theme.key_dialogTextBlack),
                lambda v: self._copy_all_links(alive),
            )
            actions_row.addView(btn_copy_all)
            container.addView(actions_row)

            def show_proxy_menu(anchor_v, p_data, st_dict, btn_updater):
                p_url = p_data.get("link") or p_data.get("url", "")
                p_key = self._get_proxy_key(p_data)
                m_link = p_data.get("msg_link", "")

                try:
                    from org.telegram.ui.Components import ItemOptions
                    opt = ItemOptions.makeOptions(container, anchor_v)

                    def do_conn():
                        _call_dex("pyOpenProxyInTelegram", act, p_url)
                    opt.add(L.g("btn_connect"), _make_runnable(do_conn))

                    def do_toggle_add():
                        if not st_dict.get("added"):
                            ok = _call_dex("pyAddProxy", p_url)
                            if ok:
                                st_dict["added"] = True
                                btn_updater(True)
                                BulletinHelper.show_info(L.g("proxy_added_toast"))
                        else:
                            ok = _call_dex("pyRemoveProxyByLink", p_url)
                            if not ok:
                                srv = p_data.get("server", "")
                                prt = p_data.get("port", "")
                                if srv:
                                    ok = _call_dex("pyRemoveProxyByLink", f"{srv}:{prt}" if prt else srv)
                            if ok:
                                st_dict["added"] = False
                                btn_updater(False)
                                BulletinHelper.show_info(L.g("proxy_removed_toast"))
                            else:
                                BulletinHelper.show_error("Не удалось удалить прокси")
                    add_label = L.g("btn_delete") if st_dict.get("added") else L.g("btn_add")
                    opt.add(add_label, _make_runnable(do_toggle_add))

                    def do_copy():
                        AndroidUtilities.addToClipboard(p_url)
                        BulletinHelper.show_info(L.g("copied_clipboard"))
                    opt.add(L.g("btn_copy"), _make_runnable(do_copy))

                    def do_share():
                        try:
                            from android.content import Intent
                            intent = Intent(Intent.ACTION_SEND)
                            intent.setType("text/plain")
                            intent.putExtra(Intent.EXTRA_TEXT, p_url)
                            act.startActivity(Intent.createChooser(intent, "Поделиться прокси"))
                        except Exception:
                            pass
                    opt.add("Поделиться", _make_runnable(do_share))

                    def do_cooldown():
                        if p_key:
                            self._add_proxy_cooldown(p_key, duration_seconds=600)
                            BulletinHelper.show_info("Прокси добавлен в кулдаун на 10 мин.")
                    opt.add("В черный список (10 мин)", _make_runnable(do_cooldown))

                    if m_link:
                        def do_post():
                            _open_link(m_link)
                        opt.add("Открыть пост в канале", _make_runnable(do_post))

                    opt.show()
                    return
                except Exception as e:
                    self.log(f"ItemOptions menu failed: {e}")

                # Fallback to AlertDialog
                b = AlertDialogBuilder(act)
                b.set_title(p_data.get("server", "Прокси"))
                m_items = [
                    L.g("btn_connect"),
                    L.g("btn_delete") if st_dict.get("added") else L.g("btn_add"),
                    L.g("btn_copy"),
                    "Поделиться",
                    "В черный список (10 мин)",
                ]
                if m_link:
                    m_items.append("Открыть пост в канале")

                def on_choice(d, which):
                    d.dismiss()
                    if which == 0:
                        _call_dex("pyOpenProxyInTelegram", act, p_url)
                    elif which == 1:
                        if not st_dict.get("added"):
                            ok = _call_dex("pyAddProxy", p_url)
                            if ok:
                                st_dict["added"] = True
                                btn_updater(True)
                                BulletinHelper.show_info(L.g("proxy_added_toast"))
                        else:
                            ok = _call_dex("pyRemoveProxyByLink", p_url)
                            if not ok:
                                srv = p_data.get("server", "")
                                prt = p_data.get("port", "")
                                if srv:
                                    ok = _call_dex("pyRemoveProxyByLink", f"{srv}:{prt}" if prt else srv)
                            if ok:
                                st_dict["added"] = False
                                btn_updater(False)
                                BulletinHelper.show_info(L.g("proxy_removed_toast"))
                            else:
                                BulletinHelper.show_error("Не удалось удалить прокси")
                    elif which == 2:
                        AndroidUtilities.addToClipboard(p_url)
                        BulletinHelper.show_info(L.g("copied_clipboard"))
                    elif which == 3:
                        try:
                            from android.content import Intent
                            intent = Intent(Intent.ACTION_SEND)
                            intent.setType("text/plain")
                            intent.putExtra(Intent.EXTRA_TEXT, p_url)
                            act.startActivity(Intent.createChooser(intent, "Поделиться прокси"))
                        except Exception:
                            pass
                    elif which == 4:
                        if p_key:
                            self._add_proxy_cooldown(p_key, duration_seconds=600)
                            BulletinHelper.show_info("Прокси добавлен в кулдаун на 10 мин.")
                    elif which == 5 and m_link:
                        _open_link(m_link)

                b.set_items(m_items, on_choice)
                b.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
                b.show()

            def _build_proxy_card(p):
                p_type = p.get("type", "MTProto")
                server = p.get("server", "")
                port = p.get("port", "")
                ping = p.get("ping", 0)
                url = p.get("link") or p.get("url", "")
                flag = p.get("flag", "")
                country = p.get("country", "")
                country_code = p.get("country_code", "")
                source = p.get("source", "")

                card = LinearLayout(act)
                card.setOrientation(LinearLayout.VERTICAL)
                card.setPadding(
                    AndroidUtilities.dp(14),
                    AndroidUtilities.dp(12),
                    AndroidUtilities.dp(14),
                    AndroidUtilities.dp(12),
                )

                card_bg = GradientDrawable()
                card_bg.setCornerRadius(AndroidUtilities.dp(14))
                card_bg.setColor(Theme.getColor(Theme.key_windowBackgroundWhite))
                card_bg.setStroke(AndroidUtilities.dp(1), Theme.getColor(Theme.key_divider))
                card.setBackground(card_bg)

                top_row = LinearLayout(act)
                top_row.setOrientation(LinearLayout.HORIZONTAL)
                top_row.setGravity(Gravity.CENTER_VERTICAL)

                title_container = LinearLayout(act)
                title_container.setOrientation(LinearLayout.HORIZONTAL)
                title_container.setGravity(Gravity.CENTER_VERTICAL)

                c_text = f"{flag} {country}" if flag else (country or "Unknown")
                title_view = TextView(act)
                title_view.setText(c_text)
                title_view.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 14.5)
                title_view.setTypeface(AndroidUtilities.bold())
                title_view.setTextColor(Theme.getColor(Theme.key_windowBackgroundWhiteBlackText))
                title_container.addView(title_view)

                if country_code:
                    code_badge = TextView(act)
                    code_badge.setText(str(country_code))
                    code_badge.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 10.5)
                    code_badge.setTypeface(AndroidUtilities.bold())
                    code_badge.setTextColor(Theme.getColor(Theme.key_windowBackgroundWhiteGrayText))
                    code_bg = GradientDrawable()
                    code_bg.setCornerRadius(AndroidUtilities.dp(4))
                    code_bg.setColor(Color.parseColor("#14808080"))
                    code_badge.setBackground(code_bg)
                    code_badge.setPadding(AndroidUtilities.dp(4), AndroidUtilities.dp(1), AndroidUtilities.dp(4), AndroidUtilities.dp(1))
                    lp_code = LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                    lp_code.setMargins(AndroidUtilities.dp(5), 0, 0, 0)
                    title_container.addView(code_badge, lp_code)

                proto_badge = TextView(act)
                proto_badge.setText(str(p_type).upper())
                proto_badge.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 10)
                proto_badge.setTypeface(AndroidUtilities.bold())
                proto_bg = GradientDrawable()
                proto_bg.setCornerRadius(AndroidUtilities.dp(5))
                p_type_lower = str(p_type).lower()
                if "socks" in p_type_lower:
                    proto_bg.setColor(Color.parseColor("#1AFF9800"))
                    proto_badge.setTextColor(Color.parseColor("#E65100"))
                elif "tls" in p_type_lower:
                    proto_bg.setColor(Color.parseColor("#1A9C27B0"))
                    proto_badge.setTextColor(Color.parseColor("#7B1FA2"))
                else:
                    proto_bg.setColor(Color.parseColor("#1A2196F3"))
                    proto_badge.setTextColor(Color.parseColor("#1976D2"))
                proto_badge.setBackground(proto_bg)
                proto_badge.setPadding(AndroidUtilities.dp(6), AndroidUtilities.dp(2), AndroidUtilities.dp(6), AndroidUtilities.dp(2))
                lp_proto = LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                lp_proto.setMargins(AndroidUtilities.dp(6), 0, 0, 0)
                title_container.addView(proto_badge, lp_proto)

                top_row.addView(title_container, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1.0))

                ping_view = TextView(act)
                ping_bg = GradientDrawable()
                ping_bg.setCornerRadius(AndroidUtilities.dp(6))
                if ping < 300:
                    ping_view.setText(f"⚡ {ping} ms")
                    ping_bg.setColor(Color.parseColor("#1A4CAF50"))
                    ping_view.setTextColor(Color.parseColor("#2E7D32"))
                elif ping < 700:
                    ping_view.setText(f"{ping} ms")
                    ping_bg.setColor(Color.parseColor("#1AFF9800"))
                    ping_view.setTextColor(Color.parseColor("#EF6C00"))
                else:
                    ping_view.setText(f"{ping} ms")
                    ping_bg.setColor(Color.parseColor("#1AF44336"))
                    ping_view.setTextColor(Color.parseColor("#C62828"))
                ping_view.setBackground(ping_bg)
                ping_view.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 11.5)
                ping_view.setTypeface(AndroidUtilities.bold())
                ping_view.setPadding(AndroidUtilities.dp(7), AndroidUtilities.dp(2), AndroidUtilities.dp(7), AndroidUtilities.dp(2))
                top_row.addView(ping_view)

                card.addView(top_row)

                host_view = TextView(act)
                host_view.setText(f"{server}:{port}")
                host_view.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 13)
                host_view.setTextColor(Theme.getColor(Theme.key_windowBackgroundWhiteGrayText))
                host_view.setPadding(0, AndroidUtilities.dp(3), 0, 0)
                host_view.setClickable(True)

                def make_copy_host(hp):
                    def do_c(v):
                        AndroidUtilities.addToClipboard(hp)
                        BulletinHelper.show_info(f"Скопировано: {hp}")
                    return do_c
                _set_click_listener(host_view, make_copy_host(f"{server}:{port}"))
                card.addView(host_view)

                msg_link = p.get("msg_link", "")
                if source:
                    src_view = TextView(act)
                    src_view.setText(L.g('source_channel', source))
                    src_view.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 12)
                    src_view.setTextColor(Theme.getColor(Theme.key_windowBackgroundWhiteBlueText))
                    src_view.setPadding(0, AndroidUtilities.dp(2), 0, 0)
                    if msg_link:
                        src_view.setClickable(True)
                        try:
                            src_view.setOnClickListener(OnClickListener(lambda v, ml=msg_link: _open_link(ml)))
                        except Exception:
                            pass
                    card.addView(src_view)

                btns_row = LinearLayout(act)
                btns_row.setOrientation(LinearLayout.HORIZONTAL)
                btns_row.setGravity(Gravity.CENTER_VERTICAL)
                btns_row.setPadding(0, AndroidUtilities.dp(10), 0, 0)

                cur_url = url
                is_added = bool(_call_dex("pyIsProxyAdded", cur_url))
                btn_state = {"added": is_added}

                def update_card_btn(btn_view, added):
                    shape = GradientDrawable()
                    shape.setCornerRadius(AndroidUtilities.dp(8))
                    if added:
                        btn_view.setText(L.g("btn_delete"))
                        shape.setColor(Color.parseColor("#1CE53935"))
                        btn_view.setTextColor(Color.parseColor("#E53935"))
                    else:
                        btn_view.setText(L.g("btn_add"))
                        shape.setColor(Theme.getColor(Theme.key_featuredStickers_addButton))
                        btn_view.setTextColor(Theme.getColor(Theme.key_featuredStickers_buttonText))
                    btn_view.setBackground(shape)

                def make_add_toggle_action(target_url, state_dict):
                    def do_toggle(btn_view):
                        if not state_dict["added"]:
                            ok = _call_dex("pyAddProxy", target_url)
                            if ok:
                                state_dict["added"] = True
                                update_card_btn(btn_view, True)
                                BulletinHelper.show_info(L.g("proxy_added_toast"))
                        else:
                            ok = _call_dex("pyRemoveProxyByLink", target_url)
                            if not ok:
                                srv = p.get("server", "")
                                prt = p.get("port", "")
                                if srv:
                                    ok = _call_dex("pyRemoveProxyByLink", f"{srv}:{prt}" if prt else srv)
                            if ok:
                                state_dict["added"] = False
                                update_card_btn(btn_view, False)
                                BulletinHelper.show_info(L.g("proxy_removed_toast"))
                            else:
                                BulletinHelper.show_error("Не удалось удалить прокси")
                    return do_toggle

                def make_connect_action(target_url):
                    def do_conn(btn_view):
                        _call_dex("pyOpenProxyInTelegram", act, target_url)
                    return do_conn

                def make_copy_action(target_url):
                    def do_copy(btn_view):
                        AndroidUtilities.addToClipboard(target_url)
                        BulletinHelper.show_info(L.g("copied_clipboard"))
                    return do_copy

                btn_add = create_button(
                    L.g("btn_add"),
                    Theme.getColor(Theme.key_featuredStickers_addButton),
                    Theme.getColor(Theme.key_featuredStickers_buttonText),
                    make_add_toggle_action(cur_url, btn_state),
                )
                update_card_btn(btn_add, is_added)

                def make_updater(btn, st):
                    def fn(val):
                        st["added"] = val
                        update_card_btn(btn, val)
                    return fn
                card_updater_fn = make_updater(btn_add, btn_state)
                card_updaters.append((card_updater_fn, self._get_proxy_key(p)))

                btns_row.addView(btn_add)

                btn_conn = create_button(
                    L.g("btn_connect"),
                    Theme.getColor(Theme.key_dialogButtonSelector),
                    Theme.getColor(Theme.key_windowBackgroundWhiteBlackText),
                    make_connect_action(cur_url),
                )
                p_conn = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                )
                p_conn.setMargins(AndroidUtilities.dp(8), 0, 0, 0)
                btns_row.addView(btn_conn, p_conn)

                btn_copy = create_button(
                    L.g("btn_copy"),
                    Theme.getColor(Theme.key_dialogButtonSelector),
                    Theme.getColor(Theme.key_windowBackgroundWhiteBlackText),
                    make_copy_action(cur_url),
                )
                p_copy = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                )
                p_copy.setMargins(AndroidUtilities.dp(8), 0, 0, 0)
                btns_row.addView(btn_copy, p_copy)

                # More options menu button (extera style ⋮)
                btn_more = TextView(act)
                btn_more.setText("⋮")
                btn_more.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 18)
                btn_more.setTypeface(AndroidUtilities.bold())
                btn_more.setTextColor(Theme.getColor(Theme.key_windowBackgroundWhiteBlackText))
                btn_more.setGravity(Gravity.CENTER)
                more_bg = GradientDrawable()
                more_bg.setCornerRadius(AndroidUtilities.dp(8))
                more_bg.setColor(Theme.getColor(Theme.key_dialogButtonSelector))
                btn_more.setBackground(more_bg)
                btn_more.setClickable(True)
                p_more = LinearLayout.LayoutParams(AndroidUtilities.dp(34), AndroidUtilities.dp(34))
                p_more.setMargins(AndroidUtilities.dp(8), 0, 0, 0)

                def make_more_click(target_p, st, up_fn):
                    return lambda: show_proxy_menu(btn_more, target_p, st, up_fn)

                _set_click_listener(btn_more, make_more_click(p, btn_state, card_updater_fn))
                btns_row.addView(btn_more, p_more)

                card.addView(btns_row)

                card.setClickable(True)
                card.setLongClickable(True)
                try:
                    class _CardLongClick(dynamic_proxy(View.OnLongClickListener)):
                        def __init__(self, target_p, st, up_fn):
                            super().__init__()
                            self.target_p = target_p
                            self.st = st
                            self.up_fn = up_fn
                        def onLongClick(self, v):
                            show_proxy_menu(card, self.target_p, self.st, self.up_fn)
                            return True
                    card.setOnLongClickListener(_CardLongClick(p, btn_state, card_updater_fn))
                except Exception:
                    pass

                return card

            cards_container = LinearLayout(act)
            cards_container.setOrientation(LinearLayout.VERTICAL)
            container.addView(cards_container)

            PAGE_SIZE = 15
            rendered_count = [0]
            more_btn_row = [None]
            is_rendering = [False]

            def render_next_batch():
                if is_rendering[0]:
                    return
                is_rendering[0] = True
                try:
                    start = rendered_count[0]
                    end = min(start + PAGE_SIZE, len(alive))
                    if start >= end:
                        if more_btn_row[0] is not None:
                            try:
                                container.removeView(more_btn_row[0])
                            except Exception:
                                pass
                            more_btn_row[0] = None
                        return

                    for idx in range(start, end):
                        p = alive[idx]
                        card = _build_proxy_card(p)
                        card_params = LinearLayout.LayoutParams(
                            LinearLayout.LayoutParams.MATCH_PARENT,
                            LinearLayout.LayoutParams.WRAP_CONTENT,
                        )
                        card_params.setMargins(0, 0, 0, AndroidUtilities.dp(10))
                        cards_container.addView(card, card_params)

                    rendered_count[0] = end

                    remaining = len(alive) - rendered_count[0]
                    if remaining > 0:
                        btn_text = f"Показать ещё ({min(PAGE_SIZE, remaining)} из {remaining})"
                        if more_btn_row[0] is None:
                            btn_box = LinearLayout(act)
                            btn_box.setOrientation(LinearLayout.VERTICAL)
                            btn_box.setGravity(Gravity.CENTER)
                            btn_box.setPadding(0, AndroidUtilities.dp(6), 0, AndroidUtilities.dp(16))

                            btn_more = create_button(
                                btn_text,
                                Theme.getColor(Theme.key_dialogButtonSelector),
                                Theme.getColor(Theme.key_windowBackgroundWhiteBlackText),
                                lambda v: render_next_batch(),
                            )
                            btn_box.addView(btn_more)
                            more_btn_row[0] = btn_box
                            container.addView(btn_box)
                        else:
                            try:
                                tv_more = more_btn_row[0].getChildAt(0)
                                if tv_more is not None:
                                    tv_more.setText(btn_text)
                            except Exception:
                                pass
                    else:
                        if more_btn_row[0] is not None:
                            try:
                                container.removeView(more_btn_row[0])
                            except Exception:
                                pass
                            more_btn_row[0] = None
                finally:
                    is_rendering[0] = False

            render_next_batch()

            try:
                class _ScrollListener(dynamic_proxy(NestedScrollView.OnScrollChangeListener)):
                    def onScrollChange(self, v, scrollX, scrollY, oldScrollX, oldScrollY):
                        if rendered_count[0] < len(alive):
                            child = v.getChildAt(0)
                            if child is not None:
                                diff = child.getBottom() - (v.getHeight() + scrollY)
                                if diff <= AndroidUtilities.dp(350):
                                    render_next_batch()
                scroll.setOnScrollChangeListener(_ScrollListener())
            except Exception:
                pass

            scroll.addView(container)
            builder.setCustomView(scroll)
            bottom_sheet = builder.create()
            try:
                bottom_sheet.setCanDismissWithSwipe(False)
            except Exception:
                pass
            try:
                bottom_sheet.setAllowNestedScroll(True)
            except Exception:
                pass
            bottom_sheet.show()
        except Exception as e:
            self.log(f"show_results_bottom_sheet error: {e}")
            self._show_scan_results_dialog(channels_count, candidates_count, alive)

    def _show_scan_results_dialog(self, channels_count, candidates_count, alive):
        act, frag = _get_context_and_fragment()
        if act is None:
            BulletinHelper.show_success(L.g("results_title", len(alive)))
            return

        builder = AlertDialogBuilder(act)
        builder.set_title(L.g("scan_dialog_title"))

        fastest = alive[0]
        msg = L.g(
            "scan_dialog_msg",
            channels_count,
            candidates_count,
            len(alive),
            fastest.get("type", "MTProto"),
            fastest.get("server", ""),
            fastest.get("port", ""),
            fastest.get("ping", ""),
        )
        builder.set_message(msg)

        def on_add_all(d, w):
            d.dismiss()
            self._add_all_to_telegram(alive)

        def on_copy_all(d, w):
            d.dismiss()
            self._copy_all_links(alive)

        builder.set_positive_button(L.g("add_all_to_tg"), lambda d, w: [d.dismiss(), self._show_add_proxies_options(alive_proxies)])
        builder.set_neutral_button(L.g("copy_all_links"), on_copy_all)
        builder.set_negative_button(L.g("close"), lambda d, w: d.dismiss())
        builder.show()

    def _add_all_to_telegram(self, alive_list):
        links = [p.get("link") or p.get("url") for p in alive_list if (p.get("link") or p.get("url"))]
        if not links:
            return
        added = _call_dex("pyAddAllProxies", json.dumps(links))
        if added is None:
            added = 0
        if int(added) > 0:
            self._record_added_proxies(alive_list)
            self._enforce_proxy_limit()
        BulletinHelper.show_success(L.g("added_success", added))

    def _copy_all_links(self, alive_list):
        urls = [p.get("link") or p.get("url") for p in alive_list if (p.get("link") or p.get("url"))]
        if urls:
            AndroidUtilities.addToClipboard("\n".join(urls))
            BulletinHelper.show_info(L.g("copied_clipboard"))

    def _create_results_sub_fragment(self):
        items = []
        alive = self._last_results

        if not alive:
            items.append(
                Text(
                    text=L.g("no_proxies_found"),
                    icon="msg_info",
                )
            )
            return items

        items.append(
            Text(
                text=L.g("add_all_to_tg"),
                subtext=L.g("results_title", len(alive)),
                icon="msg_add",
                on_click=lambda *args: self._add_all_to_telegram(alive),
            )
        )

        items.append(
            Text(
                text=L.g("copy_all_links"),
                icon="msg_copy",
                on_click=lambda *args: self._copy_all_links(alive),
            )
        )

        items.append(
            Text(
                text=L.g("clear_results"),
                icon="msg_delete",
                red=True,
                on_click=lambda *args: self._clear_results(),
            )
        )

        items.append(Divider(text=L.g("sorted_by_ping")))
        items.append(Header(text=L.g("header_results")))

        for p in alive:
            p_type = p.get("type", "MTProto")
            server = p.get("server", "")
            port = p.get("port", "")
            ping = p.get("ping", 0)
            url = p.get("url", "")
            flag = p.get("flag", "")
            country = p.get("country", "")
            source = p.get("source", "")

            title = f"{flag} {p_type} • {server}:{port}" if flag else f"{p_type} • {server}:{port}"
            sub_parts = [f"{ping} ms"]
            if country:
                sub_parts.append(country)
            if source:
                sub_parts.append(L.g("source_channel", source))
            sub = " | ".join(sub_parts)

            def make_click(cur_url, cur_title, cur_ping):
                def on_item_click(*args):
                    view = args[0] if args else None
                    self._show_proxy_action_dialog(cur_url, cur_title, cur_ping, view=view)

                return on_item_click

            items.append(
                Text(
                    text=title,
                    subtext=sub,
                    icon="msg_link2",
                    on_click=make_click(url, title, ping),
                )
            )

        return items

    def _clear_results(self):
        self._save_results([])
        BulletinHelper.show_info(L.g("results_cleared"))

    def _show_proxy_action_dialog(self, url, title, ping, view=None):
        act, frag = _get_context_and_fragment(view)
        if act is None:
            return

        b = AlertDialogBuilder(act)
        b.set_title(title)
        b.set_message(L.g("ping_ms", ping))

        def do_connect(d, w):
            d.dismiss()
            if _py_apply_proxy(url):
                BulletinHelper.show_success(L.g("connected_success"))

        def do_add(d, w):
            d.dismiss()
            if _py_add_proxy(url):
                BulletinHelper.show_success(L.g("one_added_success"))

        def do_copy(d, w):
            d.dismiss()
            AndroidUtilities.addToClipboard(url)
            BulletinHelper.show_info(L.g("copied_clipboard"))

        b.set_positive_button(L.g("connect"), do_connect)
        b.set_neutral_button(L.g("add_one"), do_add)
        b.set_negative_button(L.g("copy_one"), do_copy)
        b.show()

    def _on_manage_channels_click(self, *args):
        view = args[0] if args else None
        act, frag = _get_context_and_fragment(view)
        if act is None:
            return

        channels = self._get_channels_config()
        builder = AlertDialogBuilder(act)
        builder.set_title(L.g("channel_count_header", len(channels)))

        options = [
            L.g("add_channel_title"),
            L.g("pick_channels"),
            L.g("clear_channels"),
        ]
        for ch in channels:
            disp = ch.get("display", ch.get("chat", ""))
            ch_depth = _get_channel_depth_label(ch.get("flag", 1), self._get_depth_days())
            options.append(f"{disp} ({ch_depth})")

        def on_select(d, which):
            d.dismiss()
            if which == 0:
                self._show_add_channel_dialog(act)
            elif which == 1:
                self._open_channel_picker(act, frag)
            elif which == 2:
                self._clear_all_channels()
            else:
                ch_idx = which - 3
                if 0 <= ch_idx < len(channels):
                    target_ch = channels[ch_idx]
                    self._show_channel_actions_dialog(target_ch, act, frag)

        builder.set_items(options, on_select)
        builder.set_negative_button(L.g("close"), lambda d, w: d.dismiss())
        builder.show()

    def _show_channel_actions_dialog(self, ch_entry, act, frag):
        if act is None:
            return
        disp = ch_entry.get("display", ch_entry.get("chat", ""))
        cur_depth_label = _get_channel_depth_label(ch_entry.get("flag", 1), self._get_depth_days())

        b = AlertDialogBuilder(act)
        b.set_title(disp)
        options = [
            f"{L.g('channel_action_depth')}: {cur_depth_label}",
            L.g("channel_action_delete"),
        ]

        def on_pick(d, which):
            d.dismiss()
            if which == 0:
                self._show_channel_depth_dialog(ch_entry, act, frag)
            elif which == 1:
                self._confirm_remove_channel(ch_entry, act, frag)

        b.set_items(options, on_pick)
        b.set_negative_button(L.g("close"), lambda d, w: d.dismiss())
        b.show()

    def _show_channel_depth_dialog(self, ch_entry, act, frag):
        if act is None:
            return
        disp = ch_entry.get("display", ch_entry.get("chat", ""))
        cur_flag = ch_entry.get("flag", 1)
        cur_default_days = self._get_depth_days()

        def_sub = L.g("depth_1") if cur_default_days == 1 else (L.g("depth_3") if cur_default_days == 3 else L.g("depth_7"))
        opts = [
            (1, f"{L.g('depth_default')} ({def_sub})"),
            (0, L.g("depth_all")),
            (101, L.g("depth_1")),
            (103, L.g("depth_3")),
            (107, L.g("depth_7")),
        ]

        display_opts = []
        for fl, name in opts:
            if fl == cur_flag:
                display_opts.append(f"{name}  ✓")
            else:
                display_opts.append(name)

        b = AlertDialogBuilder(act)
        b.set_title(f"{L.g('channel_depth_title')}: {disp}")

        def on_select(d, which):
            d.dismiss()
            new_flag = opts[which][0]
            cur_cfg = self._get_channels_config()
            for c in cur_cfg:
                if c.get("chat") == ch_entry.get("chat") and c.get("topic_id") == ch_entry.get("topic_id"):
                    c["flag"] = new_flag
                    break
            self._set_channels_config(cur_cfg)
            try:
                from client_utils import get_last_fragment
                f = get_last_fragment()
                if f and hasattr(f, "rebuildUI"):
                    f.rebuildUI()
            except Exception:
                pass
            BulletinHelper.show_success(L.g("saved"))

        b.set_items(display_opts, on_select)
        b.set_negative_button(L.g("close"), lambda d, w: d.dismiss())
        b.show()

    def _show_add_channel_dialog(self, act):
        if act is None:
            return
        from android.widget import EditText as AndroidEditText, LinearLayout, ScrollView, TextView
        from org.telegram.ui.ActionBar import Theme

        scr = ScrollView(act)
        lay = LinearLayout(act)
        lay.setOrientation(LinearLayout.VERTICAL)
        lay.setPadding(AndroidUtilities.dp(20), AndroidUtilities.dp(10), AndroidUtilities.dp(20), AndroidUtilities.dp(10))
        scr.addView(lay)

        tv = TextView(act)
        tv.setText(L.g("add_channel_hint"))
        tv.setTextSize(14.0)
        tv.setPadding(0, 0, 0, AndroidUtilities.dp(8))
        try:
            tv.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
        except Exception:
            pass
        lay.addView(tv)

        field = AndroidEditText(act)
        field.setHint("@channel, @chat:42, https://t.me/...")
        field.setSingleLine(False)
        field.setMaxLines(6)
        field.setTextSize(15.0)
        try:
            field.setTextColor(Theme.getColor(Theme.key_dialogTextBlack))
            field.setHintTextColor(Theme.getColor(Theme.key_dialogTextHint))
        except Exception:
            pass
        lay.addView(field)

        builder = AlertDialogBuilder(act)
        builder.set_title(L.g("add_channel_title"))
        builder.set_view(scr)

        def on_add(d, w):
            d.dismiss()
            txt = ""
            try:
                txt = str(field.getText().toString())
            except Exception:
                pass
            if not txt.strip():
                return
            tokens = re.split(r"[\s,;\n]+", txt.strip())
            added = 0
            cur_cfg = self._get_channels_config()
            for t in tokens:
                p = _parse_channel_token(t)
                if p:
                    exists = any(c.get("chat") == p["chat"] and c.get("topic_id") == p["topic_id"] for c in cur_cfg)
                    if not exists:
                        cur_cfg.append(p)
                        added += 1
            if added > 0:
                self._set_channels_config(cur_cfg)
                BulletinHelper.show_success(L.g("channel_added_success", added))

        builder.set_positive_button(L.g("add_btn"), on_add)
        builder.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
        builder.show()

    def _on_add_channel_sub_click(self, *args):
        view = args[0] if args else None
        act, frag = _get_context_and_fragment(view)
        self._show_add_channel_dialog(act)

    def _confirm_remove_channel(self, ch_entry, act, frag):
        if act is None:
            return
        disp = ch_entry.get("display", ch_entry.get("chat", ""))
        b = AlertDialogBuilder(act)
        b.set_title(L.g("delete"))
        b.set_message(L.g("remove_channel_confirm", disp))

        def on_del(d, w):
            d.dismiss()
            cur = self._get_channels_config()
            new_cur = [c for c in cur if not (c.get("chat") == ch_entry.get("chat") and c.get("topic_id") == ch_entry.get("topic_id"))]
            self._set_channels_config(new_cur)
            try:
                from client_utils import get_last_fragment
                f = get_last_fragment()
                if f and hasattr(f, "rebuildUI"):
                    f.rebuildUI()
            except Exception:
                pass
            BulletinHelper.show_info(L.g("channel_removed", disp))

        b.set_positive_button(L.g("delete"), on_del)
        b.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
        b.show()

    def _save_channel_entry(self, ch_entry):
        current = self._get_channels_config()
        for idx, c in enumerate(current):
            if c.get("chat") == ch_entry.get("chat") and c.get("topic_id") == ch_entry.get("topic_id"):
                current[idx] = ch_entry
                self._set_channels_config(current)
                return

    def _show_channel_management_picker(self, *args):
        view = args[0] if args else None
        act, frag = _get_context_and_fragment(view)
        if not act:
            act = _get_act()
        if not act:
            return
        channels = self._get_channels_config()
        if not channels:
            return

        plugin = self
        b = AlertDialogBuilder(act)
        b.set_title(L.g("channel_manage_btn"))
        items = []
        for ch in channels:
            disp = ch.get("display", ch.get("chat", ""))
            is_en = bool(ch.get("enabled", True))
            status = "Вкл" if is_en else "Выкл"
            depth = _get_channel_depth_label(ch.get("flag", 1), self._get_depth_days())
            items.append(f"[{status}] {disp} ({depth})")

        def on_pick(d, which):
            d.dismiss()
            if 0 <= which < len(channels):
                plugin._show_channel_actions_dialog(channels[which], act, frag)

        b.set_items(items, on_pick)
        b.set_negative_button(L.g("close"), lambda d, w: d.dismiss())
        b.show()

    def _show_channel_actions_dialog(self, ch_entry, act, frag):
        if act is None:
            return
        disp = ch_entry.get("display", ch_entry.get("chat", ""))
        cur_depth_label = _get_channel_depth_label(ch_entry.get("flag", 1), self._get_depth_days())
        is_en = bool(ch_entry.get("enabled", True))
        status_label = "Включен" if is_en else "Отключен"

        plugin = self
        b = AlertDialogBuilder(act)
        b.set_title(disp)
        options = [
            f"Статус: {status_label} (нажмите для переключения)",
            f"{L.g('channel_action_depth')}: {cur_depth_label}",
            L.g("channel_action_delete"),
        ]

        def on_pick(d, which):
            d.dismiss()
            if which == 0:
                ch_entry["enabled"] = not is_en
                plugin._save_channel_entry(ch_entry)
                try:
                    from client_utils import get_last_fragment
                    f = get_last_fragment()
                    if f and hasattr(f, "rebuildUI"):
                        f.rebuildUI()
                except Exception:
                    pass
                run_on_ui_thread(lambda: BulletinHelper.show_info(f"{disp}: {'Включен' if not is_en else 'Отключен'}"))
            elif which == 1:
                plugin._show_channel_depth_dialog(ch_entry, act, frag)
            elif which == 2:
                plugin._confirm_remove_channel(ch_entry, act, frag)

        b.set_items(options, on_pick)
        b.set_negative_button(L.g("close"), lambda d, w: d.dismiss())
        b.show()

    def _create_channels_sub_fragment(self):
        items = []
        channels = self._get_channels_config()

        items.append(
            Text(
                text=L.g("add_channel_title"),
                subtext=L.g("manage_channels_sub"),
                icon="msg_add",
                on_click=lambda *args: self._on_add_channel_sub_click(*args),
            )
        )

        items.append(
            Text(
                text=L.g("pick_channels"),
                subtext=L.g("pick_channels_sub"),
                icon="msg_channel",
                on_click=lambda *args: self._open_channel_picker(*args),
            )
        )

        if channels:
            items.append(
                Text(
                    text=L.g("channel_manage_btn"),
                    subtext=L.g("channel_manage_sub"),
                    icon="msg_settings",
                    on_click=lambda *args: self._show_channel_management_picker(*args),
                )
            )
            items.append(
                Text(
                    text=L.g("clear_channels"),
                    icon="msg_delete",
                    red=True,
                    on_click=lambda *args: self._clear_all_channels(),
                )
            )

        items.append(Divider())
        active_cnt = sum(1 for c in channels if c.get("enabled", True))
        items.append(Header(text=f"Добавленные каналы ({active_cnt}/{len(channels)} активны)"))

        if not channels:
            items.append(
                Text(
                    text=L.g("no_channels_in_list"),
                    icon="msg_info",
                )
            )
        else:
            plugin = self
            for idx, ch in enumerate(channels):
                disp = ch.get("display", ch.get("chat", ""))
                ch_depth = _get_channel_depth_label(ch.get("flag", 1), self._get_depth_days())
                is_en = bool(ch.get("enabled", True))

                def make_toggle(cur_ch):
                    def on_toggle(val):
                        cur_ch["enabled"] = bool(val)
                        plugin._save_channel_entry(cur_ch)
                        try:
                            from client_utils import get_last_fragment
                            f = get_last_fragment()
                            if f and hasattr(f, "rebuildUI"):
                                f.rebuildUI()
                        except Exception:
                            pass
                    return on_toggle

                ch_key = f"ch_sw_{ch.get('chat')}_{ch.get('topic_id')}_{idx}"
                status_str = "Включен" if is_en else "Отключен"

                items.append(
                    Switch(
                        key=ch_key,
                        text=disp,
                        subtext=f"{status_str} • {ch_depth}",
                        default=is_en,
                        on_change=make_toggle(ch),
                    )
                )

        return items

    def _clear_all_channels(self):
        self._set_channels_config([])
        BulletinHelper.show_info(L.g("channels_cleared"))

    def _handle_picked_channels(self, json_str):
        if not json_str:
            return
        items_data = _safe_json_loads(json_str)
        if not items_data or not isinstance(items_data, list):
            return

        account = int(UserConfig.selectedAccount)
        mc = MessagesController.getInstance(account)
        to_add = []

        for it in items_data:
            did = int(it.get("did", 0))
            tid = int(it.get("tid", 0))
            if did == 0:
                continue

            chat = None
            user = None
            chat_username = None
            chat_title = None

            if did < 0:
                chat = mc.getChat(jlong(-did))
                if chat:
                    chat_username = getattr(chat, "username", None)
                    chat_title = getattr(chat, "title", None)
            elif did > 0:
                user = mc.getUser(jlong(did))
                if user:
                    chat_username = getattr(user, "username", None)
                    chat_title = getattr(user, "first_name", None)

            is_forum = False
            if chat and did < 0:
                try:
                    ChatObject = jclass("org.telegram.messenger.ChatObject")
                    is_forum = bool(ChatObject.isForum(chat))
                except Exception:
                    try:
                        is_forum = bool(getattr(chat, "forum", False))
                    except Exception:
                        pass

            if is_forum and tid == 0:
                self._prompt_forum_topic_selection(chat, did, chat_username, chat_title)
                continue

            topic_title = None
            if tid > 0:
                try:
                    tc = mc.getTopicsController()
                    if tc:
                        t_obj = tc.findTopic(jlong(-did), jlong(tid))
                        if t_obj and getattr(t_obj, "title", None):
                            topic_title = str(t_obj.title)
                except Exception:
                    pass

            if chat_username:
                clean_ch = chat_username.lstrip("@")
                disp = f"@{clean_ch}"
                if topic_title:
                    disp += f" › {topic_title}"
                elif tid > 0:
                    disp += f" (тема #{tid})"
                to_add.append({
                    "chat": clean_ch,
                    "topic_id": tid if tid > 0 else None,
                    "display": disp,
                    "flag": 1,
                })
            else:
                clean_ch = str(did)
                disp = chat_title if chat_title else f"ID:{did}"
                if topic_title:
                    disp += f" › {topic_title}"
                elif tid > 0:
                    disp += f" (тема #{tid})"
                to_add.append({
                    "chat": clean_ch,
                    "topic_id": tid if tid > 0 else None,
                    "display": disp,
                    "flag": 1,
                })

        if to_add:
            current = self._get_channels_config()
            added_cnt = 0
            for item in to_add:
                if item and not any(c.get("chat") == item["chat"] and c.get("topic_id") == item["topic_id"] for c in current):
                    current.append(item)
                    added_cnt += 1
            if added_cnt > 0:
                self._set_channels_config(current)
                run_on_ui_thread(
                    lambda: BulletinHelper.show_success(
                        L.g("added_success", added_cnt)
                    )
                )

    def _prompt_forum_topic_selection(self, chat, did, chat_username, chat_title):
        clean_ch = chat_username.lstrip("@") if chat_username else str(did)
        base_disp = f"@{clean_ch}" if chat_username else (chat_title if chat_title else f"ID:{did}")
        act = _get_act()
        plugin = self

        def show_dialog(topics_list):
            if not act:
                plugin._add_single_channel_entry(clean_ch, None, base_disp)
                return

            def ui_action():
                b = AlertDialogBuilder(act)
                b.set_title(f"Темы: {base_disp}")

                options = ["Весь чат (без фильтра по темам)"]
                for t in topics_list:
                    options.append(f"#{t.get('id', 0)} {t.get('title', 'Тема')}")

                def on_select(d, which):
                    d.dismiss()
                    if which == 0:
                        plugin._add_single_channel_entry(clean_ch, None, base_disp)
                    else:
                        selected_t = topics_list[which - 1]
                        t_id = int(selected_t.get("id", 0))
                        t_title = str(selected_t.get("title", f"Тема #{t_id}"))
                        disp_with_topic = f"{base_disp} › {t_title}"
                        plugin._add_single_channel_entry(clean_ch, t_id, disp_with_topic)

                b.set_items(options, on_select)
                b.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
                b.show()

            run_on_ui_thread(ui_action)

        # Check local cache first
        try:
            account = int(UserConfig.selectedAccount)
            mc = MessagesController.getInstance(account)
            tc = mc.getTopicsController() if mc else None
            cached_topics = tc.getTopics(jlong(-did)) if tc else None
            if cached_topics and not cached_topics.isEmpty():
                t_arr = []
                for obj in _iter_java_list(cached_topics):
                    if obj:
                        t_id = getattr(obj, "id", 0)
                        t_title = getattr(obj, "title", f"Тема #{t_id}")
                        t_arr.append({"id": int(t_id), "title": str(t_title)})
                if t_arr:
                    show_dialog(t_arr)
                    return
        except Exception as e:
            plugin.log(f"cached topics check error: {e}")

        # Fetch via Dex
        if dex.loaded:
            try:
                CbCls = jclass("com.exteraplugins.proxy_manager.Main$ForumTopicsCallback")
                class FTCallback(dynamic_proxy(CbCls)):
                    def onTopics(s_self, jstr):
                        try:
                            parsed = _safe_json_loads(str(jstr)) or []
                            show_dialog(parsed)
                        except Exception as ex:
                            plugin.log(f"onTopics parse error: {ex}")
                            show_dialog([])
                dex.call("pyGetForumTopics", jlong(-did), FTCallback())
                return
            except Exception as e:
                plugin.log(f"pyGetForumTopics call error: {e}")

        # Fallback: add whole chat
        plugin._add_single_channel_entry(clean_ch, None, base_disp)

    def _add_single_channel_entry(self, clean_ch, topic_id, display):
        current = self._get_channels_config()
        if not any(c.get("chat") == clean_ch and c.get("topic_id") == topic_id for c in current):
            current.append({
                "chat": clean_ch,
                "topic_id": topic_id,
                "display": display,
                "flag": 1,
            })
            self._set_channels_config(current)
            run_on_ui_thread(lambda: BulletinHelper.show_info(f"Добавлено: {display}"))
            try:
                from client_utils import get_last_fragment
                f = get_last_fragment()
                if f and hasattr(f, "rebuildUI"):
                    f.rebuildUI()
            except Exception:
                pass

    def _open_channel_picker(self, *args, **kwargs):
        view = None
        act = kwargs.get("act")
        frag = kwargs.get("frag")
        for a in args:
            if hasattr(a, "presentFragment"):
                if hasattr(a, "getParentActivity"):
                    frag = a
                else:
                    act = a
            elif hasattr(a, "getContext"):
                view = a

        if act is None and frag is None:
            act, frag = _get_context_and_fragment(view)

        plugin = self

        if dex.loaded:
            try:
                CbCls = jclass("com.exteraplugins.proxy_manager.Main$ChannelPickerCallback")
                class PickerCb(dynamic_proxy(CbCls)):
                    def onChannelsPicked(self, json_arr_str):
                        try:
                            plugin._handle_picked_channels(str(json_arr_str))
                        except Exception as ex:
                            plugin.log(f"onChannelsPicked error: {ex}")

                dex.call("pyOpenChannelPicker", frag, PickerCb())
                return
            except Exception as e:
                self.log(f"pyOpenChannelPicker failed, trying fallback: {e}")

        try:
            USA = jclass("org.telegram.ui.UsersSelectActivity")
            DelCls = getattr(
                USA,
                "FilterUsersActivityDelegate",
                jclass("org.telegram.ui.UsersSelectActivity$FilterUsersActivityDelegate"),
            )

            picker = None
            try:
                picker = USA(False, ArrayList(), 0)
            except Exception:
                try:
                    picker = USA(True, ArrayList(), 0)
                except Exception:
                    picker = USA(0)

            try:
                picker.noChatTypes = True
            except Exception:
                pass

            class FallbackPickerDelegate(dynamic_proxy(DelCls)):
                def didSelectChats(s_self, dids, flags):
                    try:
                        if not dids:
                            return
                        arr = []
                        for did_obj in _iter_java_list(dids):
                            arr.append({"did": int(did_obj), "tid": 0})
                        plugin._handle_picked_channels(json.dumps(arr))
                    except Exception as ex:
                        plugin.log(f"Fallback picker error: {ex}")

            picker.setDelegate(FallbackPickerDelegate())
            presented = False
            if frag is not None:
                try:
                    frag.presentFragment(picker)
                    presented = True
                except Exception:
                    pass
            if not presented and act is not None and hasattr(act, "presentFragment"):
                try:
                    act.presentFragment(picker)
                    presented = True
                except Exception:
                    pass
            if not presented:
                from org.telegram.ui import LaunchActivity
                if LaunchActivity.instance is not None:
                    LaunchActivity.instance.presentFragment(picker)
        except Exception as e:
            self.log(f"Error launching fallback channel picker: {e}")

    def _on_import_click(self, *args):
        view = args[0] if args else None
        act, frag = _get_context_and_fragment(view)
        if act is None:
            return

        builder = AlertDialogBuilder(act)
        builder.set_title(L.g("import_title"))
        items = [
            L.g("import_from_clip"),
            L.g("import_from_file"),
        ]

        def on_select(d, which):
            d.dismiss()
            if which == 0:
                self._import_from_clipboard()
            elif which == 1:
                self._import_from_file_prompt(act)

        builder.set_items(items, on_select)
        builder.set_negative_button(L.g("close"), lambda d, w: d.dismiss())
        builder.show()

    def _import_from_clipboard(self):
        try:
            from android.content import Context
            ctx = ApplicationLoader.applicationContext
            cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE)
            clip = cm.getPrimaryClip()
            if not clip or clip.getItemCount() == 0:
                BulletinHelper.show_error(L.g("no_proxies_in_clip"))
                return
            item = clip.getItemAt(0)
            text = str(item.coerceToText(ctx) if item else "")
            if not text:
                BulletinHelper.show_error(L.g("no_proxies_in_clip"))
                return
            self._import_proxies_from_raw_text(text)
        except Exception as e:
            self.log(f"Clipboard import error: {e}")
            BulletinHelper.show_error(str(e))

    def _import_from_file_prompt(self, act):
        from android.os import Environment
        pub_downloads = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
        txt_files = []
        if pub_downloads and pub_downloads.exists():
            for f in pub_downloads.listFiles():
                if f.isFile() and f.getName().lower().endswith(".txt"):
                    txt_files.append(f)

        if not txt_files:
            ctx = ApplicationLoader.applicationContext
            ext_dir = ctx.getExternalFilesDir(None)
            target_dir = ext_dir if ext_dir else ctx.getCacheDir()
            if target_dir and target_dir.exists():
                for f in target_dir.listFiles():
                    if f.isFile() and f.getName().lower().endswith(".txt"):
                        txt_files.append(f)

        if not txt_files:
            BulletinHelper.show_error(L.g("no_txt_files_found"))
            return

        builder = AlertDialogBuilder(act)
        builder.set_title(L.g("choose_file_to_import"))
        names = [f.getName() for f in txt_files]

        def on_choose(d, which):
            d.dismiss()
            chosen = txt_files[which]
            try:
                with open(str(chosen.getAbsolutePath()), "r", encoding="utf-8", errors="ignore") as fp:
                    content = fp.read()
                self._import_proxies_from_raw_text(content)
            except Exception as e:
                BulletinHelper.show_error(str(e))

        builder.set_items(names, on_choose)
        builder.set_negative_button(L.g("cancel"), lambda d, w: d.dismiss())
        builder.show()

    def _import_proxies_from_raw_text(self, text):
        candidates = _extract_proxies_from_raw_text(text)
        if not candidates:
            BulletinHelper.show_error(L.g("no_proxies_in_clip"))
            return

        added_count = 0
        for p in candidates:
            url = p.get("url")
            if url and _py_add_proxy(url):
                added_count += 1

        if added_count > 0:
            BulletinHelper.show_success(L.g("imported_success", added_count))
        else:
            BulletinHelper.show_error(L.g("no_proxies_in_clip"))

    def _on_message_proxy_click(self, ctx_dict):
        try:
            msg = ctx_dict.get("message")
            if not msg:
                return

            mo = getattr(msg, "messageOwner", None) or msg
            proxies = _extract_proxy_candidates_from_message(mo)
            if not proxies:
                run_on_ui_thread(lambda: BulletinHelper.show_error(L.g("no_proxies_in_message")))
                return

            m_id = getattr(mo, "id", None)
            chat_id = None
            try:
                chat_id = msg.getDialogId()
            except Exception:
                try:
                    p_id = getattr(mo, "peer_id", None)
                    if p_id:
                        chat_id = getattr(p_id, "channel_id", None) or getattr(p_id, "chat_id", None) or getattr(p_id, "user_id", None)
                except Exception:
                    pass
            msg_link = ""
            if m_id and chat_id is not None:
                s_id = str(chat_id)
                if s_id.startswith("-100"):
                    msg_link = f"https://t.me/c/{s_id[4:]}/{m_id}"
                elif s_id.lstrip("-").isdigit():
                    msg_link = f"https://t.me/c/{s_id.lstrip('-')}/{m_id}"
                else:
                    msg_link = f"https://t.me/{s_id.lstrip('@')}/{m_id}"

            for p in proxies:
                if msg_link:
                    p["msg_link"] = msg_link

            run_on_ui_thread(lambda: BulletinHelper.show_info(L.g("checking_msg_proxies", len(proxies))))

            def worker():
                alive = _batch_check_candidates(proxies, timeout_ms=3000)
                alive.sort(key=lambda x: x.get("ping", 999999))
                _enrich_proxies_with_country(alive)

                if not alive:
                    run_on_ui_thread(
                        lambda: BulletinHelper.show_error(L.g("no_alive_proxies", len(proxies)))
                    )
                    return

                run_on_ui_thread(
                    lambda: self._show_results_bottom_sheet(alive, 1, len(alive))
                )

            threading.Thread(target=worker, daemon=True).start()
        except Exception as e:
            self.log(f"Message proxy click error: {e}")


# __DEX_BEGIN__
# PLACEHOLDER
# __DEX_END__
