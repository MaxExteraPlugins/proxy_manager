package com.exteraplugins.proxy_manager;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.SystemClock;
import android.util.Log;
import com.exteragram.messenger.proxy.ProxyController;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import org.json.JSONArray;
import org.json.JSONObject;
import org.telegram.messenger.AndroidUtilities;
import org.telegram.messenger.FileLoader;
import org.telegram.messenger.MessageObject;
import org.telegram.messenger.SharedConfig;
import org.telegram.messenger.UserConfig;
import org.telegram.messenger.browser.Browser;
import org.telegram.tgnet.ConnectionsManager;
import org.telegram.ui.LaunchActivity;
import org.telegram.utils.proxy.ProxySettings;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URL;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.Callable;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import android.view.View;
import org.telegram.messenger.LocaleController;
import org.telegram.ui.ActionBar.ActionBar;
import org.telegram.ui.ActionBar.ActionBarMenu;
import org.telegram.ui.ActionBar.ActionBarMenuItem;
import org.telegram.ui.ActionBar.BaseFragment;
import org.telegram.ui.ActionBar.Theme;
import org.telegram.ui.Cells.TextSettingsCell;
import org.telegram.ui.Components.RecyclerListView;
import androidx.recyclerview.widget.RecyclerView;

public class Main {
    private static final String TAG = "ProxyManager";
    private static final java.util.concurrent.ConcurrentHashMap<String, String> GEO_CACHE = new java.util.concurrent.ConcurrentHashMap<String, String>();
    public static final String VERSION = "1.0.28";
    public static final int VERSION_CODE = 28;

    public static volatile boolean autoConnectEnabled = true;
    public static volatile Runnable onAutoConnectFallback = null;

    public static String httpGet(String urlStr) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(urlStr);
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setRequestProperty("User-Agent", "Mozilla/5.0 (Android; Telegram)");
            conn.setConnectTimeout(6000);
            conn.setReadTimeout(6000);
            if (conn.getResponseCode() == 200) {
                BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream(), "UTF-8"));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = reader.readLine()) != null) {
                    sb.append(line).append("\n");
                }
                reader.close();
                return sb.toString();
            }
        } catch (Throwable t) {
            Log.e(TAG, "httpGet failed for " + urlStr, t);
        } finally {
            if (conn != null) {
                try { conn.disconnect(); } catch (Throwable ignored) {}
            }
        }
        return null;
    }

    public static void load() {
        Log.d(TAG, "DEX module loaded v" + VERSION);
        try {
            Method m = ConnectionsManager.class.getMethod("setProxySettings", boolean.class, ProxySettings.class);
            XposedBridge.hookMethod(m, new XC_MethodHook() {
                @Override
                public void beforeHookedMethod(MethodHookParam param) {
                    try {
                        if (!autoConnectEnabled) return;
                        boolean enabled = (Boolean) param.args[0];
                        if (!enabled) return;

                        boolean wasEnabled = false;
                        try {
                            wasEnabled = SharedConfig.isProxyEnabled();
                        } catch (Throwable ignored) {}

                        // If proxy was already enabled, user is manually switching proxies -> do not override
                        if (wasEnabled) {
                            return;
                        }

                        ProxySettings settings = (ProxySettings) param.args[1];

                        // Only auto-connect if no proxy was chosen or settings are invalid
                        boolean needsWorking = false;
                        if (settings == null || !settings.isValid()) {
                            needsWorking = true;
                        }

                        if (needsWorking) {
                            ProxySettings working = findFirstAliveProxy(1500);
                            if (working != null) {
                                param.args[1] = working;
                                applyProxySettings(working);
                            } else if (onAutoConnectFallback != null) {
                                onAutoConnectFallback.run();
                            }
                        } else {
                            // If proxy was toggled ON with an existing proxy, monitor if it fails to connect
                            new Thread(new Runnable() {
                                @Override
                                public void run() {
                                    try {
                                        Thread.sleep(3500);
                                        if (!SharedConfig.isProxyEnabled()) return;
                                        int account = UserConfig.selectedAccount;
                                        int state = ConnectionsManager.getInstance(account).getConnectionState();
                                        if (state == ConnectionsManager.ConnectionStateConnectingToProxy ||
                                            state == ConnectionsManager.ConnectionStateConnecting) {
                                            ProxySettings working = findFirstAliveProxy(1500);
                                            if (working != null) {
                                                applyProxySettings(working);
                                            } else if (onAutoConnectFallback != null) {
                                                onAutoConnectFallback.run();
                                            }
                                        }
                                    } catch (Throwable ignored) {}
                                }
                            }).start();
                        }
                    } catch (Throwable t) {
                        Log.e(TAG, "setProxySettings hook error", t);
                    }
                }
            });
        } catch (Throwable t) {
            Log.e(TAG, "hook setProxySettings failed", t);
        }
        hookProxyListActivity();
    }

    private static void hookProxyListActivity() {
        try {
            final Class<?> plaClass = Class.forName("org.telegram.ui.ProxyListActivity");
            Method createViewMethod = plaClass.getMethod("createView", Context.class);
            XposedBridge.hookMethod(createViewMethod, new XC_MethodHook() {
                @Override
                public void afterHookedMethod(MethodHookParam param) {
                    try {
                        final BaseFragment fragment = (BaseFragment) param.thisObject;
                        if (fragment == null) return;
                        ActionBar actionBar = fragment.getActionBar();
                        if (actionBar != null) {
                            ActionBarMenu menu = actionBar.createMenu();
                            if (menu != null) {
                                ActionBarMenuItem existing = menu.getItem(8899);
                                if (existing == null) {
                                    int iconRes = getDrawableRes("msg_settings");
                                    ActionBarMenuItem item = menu.addItem(8899, iconRes);
                                    if (item != null) {
                                        item.setContentDescription(getSettingsButtonTitle());
                                        item.setOnClickListener(new View.OnClickListener() {
                                            @Override
                                            public void onClick(View v) {
                                                openProxyManagerSettings(fragment);
                                            }
                                        });
                                    }
                                } else {
                                    existing.setOnClickListener(new View.OnClickListener() {
                                        @Override
                                        public void onClick(View v) {
                                            openProxyManagerSettings(fragment);
                                        }
                                    });
                                }
                            }
                        }

                        try {
                            Field lvField = plaClass.getDeclaredField("listView");
                            lvField.setAccessible(true);
                            RecyclerListView lv = (RecyclerListView) lvField.get(fragment);
                            if (lv != null && (lv.getTag() == null || !"pm_hooked".equals(lv.getTag()))) {
                                lv.setTag("pm_hooked");
                                final RecyclerListView.OnItemClickListener orig = lv.getOnItemClickListener();
                                lv.setOnItemClickListener(new RecyclerListView.OnItemClickListener() {
                                    @Override
                                    public void onItemClick(View view, int position) {
                                        try {
                                            Field rcField = plaClass.getDeclaredField("rowCount");
                                            rcField.setAccessible(true);
                                            int rc = rcField.getInt(fragment);
                                            if (position == rc) {
                                                openProxyManagerSettings(fragment);
                                                return;
                                            }
                                        } catch (Throwable ignored) {}
                                        if (orig != null) {
                                            orig.onItemClick(view, position);
                                        }
                                    }
                                });
                            }
                        } catch (Throwable ignored) {}
                    } catch (Throwable t) {
                        Log.e(TAG, "hook createView error", t);
                    }
                }
            });

            Class<?> adapterClass = Class.forName("org.telegram.ui.ProxyListActivity$ListAdapter");
            Method getItemCountMethod = adapterClass.getMethod("getItemCount");
            XposedBridge.hookMethod(getItemCountMethod, new XC_MethodHook() {
                @Override
                public void afterHookedMethod(MethodHookParam param) {
                    try {
                        int count = (Integer) param.getResult();
                        param.setResult(count + 1);
                    } catch (Throwable ignored) {}
                }
            });

            Method getItemViewTypeMethod = adapterClass.getMethod("getItemViewType", int.class);
            XposedBridge.hookMethod(getItemViewTypeMethod, new XC_MethodHook() {
                @Override
                public void beforeHookedMethod(MethodHookParam param) {
                    try {
                        Object adapter = param.thisObject;
                        Field this0Field = adapter.getClass().getDeclaredField("this$0");
                        this0Field.setAccessible(true);
                        Object pla = this0Field.get(adapter);
                        Field rowCountField = pla.getClass().getDeclaredField("rowCount");
                        rowCountField.setAccessible(true);
                        int rowCount = rowCountField.getInt(pla);
                        int pos = (Integer) param.args[0];
                        if (pos == rowCount) {
                            param.setResult(1); // 1 = TextSettingsCell in ProxyListActivity$ListAdapter
                        }
                    } catch (Throwable ignored) {}
                }
            });

            Method isEnabledMethod = null;
            for (Method m : adapterClass.getDeclaredMethods()) {
                if ("isEnabled".equals(m.getName()) && m.getParameterTypes().length == 1) {
                    isEnabledMethod = m;
                    break;
                }
            }
            if (isEnabledMethod != null) {
                XposedBridge.hookMethod(isEnabledMethod, new XC_MethodHook() {
                    @Override
                    public void beforeHookedMethod(MethodHookParam param) {
                        try {
                            RecyclerView.ViewHolder vh = (RecyclerView.ViewHolder) param.args[0];
                            if (vh != null) {
                                int pos = vh.getAdapterPosition();
                                Object adapter = param.thisObject;
                                Field this0Field = adapter.getClass().getDeclaredField("this$0");
                                this0Field.setAccessible(true);
                                Object pla = this0Field.get(adapter);
                                Field rowCountField = pla.getClass().getDeclaredField("rowCount");
                                rowCountField.setAccessible(true);
                                int rowCount = rowCountField.getInt(pla);
                                if (pos == rowCount) {
                                    param.setResult(true);
                                }
                            }
                        } catch (Throwable ignored) {}
                    }
                });
            }

            Method onBindViewHolderMethod = null;
            for (Method m : adapterClass.getDeclaredMethods()) {
                if ("onBindViewHolder".equals(m.getName()) && m.getParameterTypes().length == 2) {
                    onBindViewHolderMethod = m;
                    break;
                }
            }
            if (onBindViewHolderMethod != null) {
                XposedBridge.hookMethod(onBindViewHolderMethod, new XC_MethodHook() {
                    @Override
                    public void afterHookedMethod(MethodHookParam param) {
                        try {
                            Object adapter = param.thisObject;
                            Field this0Field = adapter.getClass().getDeclaredField("this$0");
                            this0Field.setAccessible(true);
                            final BaseFragment pla = (BaseFragment) this0Field.get(adapter);
                            Field rowCountField = pla.getClass().getDeclaredField("rowCount");
                            rowCountField.setAccessible(true);
                            int rowCount = rowCountField.getInt(pla);
                            int pos = (Integer) param.args[1];
                            if (pos == rowCount) {
                                RecyclerView.ViewHolder vh = (RecyclerView.ViewHolder) param.args[0];
                                if (vh != null && vh.itemView instanceof TextSettingsCell) {
                                    TextSettingsCell cell = (TextSettingsCell) vh.itemView;
                                    cell.setText(getSettingsButtonTitle(), false);
                                    cell.setTextColor(Theme.getColor(Theme.key_windowBackgroundWhiteBlueText4));
                                    cell.setOnClickListener(new View.OnClickListener() {
                                        @Override
                                        public void onClick(View v) {
                                            openProxyManagerSettings(pla);
                                        }
                                    });
                                }
                            }
                        } catch (Throwable ignored) {}
                    }
                });
            }
        } catch (Throwable t) {
            Log.e(TAG, "hookProxyListActivity failed", t);
        }
    }

    public static void openProxyManagerSettings(BaseFragment fragment) {
        try {
            Class<?> controllerCls = Class.forName("com.exteragram.messenger.plugins.PluginsController");
            Method getInstance = controllerCls.getMethod("getInstance");
            Object controller = getInstance.invoke(null);
            Field pluginsField = null;
            try {
                pluginsField = controllerCls.getField("plugins");
            } catch (Throwable t) {
                pluginsField = controllerCls.getDeclaredField("plugins");
                pluginsField.setAccessible(true);
            }
            java.util.Map<?, ?> map = (java.util.Map<?, ?>) pluginsField.get(controller);
            Object javaPlugin = map != null ? map.get("proxy_manager") : null;
            if (javaPlugin != null) {
                Class<?> actCls = Class.forName("com.exteragram.messenger.plugins.ui.PluginSettingsActivity");
                Constructor<?>[] ctors = actCls.getConstructors();
                BaseFragment targetFragment = null;
                for (Constructor<?> c : ctors) {
                    if (c.getParameterTypes().length == 1) {
                        targetFragment = (BaseFragment) c.newInstance(javaPlugin);
                        break;
                    }
                }
                if (targetFragment != null && fragment != null) {
                    fragment.presentFragment(targetFragment);
                }
            }
        } catch (Throwable t) {
            Log.e(TAG, "openProxyManagerSettings error", t);
        }
    }

    private static String getSettingsButtonTitle() {
        try {
            LocaleController lc = LocaleController.getInstance();
            Locale loc = lc != null ? lc.getCurrentLocale() : null;
            if (loc != null) {
                String lang = loc.getLanguage();
                if (lang != null && (lang.toLowerCase().startsWith("ru") || lang.toLowerCase().startsWith("uk") || lang.toLowerCase().startsWith("be"))) {
                    return "Настройки Proxy Manager";
                }
            }
        } catch (Throwable ignored) {}
        return "Proxy Manager Settings";
    }

    private static int getDrawableRes(String name) {
        try {
            Class<?> rDraw = Class.forName("org.telegram.messenger.R$drawable");
            Field f = rDraw.getField(name);
            return f.getInt(null);
        } catch (Throwable t) {
            return 0;
        }
    }

    public static void unload() {
        Log.d(TAG, "DEX module unloaded");
    }

    public static void pySetAutoConnect(boolean enabled) {
        autoConnectEnabled = enabled;
    }

    public static void pySetAutoConnectFallback(Runnable fallback) {
        onAutoConnectFallback = fallback;
    }

    public static long getOpenChatDialogId() {
        try {
            LaunchActivity la = LaunchActivity.instance;
            if (la == null || la.isFinishing()) return 0;

            // Check if PhotoViewer is currently open
            try {
                Class<?> pvClass = Class.forName("org.telegram.ui.PhotoViewer");
                Method getInstanceM = pvClass.getMethod("getInstance");
                Object pv = getInstanceM.invoke(null);
                if (pv != null) {
                    Method isVisibleM = pvClass.getMethod("isVisible");
                    if ((Boolean) isVisibleM.invoke(pv)) {
                        Method getCurMsgM = pvClass.getMethod("getCurrentMessageObject");
                        Object msg = getCurMsgM.invoke(pv);
                        if (msg instanceof MessageObject) {
                            return ((MessageObject) msg).getDialogId();
                        }
                    }
                }
            } catch (Throwable ignored) {}

            BaseFragment f = la.getActionBarLayout() != null ? la.getActionBarLayout().getLastFragment() : null;
            if (f != null && "ChatActivity".equals(f.getClass().getSimpleName())) {
                Method m = f.getClass().getMethod("getDialogId");
                return (Long) m.invoke(f);
            }
        } catch (Throwable ignored) {}
        return 0;
    }

    public interface ChannelPickerCallback {
        void onChannelsPicked(String jsonArrayStr);
    }

    public static void pyOpenChannelPicker(final Object hostFragment, final ChannelPickerCallback callback) {
        AndroidUtilities.runOnUIThread(new Runnable() {
            @Override
            public void run() {
                try {
                    BaseFragment currentFragment = null;
                    if (hostFragment instanceof BaseFragment) {
                        currentFragment = (BaseFragment) hostFragment;
                    }
                    if (currentFragment == null) {
                        LaunchActivity la = LaunchActivity.instance;
                        if (la != null && !la.isFinishing() && la.getActionBarLayout() != null) {
                            currentFragment = la.getActionBarLayout().getLastFragment();
                        }
                    }
                    if (currentFragment == null) return;

                    android.os.Bundle args = new android.os.Bundle();
                    args.putBoolean("onlySelect", true);
                    args.putBoolean("checkCanWrite", false);
                    args.putBoolean("canSelectTopics", true);
                    args.putInt("dialogsType", 3);
                    args.putBoolean("resetDelegate", true);
                    args.putBoolean("allowSwitchAccount", false);

                    Class<?> daClass = Class.forName("org.telegram.ui.DialogsActivity");
                    Constructor<?> ctor = daClass.getConstructor(android.os.Bundle.class);
                    final BaseFragment fragment = (BaseFragment) ctor.newInstance(args);

                    Class<?> delegateClass = Class.forName("org.telegram.ui.DialogsActivity$DialogsActivityDelegate");
                    Object proxy = java.lang.reflect.Proxy.newProxyInstance(delegateClass.getClassLoader(), new Class<?>[]{delegateClass}, new java.lang.reflect.InvocationHandler() {
                        @Override
                        public Object invoke(Object prx, Method method, Object[] mArgs) throws Throwable {
                            if ("didSelectDialogs".equals(method.getName())) {
                                try {
                                    if (mArgs != null && mArgs.length > 1 && mArgs[1] instanceof ArrayList) {
                                        ArrayList<?> list = (ArrayList<?>) mArgs[1];
                                        JSONArray arr = new JSONArray();
                                        for (Object item : list) {
                                            long did = 0;
                                            long tid = 0;
                                            if (item instanceof Number) {
                                                did = ((Number) item).longValue();
                                            } else if (item != null) {
                                                try {
                                                    Field f = item.getClass().getDeclaredField("dialogId");
                                                    f.setAccessible(true);
                                                    did = f.getLong(item);
                                                } catch (Throwable ignored) {
                                                    try {
                                                        Field f = item.getClass().getDeclaredField("id");
                                                        f.setAccessible(true);
                                                        did = f.getLong(item);
                                                    } catch (Throwable ignored2) {}
                                                }
                                                try {
                                                    Field f = item.getClass().getDeclaredField("topicId");
                                                    f.setAccessible(true);
                                                    tid = f.getLong(item);
                                                } catch (Throwable ignored) {}
                                            }
                                            if (did != 0) {
                                                JSONObject obj = new JSONObject();
                                                obj.put("did", did);
                                                obj.put("tid", tid);
                                                arr.put(obj);
                                            }
                                        }

                                        for (Object arg : mArgs) {
                                            if (arg instanceof BaseFragment) {
                                                try {
                                                    ((BaseFragment) arg).finishFragment();
                                                } catch (Throwable ignored) {}
                                            }
                                        }
                                        try {
                                            fragment.finishFragment();
                                        } catch (Throwable ignored) {}

                                        if (callback != null && arr.length() > 0) {
                                            final String json = arr.toString();
                                            AndroidUtilities.runOnUIThread(new Runnable() {
                                                @Override
                                                public void run() {
                                                    callback.onChannelsPicked(json);
                                                }
                                            });
                                        }
                                    }
                                } catch (Throwable t) {
                                    Log.e(TAG, "didSelectDialogs error", t);
                                }
                                return true;
                            }
                            return null;
                        }
                    });

                    Method setDelegate = daClass.getMethod("setDelegate", delegateClass);
                    setDelegate.invoke(fragment, proxy);
                    currentFragment.presentFragment(fragment);
                } catch (Throwable t) {
                    Log.e(TAG, "pyOpenChannelPicker error", t);
                }
            }
        });
    }

    public static long pyGetMediaTransferBytes() {
        try {
            int account = UserConfig.selectedAccount;
            FileLoader fl = FileLoader.getInstance(account);
            long openDialogId = getOpenChatDialogId();

            long sum = 0;
            int activeCount = 0;

            // 1. Check incoming downloads (in open chat / channel)
            if (openDialogId != 0) {
                try {
                    Field f = FileLoader.class.getDeclaredField("loadOperationPaths");
                    f.setAccessible(true);
                    ConcurrentHashMap<?, ?> map = (ConcurrentHashMap<?, ?>) f.get(fl);
                    if (map != null && !map.isEmpty()) {
                        Field stateF = null;
                        Field pausedF = null;
                        Field bytesF = null;
                        Field parentF = null;

                        for (Object op : map.values()) {
                            if (op == null) continue;
                            Class<?> opClass = op.getClass();

                            if (stateF == null) {
                                try {
                                    stateF = opClass.getDeclaredField("state");
                                    stateF.setAccessible(true);
                                } catch (Throwable ignored) {}
                            }
                            if (stateF != null) {
                                int state = stateF.getInt(op);
                                // 1 is stateDownloading
                                if (state != 1) {
                                    continue;
                                }
                            }

                            if (pausedF == null) {
                                try {
                                    pausedF = opClass.getDeclaredField("paused");
                                    pausedF.setAccessible(true);
                                } catch (Throwable ignored) {}
                            }
                            if (pausedF != null && pausedF.getBoolean(op)) {
                                continue;
                            }

                            if (parentF == null) {
                                try {
                                    parentF = opClass.getDeclaredField("parentObject");
                                    parentF.setAccessible(true);
                                } catch (Throwable ignored) {}
                            }
                            if (parentF != null) {
                                Object parent = parentF.get(op);
                                if (parent != null) {
                                    long opDialogId = 0;
                                    if (parent instanceof MessageObject) {
                                        opDialogId = ((MessageObject) parent).getDialogId();
                                    } else {
                                        try {
                                            Method getDidM = parent.getClass().getMethod("getDialogId");
                                            opDialogId = (Long) getDidM.invoke(parent);
                                        } catch (Throwable ignored) {}
                                    }
                                    if (opDialogId != 0 && opDialogId != openDialogId) {
                                        // Belongs to background chat, not the open one
                                        continue;
                                    }
                                }
                            }

                            if (bytesF == null) {
                                bytesF = opClass.getDeclaredField("downloadedBytes");
                                bytesF.setAccessible(true);
                            }
                            sum += bytesF.getLong(op);
                            activeCount++;
                        }
                    }
                } catch (Throwable ignored) {}
            }

            // 2. Check outgoing uploads (media uploading to be sent: photos, videos, files, voice notes)
            try {
                String[] uploadFieldNames = new String[]{"uploadOperationPaths", "uploadOperationPathsEnc"};
                Field upStartedF = null;
                Field upStateF = null;
                Field upBytesF = null;
                Field upTotalF = null;

                for (String fieldName : uploadFieldNames) {
                    try {
                        Field f = FileLoader.class.getDeclaredField(fieldName);
                        f.setAccessible(true);
                        ConcurrentHashMap<?, ?> map = (ConcurrentHashMap<?, ?>) f.get(fl);
                        if (map == null || map.isEmpty()) continue;

                        for (Object op : map.values()) {
                            if (op == null) continue;
                            Class<?> opClass = op.getClass();

                            if (upStartedF == null) {
                                try {
                                    upStartedF = opClass.getDeclaredField("started");
                                    upStartedF.setAccessible(true);
                                } catch (Throwable ignored) {}
                            }
                            if (upStartedF != null && !upStartedF.getBoolean(op)) {
                                continue;
                            }

                            if (upStateF == null) {
                                try {
                                    upStateF = opClass.getDeclaredField("state");
                                    upStateF.setAccessible(true);
                                } catch (Throwable ignored) {}
                            }
                            if (upStateF != null) {
                                int state = upStateF.getInt(op);
                                // state 1 is active upload; state 2 is canceled
                                if (state != 1) continue;
                            }

                            if (upTotalF == null) {
                                try {
                                    upTotalF = opClass.getDeclaredField("totalFileSize");
                                    upTotalF.setAccessible(true);
                                } catch (Throwable ignored) {}
                            }
                            long total = (upTotalF != null) ? upTotalF.getLong(op) : 0;

                            if (upBytesF == null) {
                                try {
                                    upBytesF = opClass.getDeclaredField("uploadedBytesCount");
                                    upBytesF.setAccessible(true);
                                } catch (Throwable ignored) {}
                            }
                            long uploaded = (upBytesF != null) ? upBytesF.getLong(op) : 0;

                            if (total > 0 && uploaded >= total) {
                                continue; // upload already finished
                            }

                            sum += uploaded;
                            activeCount++;
                        }
                    } catch (Throwable ignored) {}
                }
            } catch (Throwable ignored) {}

            if (activeCount == 0) {
                return -1;
            }
            return sum;
        } catch (Throwable t) {
            return -1;
        }
    }

    public static long pyGetDownloadingBytes() {
        return pyGetMediaTransferBytes();
    }

    public static boolean pySwitchToNextProxy(final String cooldownJson, final int strategy, final String smartScoresJson) {
        try {
            ProxyController pc = ProxyController.getInstance();
            ArrayList<?> list = pc.getProxyList();
            if (list == null || list.size() <= 1) return false;

            Class<?> piClass = Class.forName("org.telegram.messenger.SharedConfig$ProxyInfo");
            Field settingsF = piClass.getField("settings");
            Field availF = piClass.getField("available");
            Field availTimeF = piClass.getField("availableCheckTime");

            Method curM = ProxyController.class.getMethod("getCurrentProxy");
            Object curPi = null;
            try { curPi = curM.invoke(pc); } catch (Throwable ignored) {}

            int curIndex = -1;
            if (curPi != null) {
                for (int i = 0; i < list.size(); i++) {
                    if (list.get(i) == curPi) {
                        curIndex = i;
                        break;
                    }
                }
            }

            java.util.HashSet<String> cooldownKeys = new java.util.HashSet<String>();
            if (cooldownJson != null && !cooldownJson.trim().isEmpty()) {
                try {
                    JSONArray arr = new JSONArray(cooldownJson);
                    for (int k = 0; k < arr.length(); k++) {
                        String s = arr.optString(k, "").trim().toLowerCase();
                        if (!s.isEmpty()) cooldownKeys.add(s);
                    }
                } catch (Throwable ignored) {}
            }

            JSONObject smartScores = null;
            if (smartScoresJson != null && !smartScoresJson.trim().isEmpty()) {
                try {
                    smartScores = new JSONObject(smartScoresJson);
                } catch (Throwable ignored) {}
            }

            ArrayList<Integer> candidates = new ArrayList<Integer>();
            for (int step = 1; step < list.size(); step++) {
                int nextIndex = (curIndex + step) % list.size();
                Object pi = list.get(nextIndex);
                if (pi == null || pi == curPi) continue;
                try {
                    ProxySettings ps = (ProxySettings) settingsF.get(pi);
                    if (ps == null || !ps.isValid()) continue;

                    String k1 = (ps.getAddress() + ":" + ps.getPort()).toLowerCase();
                    String k2 = ps.getAddress().toLowerCase();
                    if (cooldownKeys.contains(k1) || cooldownKeys.contains(k2)) continue;

                    long avTime = availTimeF.getLong(pi);
                    boolean av = availF.getBoolean(pi);
                    if (avTime > 0 && !av) continue;

                    candidates.add(nextIndex);
                } catch (Throwable ignored) {}
            }

            if (candidates.isEmpty()) {
                for (int step = 1; step < list.size(); step++) {
                    int nextIndex = (curIndex + step) % list.size();
                    Object pi = list.get(nextIndex);
                    if (pi == null || pi == curPi) continue;
                    try {
                        ProxySettings ps = (ProxySettings) settingsF.get(pi);
                        if (ps != null && ps.isValid()) candidates.add(nextIndex);
                    } catch (Throwable ignored) {}
                }
            }

            if (candidates.isEmpty()) return false;

            int chosenIndex = -1;
            if (strategy == 1) { // Random / Weighted random
                if (smartScores != null && smartScores.length() > 0) {
                    double totalWeight = 0.0;
                    double[] weights = new double[candidates.size()];
                    for (int i = 0; i < candidates.size(); i++) {
                        int idx = candidates.get(i);
                        Object pi = list.get(idx);
                        double sc = 1.0;
                        try {
                            ProxySettings ps = (ProxySettings) settingsF.get(pi);
                            if (ps != null) {
                                String k1 = (ps.getAddress() + ":" + ps.getPort()).toLowerCase();
                                String k2 = ps.getAddress().toLowerCase();
                                sc = smartScores.optDouble(k1, smartScores.optDouble(k2, 1.0));
                            }
                        } catch (Throwable ignored) {}
                        if (sc <= 0.05) sc = 0.05;
                        weights[i] = sc;
                        totalWeight += sc;
                    }
                    if (totalWeight > 0.0) {
                        double r = Math.random() * totalWeight;
                        double acc = 0.0;
                        for (int i = 0; i < candidates.size(); i++) {
                            acc += weights[i];
                            if (r <= acc) {
                                chosenIndex = candidates.get(i);
                                break;
                            }
                        }
                    }
                }
                if (chosenIndex == -1) {
                    int r = (int) (Math.random() * candidates.size());
                    chosenIndex = candidates.get(r);
                }
            } else if (strategy == 0) { // Lowest ping weighted by smart score
                double bestEffPing = Double.MAX_VALUE;
                for (int idx : candidates) {
                    Object pi = list.get(idx);
                    try {
                        long avTime = availTimeF.getLong(pi);
                        double basePing = (avTime > 0) ? (double) avTime : 400.0;
                        double sc = 1.0;
                        if (smartScores != null && smartScores.length() > 0) {
                            ProxySettings ps = (ProxySettings) settingsF.get(pi);
                            if (ps != null) {
                                String k1 = (ps.getAddress() + ":" + ps.getPort()).toLowerCase();
                                String k2 = ps.getAddress().toLowerCase();
                                sc = smartScores.optDouble(k1, smartScores.optDouble(k2, 1.0));
                            }
                        }
                        if (sc <= 0.05) sc = 0.05;
                        double effPing = basePing / sc;
                        if (effPing < bestEffPing) {
                            bestEffPing = effPing;
                            chosenIndex = idx;
                        }
                    } catch (Throwable ignored) {}
                }
                if (chosenIndex == -1) chosenIndex = candidates.get(0);
            } else { // Sequential (with smart preference)
                if (smartScores != null && smartScores.length() > 0) {
                    for (int idx : candidates) {
                        try {
                            Object pi = list.get(idx);
                            ProxySettings ps = (ProxySettings) settingsF.get(pi);
                            if (ps != null) {
                                String k1 = (ps.getAddress() + ":" + ps.getPort()).toLowerCase();
                                String k2 = ps.getAddress().toLowerCase();
                                double sc = smartScores.optDouble(k1, smartScores.optDouble(k2, 1.0));
                                if (sc >= 0.8) {
                                    chosenIndex = idx;
                                    break;
                                }
                            }
                        } catch (Throwable ignored) {}
                    }
                }
                if (chosenIndex == -1) chosenIndex = candidates.get(0);
            }

            Object nextPi = list.get(chosenIndex);
            Method setCurM = ProxyController.class.getMethod("setCurrentProxy", piClass);
            setCurM.invoke(pc, nextPi);
            pc.saveProxyList();
            try {
                ProxySettings ps = (ProxySettings) settingsF.get(nextPi);
                if (ps != null) {
                    applyProxySettings(ps);
                }
            } catch (Throwable ignored) {}
            return true;
        } catch (Throwable t) {
            Log.e(TAG, "pySwitchToNextProxy error", t);
            return false;
        }
    }

    public static boolean pySwitchToNextProxy(final String cooldownJson, final int strategy) {
        return pySwitchToNextProxy(cooldownJson, strategy, null);
    }

    public static boolean pySwitchToNextProxy() {
        return pySwitchToNextProxy(null, 0, null);
    }

    public static int pyEnforceProxyLimit(final int maxCount, final int policy, final String recentlyAddedJson) {
        if (maxCount <= 0) return 0;
        try {
            ProxyController pc = ProxyController.getInstance();
            ArrayList<?> list = pc.getProxyList();
            if (list == null || list.size() <= maxCount) return 0;

            Class<?> piClass = Class.forName("org.telegram.messenger.SharedConfig$ProxyInfo");
            Field settingsF = piClass.getField("settings");
            Field availF = piClass.getField("available");
            Field availTimeF = piClass.getField("availableCheckTime");

            Method delM = ProxyController.class.getMethod("deleteProxy", piClass);
            Method curM = ProxyController.class.getMethod("getCurrentProxy");
            Object curPi = null;
            try { curPi = curM.invoke(pc); } catch (Throwable ignored) {}

            java.util.HashSet<String> protectedKeys = new java.util.HashSet<String>();
            if (recentlyAddedJson != null && !recentlyAddedJson.trim().isEmpty()) {
                try {
                    JSONArray arr = new JSONArray(recentlyAddedJson);
                    for (int k = 0; k < arr.length(); k++) {
                        String s = arr.optString(k, "").trim().toLowerCase();
                        if (!s.isEmpty()) protectedKeys.add(s);
                    }
                } catch (Throwable ignored) {}
            }

            int deleted = 0;

            if (policy == 0) {
                // Policy 0: Replace dead first, then slowest
                for (int i = list.size() - 1; i >= 0 && list.size() > maxCount; i--) {
                    Object pi = list.get(i);
                    if (pi == null || pi == curPi) continue;
                    try {
                        ProxySettings ps = (ProxySettings) settingsF.get(pi);
                        if (ps != null) {
                            String k1 = (ps.getAddress() + ":" + ps.getPort()).toLowerCase();
                            String k2 = ps.getAddress().toLowerCase();
                            if (protectedKeys.contains(k1) || protectedKeys.contains(k2)) continue;
                        }
                        boolean isDead = false;
                        long avTime = availTimeF.getLong(pi);
                        boolean av = availF.getBoolean(pi);
                        if (avTime > 0 && !av) isDead = true;

                        if (isDead) {
                            delM.invoke(pc, pi);
                            deleted++;
                        }
                    } catch (Throwable ignored) {}
                }

                if (list.size() > maxCount) {
                    ArrayList<Object> sorted = new ArrayList<Object>(list);
                    Collections.sort(sorted, new Comparator<Object>() {
                        @Override
                        public int compare(Object a, Object b) {
                            long pA = 0, pB = 0;
                            try { pA = availTimeF.getLong(a); } catch (Throwable ignored) {}
                            try { pB = availTimeF.getLong(b); } catch (Throwable ignored) {}
                            return Long.compare(pB, pA);
                        }
                    });

                    for (Object pi : sorted) {
                        if (list.size() <= maxCount) break;
                        if (pi == null || pi == curPi) continue;
                        try {
                            ProxySettings ps = (ProxySettings) settingsF.get(pi);
                            if (ps != null) {
                                String k1 = (ps.getAddress() + ":" + ps.getPort()).toLowerCase();
                                String k2 = ps.getAddress().toLowerCase();
                                if (protectedKeys.contains(k1) || protectedKeys.contains(k2)) continue;
                            }
                            delM.invoke(pc, pi);
                            deleted++;
                        } catch (Throwable ignored) {}
                    }
                }
            } else {
                // Policy 1: Replace oldest (from head of list, skipping protected & current)
                for (int i = 0; i < list.size() && list.size() > maxCount; i++) {
                    Object pi = list.get(i);
                    if (pi == null || pi == curPi) continue;
                    try {
                        ProxySettings ps = (ProxySettings) settingsF.get(pi);
                        if (ps != null) {
                            String k1 = (ps.getAddress() + ":" + ps.getPort()).toLowerCase();
                            String k2 = ps.getAddress().toLowerCase();
                            if (protectedKeys.contains(k1) || protectedKeys.contains(k2)) continue;
                        }
                        delM.invoke(pc, pi);
                        deleted++;
                        i--;
                    } catch (Throwable ignored) {}
                }
            }

            if (deleted > 0) {
                pc.saveProxyList();
            }
            return deleted;
        } catch (Throwable t) {
            Log.e(TAG, "pyEnforceProxyLimit error", t);
            return 0;
        }
    }

    private static ProxySettings findFirstAliveProxy(int timeoutMs) {
        try {
            ProxyController pc = ProxyController.getInstance();
            ArrayList<?> list = pc.getProxyList();
            if (list == null || list.isEmpty()) return null;
            Class<?> piClass = Class.forName("org.telegram.messenger.SharedConfig$ProxyInfo");
            Field settingsF = piClass.getField("settings");
            Field availF = piClass.getField("available");
            Field availTimeF = piClass.getField("availableCheckTime");

            for (int i = 0; i < list.size(); i++) {
                Object pi = list.get(i);
                if (pi == null) continue;
                try {
                    boolean av = availF.getBoolean(pi);
                    long avTime = availTimeF.getLong(pi);
                    if (av && avTime > 0 && (SystemClock.elapsedRealtime() - avTime < 180000)) {
                        ProxySettings ps = (ProxySettings) settingsF.get(pi);
                        if (ps != null && ps.isValid()) return ps;
                    }
                } catch (Throwable ignored) {}
            }

            for (int i = 0; i < list.size(); i++) {
                Object pi = list.get(i);
                if (pi == null) continue;
                ProxySettings ps = (ProxySettings) settingsF.get(pi);
                if (ps != null && ps.isValid()) {
                    try {
                        boolean av = availF.getBoolean(pi);
                        long avTime = availTimeF.getLong(pi);
                        if (!av && avTime > 0 && (SystemClock.elapsedRealtime() - avTime < 180000)) {
                            continue;
                        }
                    } catch (Throwable ignored) {}

                    String t = ps.getType() == ProxySettings.Type.WEB ? "WebProxy" : (ps.getType() == ProxySettings.Type.SOCKS5 ? "SOCKS5" : "MTProto");
                    int port = ps.getPort() > 0 ? ps.getPort() : 443;
                    int ping = pyCheckProxy(t, ps.getAddress(), port, timeoutMs);
                    if (ping >= 0) {
                        return ps;
                    }
                }
            }
        } catch (Throwable t) {
            Log.e(TAG, "findFirstAliveProxy error", t);
        }
        return null;
    }

    private static void applyProxySettings(ProxySettings settings) {
        try {
            ProxyController pc = ProxyController.getInstance();
            Class<?> piClass = Class.forName("org.telegram.messenger.SharedConfig$ProxyInfo");
            Constructor<?> ctor = piClass.getConstructor(ProxySettings.class);
            Object proxyInfo = ctor.newInstance(settings);
            Method setCurM = ProxyController.class.getMethod("setCurrentProxy", piClass);
            setCurM.invoke(pc, proxyInfo);
            pc.saveProxyList();
        } catch (Throwable t) {
            Log.e(TAG, "applyProxySettings error", t);
        }
    }

    public static boolean pyAddProxy(String link) {
        try {
            if (link == null || link.isEmpty()) return false;
            Uri uri = Uri.parse(link);
            ProxySettings settings = ProxySettings.fromUri(uri);
            if (settings != null && settings.isValid()) {
                Class<?> piClass = Class.forName("org.telegram.messenger.SharedConfig$ProxyInfo");
                Constructor<?> ctor = piClass.getConstructor(ProxySettings.class);
                Object proxyInfo = ctor.newInstance(settings);
                ProxyController pc = ProxyController.getInstance();
                Method addM = ProxyController.class.getMethod("addProxy", piClass);
                addM.invoke(pc, proxyInfo);
                pc.saveProxyList();
                return true;
            }
        } catch (Throwable t) {
            Log.e(TAG, "pyAddProxy error", t);
        }
        return false;
    }

    public static boolean pyIsProxyAdded(String link) {
        try {
            if (link == null || link.isEmpty()) return false;
            Uri uri = Uri.parse(link);
            ProxySettings target = ProxySettings.fromUri(uri);
            if (target == null || !target.isValid()) return false;

            ProxyController pc = ProxyController.getInstance();
            ArrayList<?> list = pc.getProxyList();
            if (list == null || list.isEmpty()) return false;

            Class<?> piClass = Class.forName("org.telegram.messenger.SharedConfig$ProxyInfo");
            Field settingsF = piClass.getField("settings");
            String tAddr = target.getAddress();
            int tPort = target.getPort();
            ProxySettings.Type tType = target.getType();

            for (int i = 0; i < list.size(); i++) {
                Object pi = list.get(i);
                if (pi == null) continue;
                ProxySettings ps = (ProxySettings) settingsF.get(pi);
                if (ps != null && ps.getAddress() != null) {
                    boolean match = false;
                    if (tType == ProxySettings.Type.WEB && ps.getType() == ProxySettings.Type.WEB) {
                        match = ps.getAddress().equalsIgnoreCase(tAddr);
                    } else {
                        match = (ps.getAddress().equalsIgnoreCase(tAddr) && ps.getPort() == tPort);
                    }
                    if (match) {
                        return true;
                    }
                }
            }
        } catch (Throwable t) {
            Log.e(TAG, "pyIsProxyAdded error", t);
        }
        return false;
    }

    public static boolean pyRemoveProxyByLink(String link) {
        try {
            if (link == null || link.isEmpty()) return false;
            Uri uri = Uri.parse(link);
            ProxySettings target = ProxySettings.fromUri(uri);
            if (target == null || !target.isValid()) return false;

            ProxyController pc = ProxyController.getInstance();
            ArrayList<?> list = pc.getProxyList();
            if (list == null || list.isEmpty()) return false;

            Class<?> piClass = Class.forName("org.telegram.messenger.SharedConfig$ProxyInfo");
            Field settingsF = piClass.getField("settings");
            Method delM = ProxyController.class.getMethod("deleteProxy", piClass);
            String tAddr = target.getAddress();
            int tPort = target.getPort();
            ProxySettings.Type tType = target.getType();

            boolean deleted = false;
            for (int i = 0; i < list.size(); i++) {
                Object pi = list.get(i);
                if (pi == null) continue;
                ProxySettings ps = (ProxySettings) settingsF.get(pi);
                if (ps != null && ps.getAddress() != null) {
                    boolean match = false;
                    if (tType == ProxySettings.Type.WEB && ps.getType() == ProxySettings.Type.WEB) {
                        match = ps.getAddress().equalsIgnoreCase(tAddr);
                    } else {
                        match = (ps.getAddress().equalsIgnoreCase(tAddr) && ps.getPort() == tPort);
                    }
                    if (match) {
                        delM.invoke(pc, pi);
                        deleted = true;
                        break;
                    }
                }
            }
            if (deleted) {
                pc.saveProxyList();
                return true;
            }
        } catch (Throwable t) {
            Log.e(TAG, "pyRemoveProxyByLink error", t);
        }
        return false;
    }

    public static int pyAddAllProxies(String jsonLinks) {
        int added = 0;
        try {
            if (jsonLinks == null || jsonLinks.isEmpty()) return 0;
            JSONArray arr = new JSONArray(jsonLinks);
            ProxyController pc = ProxyController.getInstance();
            Class<?> piClass = Class.forName("org.telegram.messenger.SharedConfig$ProxyInfo");
            Constructor<?> ctor = piClass.getConstructor(ProxySettings.class);
            Method addM = ProxyController.class.getMethod("addProxy", piClass);
            Field settingsF = piClass.getField("settings");

            ArrayList<?> existing = pc.getProxyList();

            for (int i = 0; i < arr.length(); i++) {
                String link = arr.optString(i, "");
                if (link.isEmpty()) continue;
                Uri uri = Uri.parse(link);
                ProxySettings settings = ProxySettings.fromUri(uri);
                if (settings != null && settings.isValid()) {
                    boolean exists = false;
                    if (existing != null) {
                        for (int j = 0; j < existing.size(); j++) {
                            Object epi = existing.get(j);
                            if (epi == null) continue;
                            ProxySettings eps = (ProxySettings) settingsF.get(epi);
                            if (eps != null && eps.getAddress() != null) {
                                if (settings.getType() == ProxySettings.Type.WEB && eps.getType() == ProxySettings.Type.WEB) {
                                    if (eps.getAddress().equalsIgnoreCase(settings.getAddress())) {
                                        exists = true;
                                        break;
                                    }
                                } else if (eps.getAddress().equalsIgnoreCase(settings.getAddress()) &&
                                    eps.getPort() == settings.getPort()) {
                                    exists = true;
                                    break;
                                }
                            }
                        }
                    }
                    if (!exists) {
                        Object proxyInfo = ctor.newInstance(settings);
                        addM.invoke(pc, proxyInfo);
                        added++;
                    }
                }
            }
            if (added > 0) {
                pc.saveProxyList();
            }
        } catch (Throwable t) {
            Log.e(TAG, "pyAddAllProxies error", t);
        }
        return added;
    }

    public static void pyOpenProxyInTelegram(Context context, String link) {
        try {
            if (link == null || link.isEmpty() || context == null) return;
            Browser.openUrl(context, link);
        } catch (Throwable t) {
            try {
                Uri uri = Uri.parse(link);
                Intent intent = new Intent(Intent.ACTION_VIEW, uri);
                intent.setPackage(context.getPackageName());
                if (!(context instanceof Activity)) {
                    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                }
                context.startActivity(intent);
            } catch (Throwable ignored) {}
        }
    }

    public static boolean pyApplyProxy(String link) {
        try {
            if (link == null || link.isEmpty()) return false;
            Uri uri = Uri.parse(link);
            ProxySettings settings = ProxySettings.fromUri(uri);
            if (settings != null && settings.isValid()) {
                Class<?> piClass = Class.forName("org.telegram.messenger.SharedConfig$ProxyInfo");
                Constructor<?> ctor = piClass.getConstructor(ProxySettings.class);
                Object proxyInfo = ctor.newInstance(settings);
                ProxyController pc = ProxyController.getInstance();
                Method addM = ProxyController.class.getMethod("addProxy", piClass);
                Object addedInfo = addM.invoke(pc, proxyInfo);
                Method setCurM = ProxyController.class.getMethod("setCurrentProxy", piClass);
                setCurM.invoke(pc, addedInfo != null ? addedInfo : proxyInfo);
                pc.saveProxyList();
                ConnectionsManager.setProxySettings(true, settings);
                return true;
            }
        } catch (Throwable t) {
            Log.e(TAG, "pyApplyProxy error", t);
        }
        return false;
    }

    public static int pyCheckProxy(String type, String host, int port, int timeoutMs) {
        Socket socket = null;
        try {
            int checkPort = (port > 0) ? port : 443;
            long startTime = System.currentTimeMillis();
            socket = new Socket();
            socket.setSoTimeout(timeoutMs);
            socket.connect(new InetSocketAddress(host, checkPort), timeoutMs);
            long ping = System.currentTimeMillis() - startTime;

            if ("SOCKS5".equalsIgnoreCase(type)) {
                OutputStream os = socket.getOutputStream();
                InputStream is = socket.getInputStream();
                os.write(new byte[]{0x05, 0x01, 0x00});
                os.flush();
                byte[] response = new byte[2];
                int read = is.read(response);
                if (read >= 2 && response[0] == 0x05) {
                    return (int) ping;
                }
                return -1;
            } else {
                return (int) ping;
            }
        } catch (Throwable t) {
            return -1;
        } finally {
            if (socket != null) {
                try {
                    socket.close();
                } catch (Throwable ignored) {}
            }
        }
    }

    public static int pyCheckProxy(String type, String host, long port, long timeoutMs) {
        return pyCheckProxy(type, host, (int) port, (int) timeoutMs);
    }

    public static String pyResolveCountry(String host) {
        if (host == null || host.trim().isEmpty()) {
            return "{}";
        }
        String h = host.trim().toLowerCase();
        String cached = GEO_CACHE.get(h);
        if (cached != null) return cached;

        String ip = h;
        try {
            InetAddress addr = InetAddress.getByName(ip);
            if (addr != null) {
                ip = addr.getHostAddress();
                cached = GEO_CACHE.get(ip);
                if (cached != null) {
                    GEO_CACHE.put(h, cached);
                    return cached;
                }
            }
        } catch (Throwable ignored) {}

        // 1. Try https://api.country.is/{ip} (HTTPS, fast, no rate limit)
        HttpURLConnection conn = null;
        try {
            URL url = new URL("https://api.country.is/" + ip);
            conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(2000);
            conn.setReadTimeout(2000);
            conn.setRequestProperty("User-Agent", "Mozilla/5.0");
            if (conn.getResponseCode() == 200) {
                BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream(), "UTF-8"));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = reader.readLine()) != null) sb.append(line);
                reader.close();
                JSONObject json = new JSONObject(sb.toString());
                String code = json.optString("country", "").trim().toUpperCase();
                if (!code.isEmpty() && code.length() == 2 && !"ZZ".equals(code)) {
                    String name = "";
                    try {
                        Locale loc = new Locale("", code);
                        name = loc.getDisplayCountry(new Locale("ru"));
                        if (name == null || name.isEmpty() || name.equalsIgnoreCase(code)) {
                            name = loc.getDisplayCountry(Locale.ENGLISH);
                        }
                    } catch (Throwable ignored) {}
                    if (name == null || name.isEmpty()) name = code;

                    JSONObject out = new JSONObject();
                    out.put("country", name);
                    out.put("country_code", code);
                    String resStr = out.toString();
                    GEO_CACHE.put(h, resStr);
                    GEO_CACHE.put(ip, resStr);
                    return resStr;
                }
            }
        } catch (Throwable ignored) {
        } finally {
            if (conn != null) {
                try { conn.disconnect(); } catch (Throwable ignored) {}
            }
        }

        // 2. Try https://freeipapi.com/api/json/{ip} (HTTPS)
        try {
            URL url = new URL("https://freeipapi.com/api/json/" + ip);
            conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(2500);
            conn.setReadTimeout(2500);
            conn.setRequestProperty("User-Agent", "Mozilla/5.0");
            if (conn.getResponseCode() == 200) {
                BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream(), "UTF-8"));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = reader.readLine()) != null) sb.append(line);
                reader.close();
                JSONObject json = new JSONObject(sb.toString());
                String code = json.optString("countryCode", "").trim().toUpperCase();
                String countryName = json.optString("countryName", "").trim();
                if (!code.isEmpty() && code.length() == 2) {
                    try {
                        Locale loc = new Locale("", code);
                        String ruName = loc.getDisplayCountry(new Locale("ru"));
                        if (ruName != null && !ruName.isEmpty() && !ruName.equalsIgnoreCase(code)) {
                            countryName = ruName;
                        }
                    } catch (Throwable ignored) {}

                    JSONObject out = new JSONObject();
                    out.put("country", countryName);
                    out.put("country_code", code);
                    String resStr = out.toString();
                    GEO_CACHE.put(h, resStr);
                    GEO_CACHE.put(ip, resStr);
                    return resStr;
                }
            }
        } catch (Throwable ignored) {
        } finally {
            if (conn != null) {
                try { conn.disconnect(); } catch (Throwable ignored) {}
            }
        }

        return "{}";
    }

    public static String pyBatchCheck(String jsonProxies, int timeoutMs) {
        try {
            JSONArray arr = new JSONArray(jsonProxies);
            int len = arr.length();
            if (len == 0) return "[]";

            int threads = Math.min(16, Math.max(2, len));
            ExecutorService executor = Executors.newFixedThreadPool(threads);
            List<Future<JSONObject>> futures = new ArrayList<Future<JSONObject>>();

            for (int i = 0; i < len; i++) {
                final JSONObject item = arr.getJSONObject(i);
                final int finalTimeout = timeoutMs;
                futures.add(executor.submit(new Callable<JSONObject>() {
                    @Override
                    public JSONObject call() {
                        try {
                            String type = item.optString("type", "MTProto");
                            String server = item.optString("server", "");
                            int port = item.optInt("port", 0);
                            if (port <= 0 && "WebProxy".equalsIgnoreCase(type)) {
                                port = 443;
                            }
                            if (server.isEmpty() || port <= 0) return null;

                            int ping = pyCheckProxy(type, server, port, finalTimeout);
                            if (ping >= 0) {
                                JSONObject res = new JSONObject(item.toString());
                                res.put("ping", ping);
                                return res;
                            }
                        } catch (Throwable ignored) {}
                        return null;
                    }
                }));
            }

            executor.shutdown();

            List<JSONObject> alive = new ArrayList<JSONObject>();
            for (Future<JSONObject> f : futures) {
                try {
                    JSONObject res = f.get();
                    if (res != null) {
                        alive.add(res);
                    }
                } catch (Throwable ignored) {}
            }

            Collections.sort(alive, new Comparator<JSONObject>() {
                @Override
                public int compare(JSONObject a, JSONObject b) {
                    return Integer.compare(a.optInt("ping", 99999), b.optInt("ping", 99999));
                }
            });

            for (JSONObject o : alive) {
                String server = o.optString("server", "");
                if (!server.isEmpty() && !o.has("country")) {
                    String geoStr = pyResolveCountry(server);
                    try {
                        JSONObject geoJson = new JSONObject(geoStr);
                        o.put("country", geoJson.optString("country", "Unknown"));
                        o.put("country_code", geoJson.optString("country_code", ""));
                    } catch (Throwable ignored) {}
                }
            }

            JSONArray out = new JSONArray();
            for (JSONObject o : alive) {
                out.put(o);
            }
            return out.toString();
        } catch (Throwable t) {
            Log.e(TAG, "pyBatchCheck error", t);
            return "[]";
        }
    }

    public static String pyBatchCheck(String jsonProxies, long timeoutMs) {
        return pyBatchCheck(jsonProxies, (int) timeoutMs);
    }

    public static String pyExportProxies() {
        try {
            ProxyController pc = ProxyController.getInstance();
            ArrayList<?> list = pc.getProxyList();
            if (list == null || list.isEmpty()) return "[]";
            JSONArray arr = new JSONArray();
            Method buildShareLinkM = null;
            try {
                Class<?> piClass = Class.forName("org.telegram.messenger.SharedConfig$ProxyInfo");
                buildShareLinkM = ProxyController.class.getMethod("buildShareLink", piClass);
            } catch (Throwable ignored) {}

            Class<?> piClass = Class.forName("org.telegram.messenger.SharedConfig$ProxyInfo");
            Field settingsF = piClass.getField("settings");

            for (int i = 0; i < list.size(); i++) {
                Object pi = list.get(i);
                if (pi == null) continue;
                try {
                    ProxySettings ps = (ProxySettings) settingsF.get(pi);
                    if (ps == null) continue;
                    JSONObject obj = new JSONObject();
                    String typeName = "MTProto";
                    if (ps.getType() == ProxySettings.Type.WEB) {
                        typeName = "WebProxy";
                    } else if (ps.getType() == ProxySettings.Type.SOCKS5) {
                        typeName = "SOCKS5";
                    }
                    obj.put("type", typeName);
                    obj.put("server", ps.getAddress() != null ? ps.getAddress() : "");
                    obj.put("port", ps.getPort() > 0 ? ps.getPort() : 443);
                    obj.put("secret", ps.getSecret() != null ? ps.getSecret() : "");
                    obj.put("user", ps.getUser() != null ? ps.getUser() : "");
                    obj.put("pass", ps.getPassword() != null ? ps.getPassword() : "");
                    String link = null;
                    if (buildShareLinkM != null) {
                        try {
                            link = (String) buildShareLinkM.invoke(pc, pi);
                        } catch (Throwable ignored) {}
                    }
                    if (link == null || link.isEmpty()) {
                        link = ps.getLink();
                    }
                    if ((link == null || link.isEmpty()) && ps.getType() == ProxySettings.Type.WEB) {
                        link = "https://t.me/webproxy?server=" + ps.getAddress() + "&secret=" + ps.getSecret();
                    }
                    obj.put("link", link != null ? link : "");
                    arr.put(obj);
                } catch (Throwable ignored) {}
            }
            return arr.toString();
        } catch (Throwable t) {
            Log.e(TAG, "pyExportProxies error", t);
            return "[]";
        }
    }

    public interface ForumTopicsCallback {
        void onTopics(String jsonTopics);
    }

    public static void pyGetForumTopics(final long chatId, final ForumTopicsCallback callback) {
        AndroidUtilities.runOnUIThread(new Runnable() {
            @Override
            public void run() {
                try {
                    int account = UserConfig.selectedAccount;
                    org.telegram.messenger.MessagesController mc = org.telegram.messenger.MessagesController.getInstance(account);
                    final long cId = chatId < 0 ? -chatId : chatId;
                    org.telegram.messenger.TopicsController tc = mc.getTopicsController();
                    ArrayList<?> list = tc != null ? tc.getTopics(cId) : null;
                    if (list != null && !list.isEmpty()) {
                        if (callback != null) callback.onTopics(serializeForumTopics(list));
                        return;
                    }
                    if (tc != null) {
                        try {
                            tc.loadTopics(cId);
                        } catch (Throwable ignored) {}
                    }

                    try {
                        Class<?> reqCls = null;
                        try {
                            reqCls = Class.forName("org.telegram.tgnet.tl.TL_forum$TL_messages_getForumTopics");
                        } catch (Throwable t1) {
                            try {
                                reqCls = Class.forName("org.telegram.tgnet.TLRPC$TL_messages_getForumTopics");
                            } catch (Throwable ignored) {}
                        }
                        if (reqCls != null) {
                            Object req = reqCls.newInstance();
                            Field peerF = reqCls.getField("peer");
                            peerF.set(req, mc.getInputPeer(-cId));
                            try {
                                Field limF = reqCls.getField("limit");
                                limF.setInt(req, 100);
                            } catch (Throwable ignored) {}

                            ConnectionsManager.getInstance(account).sendRequest((org.telegram.tgnet.TLObject) req, new org.telegram.tgnet.RequestDelegate() {
                                @Override
                                public void run(final org.telegram.tgnet.TLObject response, final org.telegram.tgnet.TLRPC.TL_error error) {
                                    AndroidUtilities.runOnUIThread(new Runnable() {
                                        @Override
                                        public void run() {
                                            try {
                                                if (response != null) {
                                                    Field tF = response.getClass().getField("topics");
                                                    ArrayList<?> tList = (ArrayList<?>) tF.get(response);
                                                    if (tList != null && !tList.isEmpty()) {
                                                        if (callback != null) callback.onTopics(serializeForumTopics(tList));
                                                        return;
                                                    }
                                                }
                                            } catch (Throwable t) {
                                                Log.e(TAG, "sendRequest getForumTopics error", t);
                                            }
                                            try {
                                                org.telegram.messenger.TopicsController tc2 = org.telegram.messenger.MessagesController.getInstance(UserConfig.selectedAccount).getTopicsController();
                                                ArrayList<?> l2 = tc2 != null ? tc2.getTopics(cId) : null;
                                                if (callback != null) callback.onTopics(serializeForumTopics(l2));
                                            } catch (Throwable ignored) {
                                                if (callback != null) callback.onTopics("[]");
                                            }
                                        }
                                    });
                                }
                            });
                            return;
                        }
                    } catch (Throwable t) {
                        Log.e(TAG, "getForumTopics sendRequest error", t);
                    }

                    AndroidUtilities.runOnUIThread(new Runnable() {
                        @Override
                        public void run() {
                            try {
                                org.telegram.messenger.TopicsController tc2 = org.telegram.messenger.MessagesController.getInstance(UserConfig.selectedAccount).getTopicsController();
                                ArrayList<?> l2 = tc2 != null ? tc2.getTopics(cId) : null;
                                if (callback != null) callback.onTopics(serializeForumTopics(l2));
                            } catch (Throwable ignored) {
                                if (callback != null) callback.onTopics("[]");
                            }
                        }
                    }, 400);
                } catch (Throwable t) {
                    Log.e(TAG, "pyGetForumTopics error", t);
                    if (callback != null) callback.onTopics("[]");
                }
            }
        });
    }

    private static String serializeForumTopics(ArrayList<?> topics) {
        JSONArray arr = new JSONArray();
        if (topics != null) {
            for (Object obj : topics) {
                if (obj == null) continue;
                try {
                    Field idF = obj.getClass().getField("id");
                    Field titleF = obj.getClass().getField("title");
                    int id = idF.getInt(obj);
                    String title = (String) titleF.get(obj);
                    JSONObject o = new JSONObject();
                    o.put("id", id);
                    o.put("title", title != null ? title : ("Topic #" + id));
                    arr.put(o);
                } catch (Throwable ignored) {}
            }
        }
        return arr.toString();
    }

    public static String pyRemoveDeadProxies(final int timeoutMs, final boolean safetyNoWipe, final String protectedKeysJson) {
        try {
            boolean proxyEnabled = false;
            try {
                proxyEnabled = SharedConfig.isProxyEnabled();
            } catch (Throwable ignored) {}
            if (!proxyEnabled) {
                JSONObject res = new JSONObject();
                res.put("deleted", 0);
                res.put("kept", 0);
                res.put("aborted", true);
                res.put("reason", "proxy_disabled");
                return res.toString();
            }

            final ProxyController pc = ProxyController.getInstance();
            ArrayList<?> list = pc.getProxyList();
            if (list == null || list.isEmpty()) {
                JSONObject res = new JSONObject();
                res.put("deleted", 0);
                res.put("kept", 0);
                return res.toString();
            }

            final java.util.HashSet<String> protectedKeys = new java.util.HashSet<String>();
            if (protectedKeysJson != null && !protectedKeysJson.trim().isEmpty()) {
                try {
                    JSONArray arr = new JSONArray(protectedKeysJson);
                    for (int k = 0; k < arr.length(); k++) {
                        String s = arr.optString(k, "").trim().toLowerCase();
                        if (!s.isEmpty()) protectedKeys.add(s);
                    }
                } catch (Throwable ignored) {}
            }

            final ArrayList<Object> snapshot = new ArrayList<Object>(list);
            int threads = Math.min(snapshot.size(), 16);
            ExecutorService pool = Executors.newFixedThreadPool(threads > 0 ? threads : 1);
            List<Future<Boolean>> futures = new ArrayList<Future<Boolean>>();
            final Class<?> piClass = Class.forName("org.telegram.messenger.SharedConfig$ProxyInfo");
            final Field settingsF = piClass.getField("settings");
            final Field availF = piClass.getField("available");
            final Field availTimeF = piClass.getField("availableCheckTime");

            for (final Object pi : snapshot) {
                futures.add(pool.submit(new Callable<Boolean>() {
                    @Override
                    public Boolean call() {
                        try {
                            ProxySettings ps = (ProxySettings) settingsF.get(pi);
                            if (ps == null || !ps.isValid()) return false;

                            String key1 = (ps.getAddress() + ":" + ps.getPort()).toLowerCase();
                            String key2 = ps.getAddress().toLowerCase();
                            if (protectedKeys.contains(key1) || protectedKeys.contains(key2)) {
                                return true;
                            }

                            try {
                                boolean av = availF.getBoolean(pi);
                                long avTime = availTimeF.getLong(pi);
                                if (avTime > 0 && !av) {
                                    return false; // Telegram says unavailable -> dead!
                                }
                            } catch (Throwable ignored) {}

                            String type = ps.getType() == ProxySettings.Type.WEB ? "WebProxy" : (ps.getType() == ProxySettings.Type.SOCKS5 ? "SOCKS5" : "MTProto");
                            int port = ps.getPort() > 0 ? ps.getPort() : 443;
                            int ping = pyCheckProxy(type, ps.getAddress(), port, timeoutMs);
                            return ping >= 0;
                        } catch (Throwable ignored) {
                            return false;
                        }
                    }
                }));
            }
            pool.shutdown();

            int aliveCount = 0;
            for (int i = 0; i < snapshot.size(); i++) {
                try {
                    if (futures.get(i).get()) aliveCount++;
                } catch (Throwable ignored) {}
            }

            if (safetyNoWipe && aliveCount == 0 && snapshot.size() > 1) {
                JSONObject res = new JSONObject();
                res.put("deleted", 0);
                res.put("kept", snapshot.size());
                res.put("aborted", true);
                res.put("reason", "all_failed");
                return res.toString();
            }

// Removed premature mass_failure abort so actual dead proxies can be cleaned

            int deleted = 0;
            int kept = 0;
            Method delM = ProxyController.class.getMethod("deleteProxy", piClass);
            for (int i = 0; i < snapshot.size(); i++) {
                Object pi = snapshot.get(i);
                boolean alive = false;
                try {
                    alive = futures.get(i).get();
                } catch (Throwable ignored) {}
                if (alive) {
                    kept++;
                } else {
                    delM.invoke(pc, pi);
                    deleted++;
                }
            }
            pc.saveProxyList();
            JSONObject res = new JSONObject();
            res.put("deleted", deleted);
            res.put("kept", kept);
            return res.toString();
        } catch (Throwable t) {
            Log.e(TAG, "pyRemoveDeadProxies error", t);
            return "{\"error\":\"" + t.getMessage() + "\"}";
        }
    }

    public static String pyRemoveDeadProxies(final int timeoutMs, final boolean safetyNoWipe) {
        return pyRemoveDeadProxies(timeoutMs, safetyNoWipe, null);
    }

    public static String pyRemoveDeadProxies(final int timeoutMs) {
        return pyRemoveDeadProxies(timeoutMs, false, null);
    }

    public static String pyRemoveDeadProxies(final long timeoutMs) {
        return pyRemoveDeadProxies((int) timeoutMs, false, null);
    }

    public static String pyRemoveDeadProxies(final long timeoutMs, final boolean safetyNoWipe) {
        return pyRemoveDeadProxies((int) timeoutMs, safetyNoWipe, null);
    }

    public static String pyRemoveDeadProxies(final long timeoutMs, final boolean safetyNoWipe, final String protectedKeysJson) {
        return pyRemoveDeadProxies((int) timeoutMs, safetyNoWipe, protectedKeysJson);
    }

    public static String pyKeepTopProxies(final int keepCount, final int timeoutMs) {
        try {
            boolean proxyEnabled = false;
            try {
                proxyEnabled = SharedConfig.isProxyEnabled();
            } catch (Throwable ignored) {}
            if (!proxyEnabled) {
                JSONObject res = new JSONObject();
                res.put("deleted", 0);
                res.put("kept", 0);
                res.put("aborted", true);
                return res.toString();
            }

            final ProxyController pc = ProxyController.getInstance();
            ArrayList<?> list = pc.getProxyList();
            if (list == null || list.isEmpty()) {
                JSONObject res = new JSONObject();
                res.put("deleted", 0);
                res.put("kept", 0);
                return res.toString();
            }

            final ArrayList<Object> snapshot = new ArrayList<Object>(list);
            int threads = Math.min(snapshot.size(), 16);
            ExecutorService pool = Executors.newFixedThreadPool(threads > 0 ? threads : 1);
            final Class<?> piClass = Class.forName("org.telegram.messenger.SharedConfig$ProxyInfo");
            final Field settingsF = piClass.getField("settings");
            final Field availF = piClass.getField("available");
            final Field availTimeF = piClass.getField("availableCheckTime");
            final Field pingF = piClass.getField("ping");

            final class TestedProxy {
                Object pi;
                int ping;
                TestedProxy(Object p, int ping) { this.pi = p; this.ping = ping; }
            }

            List<Future<TestedProxy>> futures = new ArrayList<Future<TestedProxy>>();
            for (final Object pi : snapshot) {
                futures.add(pool.submit(new Callable<TestedProxy>() {
                    @Override
                    public TestedProxy call() {
                        try {
                            ProxySettings ps = (ProxySettings) settingsF.get(pi);
                            if (ps == null || !ps.isValid()) return new TestedProxy(pi, -1);

                            try {
                                boolean av = availF.getBoolean(pi);
                                long avTime = availTimeF.getLong(pi);
                                long p = pingF.getLong(pi);
                                if (avTime > 0) {
                                    if (!av) {
                                        return new TestedProxy(pi, -1);
                                    } else {
                                        return new TestedProxy(pi, (int) Math.max(1, p));
                                    }
                                }
                            } catch (Throwable ignored) {}

                            String type = ps.getType() == ProxySettings.Type.WEB ? "WebProxy" : (ps.getType() == ProxySettings.Type.SOCKS5 ? "SOCKS5" : "MTProto");
                            int port = ps.getPort() > 0 ? ps.getPort() : 443;
                            int ping = pyCheckProxy(type, ps.getAddress(), port, timeoutMs);
                            return new TestedProxy(pi, ping);
                        } catch (Throwable ignored) {
                            return new TestedProxy(pi, -1);
                        }
                    }
                }));
            }
            pool.shutdown();

            List<TestedProxy> aliveList = new ArrayList<TestedProxy>();
            List<Object> deadList = new ArrayList<Object>();
            for (Future<TestedProxy> f : futures) {
                try {
                    TestedProxy tp = f.get();
                    if (tp != null && tp.ping >= 0) {
                        aliveList.add(tp);
                    } else if (tp != null) {
                        deadList.add(tp.pi);
                    }
                } catch (Throwable ignored) {}
            }

            Collections.sort(aliveList, new Comparator<TestedProxy>() {
                @Override
                public int compare(TestedProxy a, TestedProxy b) {
                    return Integer.compare(a.ping, b.ping);
                }
            });

            if (aliveList.isEmpty() && snapshot.size() > 1) {
                JSONObject res = new JSONObject();
                res.put("deleted", 0);
                res.put("kept", snapshot.size());
                res.put("aborted", true);
                return res.toString();
            }

            Method delM = ProxyController.class.getMethod("deleteProxy", piClass);
            int deleted = 0;
            for (Object d : deadList) {
                delM.invoke(pc, d);
                deleted++;
            }

            int kept = 0;
            for (int i = 0; i < aliveList.size(); i++) {
                TestedProxy tp = aliveList.get(i);
                if (i < keepCount) {
                    kept++;
                } else {
                    delM.invoke(pc, tp.pi);
                    deleted++;
                }
            }

            pc.saveProxyList();
            JSONObject res = new JSONObject();
            res.put("deleted", deleted);
            res.put("kept", kept);
            return res.toString();
        } catch (Throwable t) {
            Log.e(TAG, "pyKeepTopProxies error", t);
            return "{\"error\":\"" + t.getMessage() + "\"}";
        }
    }

    public static String pyKeepTopProxies(final long keepCount, final long timeoutMs) {
        return pyKeepTopProxies((int) keepCount, (int) timeoutMs);
    }

    public static String pyKeepTopProxies(final int keepCount, final long timeoutMs) {
        return pyKeepTopProxies(keepCount, (int) timeoutMs);
    }
}
