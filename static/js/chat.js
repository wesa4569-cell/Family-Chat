// static/js/chat.js - نسخة محسّنة احترافية مع صوت جديد وتحسينات شاملة
(() => {
  "use strict";

  // ====== Toast Notifications ======
  function showToast(message, typeOrTimeout = 3000, timeoutMaybe = 3000) {
    try {
      const text = (message || "").toString();

      // Backward/forward compatible args:
      // showToast("msg") -> default
      // showToast("msg", 4000) -> timeout
      // showToast("msg", "success"|"error"|"warning"|"info", 3500) -> type + timeout
      let type = "info";
      let timeoutMs = 3000;
      if (typeof typeOrTimeout === "number") {
        timeoutMs = typeOrTimeout;
      } else if (typeof typeOrTimeout === "string") {
        type = typeOrTimeout;
        timeoutMs = (typeof timeoutMaybe === "number") ? timeoutMaybe : 3000;
      }

      let host = document.getElementById("toastHost");
      if (!host) {
        host = document.createElement("div");
        host.id = "toastHost";
        host.style.cssText = "position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:9999;display:flex;flex-direction:column;gap:10px;";
        document.body.appendChild(host);
      }

      const t = document.createElement("div");
      t.textContent = text;

      let bg = "rgba(0,0,0,.88)";
      if (type === "success") bg = "rgba(16, 185, 129, .92)";
      else if (type === "error") bg = "rgba(239, 68, 68, .92)";
      else if (type === "warning") bg = "rgba(245, 158, 11, .92)";

      t.style.cssText = `padding:14px 20px;border-radius:14px;background:${bg};color:#fff;max-width:90vw;font-size:15px;box-shadow:0 12px 30px rgba(0,0,0,.3);animation:toastSlide 0.3s ease;backdrop-filter:blur(10px);`;
      host.appendChild(t);

      if (!document.querySelector("#toastStyles")) {
        const style = document.createElement("style");
        style.id = "toastStyles";
        style.textContent = "@keyframes toastSlide{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}";
        document.head.appendChild(style);
      }

      setTimeout(() => {
        t.style.opacity = "0";
        t.style.transform = "translateY(20px)";
        setTimeout(() => {
          t.remove();
          if (host.childElementCount === 0) host.remove();
        }, 300);
      }, timeoutMs);
    } catch (_) {}
  }

  // ====== IMPORTANT: Make showToast globally available ======
  window.showToast = showToast;

  // ====== State ======
  let currentReceiverId = null;
  let currentGroupId = null;
  let lastMessageTimestamp = 0;
  let lastMessageId = 0;
  let notificationSound = null;
  let currentUserId = null;
  let displayedMessages = new Set();
  let lastDate = null;
  let suppressSound = true;
  let initialPaintDone = false;
  let currentGroupIsOwner = false;
  let socket = null;
  let socketConnected = false;
  let onlineUsers = new Set();
  let typingTimer = null;
  let typingActive = false;
  let typingIndicator = null;
  let typingHideTimer = null;

  // ====== DOM Elements ======
  const messagesDiv = document.getElementById("messages");
  const form = document.getElementById("message-form");
  const messageInput = document.getElementById("message-content");
  const receiverInput = document.getElementById("receiver-id");
  const groupInput = document.getElementById("group-id");
  const chatTitle = document.getElementById("chat-title");
  const chatTitleWrap = document.querySelector(".chat-title-wrap");
  const netStatus = document.getElementById("netStatus");
  const logoutBtn = document.getElementById("logout-btn");
  const groupActionsWrap = document.getElementById("groupActionsWrap");
  const groupActionsBtn = document.getElementById("groupActionsBtn");
  const btnEditGroup = document.getElementById("btnEditGroup");
  const btnDeleteGroup = document.getElementById("btnDeleteGroup");
  const btnLeaveGroup = document.getElementById("btnLeaveGroup");
  const btnBlockGroup = document.getElementById("btnBlockGroup");
  const editGroupModalEl = document.getElementById("editGroupModal");
  const editGroupNameInput = document.getElementById("editGroupName");
  const btnSaveGroupName = document.getElementById("btnSaveGroupName");
  const usersDrawer = document.getElementById("usersDrawer");
  const overlay = document.getElementById("overlay");
  const openUsersBtn = document.getElementById("openUsers");
  const attachBtn = document.getElementById("attach-btn");
  const fileInput = document.getElementById("file-input");
  const cameraBtn = document.getElementById("camera-btn");
  const cameraInput = document.getElementById("camera-input");
  const micBtn = document.getElementById("mic-btn");
  const emojiToggle = document.getElementById("emoji-toggle");
  const emojiPicker = document.getElementById("emoji-picker");

  if (!messagesDiv || !form || !messageInput || !receiverInput) return;

  // ====== 🔊 Notifications Audio Manager ======
  const AUDIO_STORAGE_KEY = "chat_notification_audio_v1";

  const SOUND_LIBRARY = {
    // Each sound has multiple format fallbacks (best -> worst)
    notify: [
      "/static/sounds/notify.mp3",
      "/static/sounds/notify.ogg",
      "/static/sounds/notify.wav"
    ],
    soft: [
      "/static/sounds/soft.mp3",
      "/static/sounds/soft.ogg",
      "/static/sounds/soft.wav"
    ],
    ping: [
      "/static/sounds/ping.mp3",
      "/static/sounds/ping.ogg",
      "/static/sounds/ping.wav"
    ],
  };

  const AUDIO_DEFAULTS = {
    muted: false,
    volume: 0.4, // 0..1
    sounds: {
      message: "notify",
      mention: "notify",
      group: "notify",
      system: "soft",
    }
  };

  function readAudioPrefs() {
    try {
      const raw = localStorage.getItem(AUDIO_STORAGE_KEY);
      if (!raw) return JSON.parse(JSON.stringify(AUDIO_DEFAULTS));
      const obj = JSON.parse(raw);
      const merged = JSON.parse(JSON.stringify(AUDIO_DEFAULTS));
      if (typeof obj.muted === "boolean") merged.muted = obj.muted;
      if (typeof obj.volume === "number") merged.volume = Math.min(1, Math.max(0, obj.volume));
      if (obj.sounds && typeof obj.sounds === "object") {
        merged.sounds.message = obj.sounds.message || merged.sounds.message;
        merged.sounds.mention = obj.sounds.mention || merged.sounds.mention;
        merged.sounds.group = obj.sounds.group || merged.sounds.group;
        merged.sounds.system = obj.sounds.system || merged.sounds.system;
      }
      return merged;
    } catch (_) {
      return JSON.parse(JSON.stringify(AUDIO_DEFAULTS));
    }
  }

  function saveAudioPrefs(prefs) {
    try {
      localStorage.setItem(AUDIO_STORAGE_KEY, JSON.stringify(prefs));
    } catch (_) {}
  }

  class AudioManager {
    constructor() {
      this.prefs = readAudioPrefs();
      this.unlocked = false;

      // Pre-create audio elements per event type (helps mobile browsers)
      this.players = {
        message: new Audio(),
        mention: new Audio(),
        group: new Audio(),
        system: new Audio(),
      };

      this.applyPrefsToPlayers();
      this.bindUnlockOnGesture();
    }

    bindUnlockOnGesture() {
      const unlock = () => {
        if (this.unlocked) return;
        this.unlocked = true;

        // Try a short "warm-up" to satisfy some mobile autoplay policies
        try {
          const a = this.players.message;
          a.muted = true;
          const p = a.play();
          if (p && typeof p.then === "function") {
            p.then(() => { a.pause(); a.currentTime = 0; a.muted = this.prefs.muted; }).catch(() => {
              a.pause(); a.currentTime = 0; a.muted = this.prefs.muted;
            });
          } else {
            a.pause(); a.currentTime = 0; a.muted = this.prefs.muted;
          }
        } catch (_) {}
        document.removeEventListener("click", unlock, true);
        document.removeEventListener("touchstart", unlock, true);
        document.removeEventListener("keydown", unlock, true);
      };

      document.addEventListener("click", unlock, true);
      document.addEventListener("touchstart", unlock, true);
      document.addEventListener("keydown", unlock, true);
    }

    getSources(soundKey) {
      const arr = SOUND_LIBRARY[soundKey];
      if (Array.isArray(arr) && arr.length) return arr;
      return SOUND_LIBRARY.notify;
    }

    setSound(eventType, soundKey) {
      if (!this.players[eventType]) return;
      this.prefs.sounds[eventType] = soundKey;
      saveAudioPrefs(this.prefs);
      this.setPlayerSource(eventType);
    }

    setMuted(m) {
      this.prefs.muted = !!m;
      saveAudioPrefs(this.prefs);
      this.applyPrefsToPlayers();
    }

    setVolume(v01) {
      const v = Math.min(1, Math.max(0, Number(v01) || 0));
      this.prefs.volume = v;
      saveAudioPrefs(this.prefs);
      this.applyPrefsToPlayers();
    }

    applyPrefsToPlayers() {
      for (const k of Object.keys(this.players)) {
        const a = this.players[k];
        a.volume = this.prefs.volume;
        a.muted = this.prefs.muted;
        a.preload = "auto";
        this.setPlayerSource(k);
      }
    }

    setPlayerSource(eventType) {
      const a = this.players[eventType];
      if (!a) return;
      const soundKey = this.prefs.sounds[eventType] || "notify";
      const sources = this.getSources(soundKey);

      // Try first source; if it errors, rotate to next
      let idx = 0;
      const trySet = () => {
        const src = sources[idx] || sources[sources.length - 1];
        a.src = src;
        try { a.load(); } catch (_) {}
      };

      a.onstalled = null;
      a.onerror = null;
      a.onerror = () => {
        idx += 1;
        if (idx < sources.length) trySet();
      };

      trySet();
    }

    play(eventType = "message") {
      const a = this.players[eventType] || this.players.message;
      if (!a) return;
      if (this.prefs.muted) return;

      try {
        a.currentTime = 0;
        const p = a.play();
        if (p && typeof p.catch === "function") p.catch(() => {});
      } catch (_) {}
    }

    test() {
      this.play("message");
    }
  }

  const audioManager = new AudioManager();

  // ====== Notifications Settings UI ======
  function initNotificationSettingsUI() {
    const muteEl = document.getElementById("notifMuteToggle");
    const volEl = document.getElementById("notifVolumeRange");
    const volValEl = document.getElementById("notifVolumeValue");
    const msgSel = document.getElementById("notifSoundMessage");
    const menSel = document.getElementById("notifSoundMention");
    const grpSel = document.getElementById("notifSoundGroup");
    const sysSel = document.getElementById("notifSoundSystem");
    const pushToggle = document.getElementById("pushEnableToggle");
    const pushTestBtn = document.getElementById("pushTestBtn");
    const testBtn = document.getElementById("notifTestBtn");

    if (!muteEl || !volEl || !volValEl || !msgSel || !menSel || !testBtn) return;

    // init values from prefs
    muteEl.checked = !!audioManager.prefs.muted;
    const volPct = Math.round((audioManager.prefs.volume || 0) * 100);
    volEl.value = String(volPct);
    volValEl.textContent = `${volPct}%`;
    msgSel.value = audioManager.prefs.sounds.message || "notify";
    menSel.value = audioManager.prefs.sounds.mention || "notify";
    if (grpSel) grpSel.value = audioManager.prefs.sounds.group || "notify";
    if (sysSel) sysSel.value = audioManager.prefs.sounds.system || "soft";

    const updatePushTestState = () => {
      if (!pushTestBtn) return;
      const supported = ("Notification" in window) && ("serviceWorker" in navigator) && ("PushManager" in window);
      const secure = !!window.isSecureContext;
      const enabled = readPushEnabled();
      pushTestBtn.disabled = !supported || !secure || !enabled;
      if (!supported) pushTestBtn.title = "المتصفح لا يدعم إشعارات Push.";
      else if (!secure) pushTestBtn.title = "يتطلب اتصال HTTPS بشهادة موثوقة أو localhost.";
      else if (!enabled) pushTestBtn.title = "فعّل إشعارات الخلفية أولاً.";
      else pushTestBtn.title = "";
    };

    // push toggle
    if (pushToggle) {
      pushToggle.checked = readPushEnabled();
    }
    updatePushTestState();

    muteEl.addEventListener("change", () => {
      audioManager.setMuted(muteEl.checked);
    });

    volEl.addEventListener("input", () => {
      const pct = Math.max(0, Math.min(100, Number(volEl.value) || 0));
      volValEl.textContent = `${pct}%`;
      audioManager.setVolume(pct / 100);
    });

    msgSel.addEventListener("change", () => audioManager.setSound("message", msgSel.value));
    menSel.addEventListener("change", () => audioManager.setSound("mention", menSel.value));

    if (grpSel) grpSel.addEventListener("change", () => audioManager.setSound("group", grpSel.value));
    if (sysSel) sysSel.addEventListener("change", () => audioManager.setSound("system", sysSel.value));

    if (pushToggle) {
      pushToggle.addEventListener("change", async () => {
        const want = !!pushToggle.checked;
        setPushEnabled(want);
        if (want) {
          const ok = await ensurePushSubscription();
          showToast(ok ? "🛰️ تم تفعيل إشعارات الخلفية" : "تعذر تفعيل إشعارات الخلفية");
          if (!ok) pushToggle.checked = false;
          updatePushTestState();
        } else {
          await unsubscribePush();
          showToast("تم إيقاف إشعارات الخلفية");
          updatePushTestState();
        }
      });
    }

    if (pushTestBtn) {
      pushTestBtn.addEventListener("click", async (e) => {
        e.preventDefault();
        const ok = await sendTestPush();
        showToast(ok ? "🛰️ تم إرسال إشعار تجريبي" : "تعذر إرسال إشعار تجريبي");
      });
    }

    testBtn.addEventListener("click", (e) => {
      e.preventDefault();
      audioManager.test();
      showToast("🔊 تم تشغيل صوت الاختبار");
    });
  }


  // ====== Emoji Picker ======
  function initEmojiPicker() {
    if (!emojiPicker || emojiPicker.dataset.ready === "1") return;
    const EMOJIS = "😀 😃 😄 😁 😆 😅 😂 🤣 😊 😇 🙂 🙃 😉 😌 😍 🥰 😘 😗 😙 😚 😋 😛 😝 😜 🤪 🤨 🧐 🤓 😎 🤩 😏 😒 😞 😔 😟 😕 🙁 ☹️ 😣 😖 😫 😩 🥺 😢 😭 😤 😠 😡 🤬 🤯 😳 🥵 🥶 😱 😨 😰 😥 😓 🤗 🤔 🫡 🤭 🤫 🤥 😶 😐 😑 🫤 🙄 😬 🤐 🥴 🤢 🤮 🤧 😷 🤒 🤕 🤑 🤠 😈 👿 💀 ☠️ 👻 👽 🤖 💩 👍 👎 👊 ✊ 🤝 🙏 👋 🤟 🤘 👌 ❤️ 🧡 💛 💚 💙 💜 🖤 🤍 🤎 💔 💯 ✅ ❌ ⭐ 🔥 🎉 🎊 🎁 ⏳ ⚡ 📌 📎 📷 🎤 🎧 ✨ 💫 🌟 🌙 ☀️ 🌈".split(" ");
    emojiPicker.innerHTML = "";
    EMOJIS.forEach(e => {
      const s = document.createElement("span");
      s.textContent = e;
      s.addEventListener("click", () => {
        messageInput.value = (messageInput.value || "") + e;
        messageInput.focus();
        emojiPicker.style.display = "none";
      });
      emojiPicker.appendChild(s);
    });
    emojiPicker.dataset.ready = "1";
  }

  // ====== Viewport Height Fix ======
  function setVh() {
    document.documentElement.style.setProperty("--vh", `${window.innerHeight * 0.01}px`);
  }
  setVh();
  window.addEventListener("resize", setVh);

  // ====== History Management ======
  try {
    history.replaceState({ page: "chat" }, "", location.href);
    window.addEventListener("popstate", () => window.location.href = "/");
  } catch (e) {}

  // ====== Drawer Functions ======
  function openDrawer() {
    usersDrawer?.classList.add("open");
    overlay?.classList.add("show");
  }

  function closeDrawer() {
    usersDrawer?.classList.remove("open");
    overlay?.classList.remove("show");
  }

  function toggleDrawer(e) {
    if (e) { e.preventDefault(); e.stopPropagation(); }
    if (usersDrawer?.classList.contains("open")) closeDrawer();
    else openDrawer();
  }

  if (openUsersBtn) {
    openUsersBtn.addEventListener("click", toggleDrawer);
  }
  overlay?.addEventListener("click", closeDrawer);

  // ====== 🎯 Settings Dropdown Toggle ======
  // NOTE: Bootstrap JS is not used, so we manually toggle and position the menu.
  if (groupActionsBtn) {
    groupActionsBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const menu = groupActionsWrap?.querySelector(".dropdown-menu");
      const willOpen = !menu?.classList.contains("show");
      if (willOpen) {
        // Place menu in viewport (fix for mobile clipping)
        if (menu) {
          menu.classList.add("show");
          menu.style.position = "fixed";
          menu.style.inset = "auto";
          const r = groupActionsBtn.getBoundingClientRect();
          const menuW = Math.min(menu.offsetWidth || 220, window.innerWidth - 16);

          // Open from LEFT and keep inside viewport
          let left = Math.max(8, r.left);
          if (left + menuW > window.innerWidth - 8) right = Math.max(8, window.innerWidth - menuW - 8);

          let top = r.bottom + 8;
          const menuH = menu.offsetHeight || 200;
          if (top + menuH > window.innerHeight - 8) {
            top = Math.max(8, l.top - menuH - 8);
          }

          menu.style.top = `${top}px`;
          menu.style.left = `${left}px`;
          menu.style.right = "right";
          menu.style.maxWidth = `${window.innerWidth - 16}px`;
        }
        groupActionsBtn.setAttribute("aria-expanded", "true");
      } else {
        if (menu) {
          menu.classList.remove("show");
          menu.style.position = "";
          menu.style.top = "";
          menu.style.right = "";
          menu.style.left = "";
          menu.style.inset = "";
          menu.style.maxWidth = "";
        }
        groupActionsBtn.setAttribute("aria-expanded", "false");
      }
    });

    // Close dropdown when clicking outside
    document.addEventListener("click", (e) => {
      if (!groupActionsWrap?.contains(e.target)) {
        const menu = groupActionsWrap?.querySelector(".dropdown-menu");
        if (menu) {
          menu.classList.remove("show");
          menu.style.position = "";
          menu.style.top = "";
          menu.style.right = "";
          menu.style.left = "";
          menu.style.inset = "";
          menu.style.maxWidth = "";
        }
        groupActionsBtn.setAttribute("aria-expanded", "false");
      }
    });
  }

  // ====== Helper Functions ======
  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text ?? "";
    return div.innerHTML;
  }

  function formatLocalTime(ms) {
    try {
      return new Date(Number(ms)).toLocaleTimeString("ar-EG", { hour: "2-digit", minute: "2-digit" });
    } catch (_) { return ""; }
  }

  function localDateKey(ms) {
    try {
      return new Date(Number(ms)).toLocaleDateString("en-CA");
    } catch (_) { return ""; }
  }

  function hideEmptyState() {
    document.getElementById("empty-state")?.style.setProperty("display", "none");
  }

  // Update sidebar ordering instantly without refreshing the page
  function bumpConversation(kind, id, ms) {
    const ts = Number(ms || Date.now());
    let li = null;
    if (kind === "group") li = document.querySelector(`li.user-item[data-group-id="${id}"]`);
    else li = document.querySelector(`li.user-item[data-user-id="${id}"]`);
    if (li) {
      li.setAttribute("data-last-ms", String(ts));
      reorderConversationList();
    }
  }

  function scrollToBottom(force = false) {
    const nearBottom = (messagesDiv.scrollHeight - messagesDiv.scrollTop - messagesDiv.clientHeight) < 120;
    if (force || nearBottom) messagesDiv.scrollTop = messagesDiv.scrollHeight;
  }

  function hardScrollToBottom() {
    requestAnimationFrame(() => {
      scrollToBottom(true);
      requestAnimationFrame(() => scrollToBottom(true));
    });
  }

  function addDateSeparator(dateStr) {
    const dateDiv = document.createElement("div");
    dateDiv.className = "date-separator";
    let displayDate = dateStr;
    const today = new Date().toISOString().split("T")[0];
    const yesterday = new Date(Date.now() - 86400000).toISOString().split("T")[0];
    if (dateStr === today) displayDate = "اليوم";
    else if (dateStr === yesterday) displayDate = "أمس";
    else {
      try {
        displayDate = new Date(dateStr).toLocaleDateString("ar-EG", { weekday: "long", day: "numeric", month: "long" });
      } catch (_) {}
    }
    dateDiv.innerHTML = `<span>${displayDate}</span>`;
    messagesDiv.appendChild(dateDiv);
  }

  function appendMessage(msg) {
    const dkey = localDateKey(msg.timestamp_ms || 0);
    if (dkey && dkey !== lastDate) {
      addDateSeparator(dkey);
      lastDate = dkey;
    }
    const div = document.createElement("div");
    const isSent = String(msg.sender_id) === String(currentUserId);
    div.className = isSent ? "message sender" : "message receiver";
    div.dataset.msgId = String(msg.id);
    div.innerHTML = buildMessageInner(msg, isSent);
    messagesDiv.appendChild(div);
  }

  function buildMessageInner(msg, isSent) {
    const t = escapeHtml(formatLocalTime(msg.timestamp_ms || 0) || (msg.timestamp ?? ""));
    const type = (msg.message_type || msg.type || "text");
    const mediaUrl = msg.media_url || msg.media || null;
    const isGroup = Boolean(currentGroupId || msg.group_id);
    const senderName = (msg.sender_name || msg.sender || "").trim();
    const senderLine = (isGroup && !isSent && senderName) ? `<div class="msg-sender">${escapeHtml(senderName)}</div>` : "";

    if (type === "image" && mediaUrl) {
      const safeUrl = escapeHtml(mediaUrl);
      return `${senderLine}<div class="media-wrap"><a href="${safeUrl}" target="_blank" rel="noopener"><img class="msg-image" src="${safeUrl}" alt="image" loading="lazy"></a></div><div class="message-time">${t}</div>`;
    }
    if (type === "audio" && mediaUrl) {
      const safeUrl = escapeHtml(mediaUrl);
      return `${senderLine}<div class="media-wrap"><audio controls preload="none" src="${safeUrl}"></audio></div><div class="message-time">${t}</div>`;
    }
    if ((type === "video" || (type === "file" && (msg.media_mime || "").toLowerCase().startsWith("video/"))) && mediaUrl) {
      const safeUrl = escapeHtml(mediaUrl);
      return `${senderLine}<div class="media-wrap"><video controls preload="metadata" src="${safeUrl}" style="max-width:100%;border-radius:12px;"></video></div><div class="message-time">${t}</div>`;
    }
    if (type === "file" && mediaUrl) {
      const safeUrl = escapeHtml(mediaUrl);
      const fname = escapeHtml(msg.content || "ملف");
      const mime2 = escapeHtml(msg.media_mime || "");
      return `${senderLine}<div class="file-wrap"><a class="file-link" href="${safeUrl}" target="_blank" rel="noopener"><i class="bi bi-paperclip"></i><span class="file-name">${fname}</span></a>${mime2 ? `<div class="file-mime">${mime2}</div>` : ``}</div><div class="message-time">${t}</div>`;
    }
    return `${senderLine}<div>${escapeHtml(msg.content || "")}</div><div class="message-time">${t}</div>`;
  }

  // ====== Notifications ======
  // ====== Push (Service Worker) ======
  const PUSH_ENABLED_KEY = "chat_push_enabled_v1";

  function readPushEnabled() {
    try { return localStorage.getItem(PUSH_ENABLED_KEY) === "1"; } catch (_) { return false; }
  }
  function setPushEnabled(v) {
    try { localStorage.setItem(PUSH_ENABLED_KEY, v ? "1" : "0"); } catch (_) {}
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) outputArray[i] = rawData.charCodeAt(i);
    return outputArray;
  }

  async function registerServiceWorkerIfNeeded() {
    if (!("serviceWorker" in navigator)) return null;
    try {
      // Register SW at the site root so it can control all pages (required for Web Push)
      const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      return reg;
    } catch (e) {
      console.warn("SW register failed", e);
      return null;
    }
  }

  async function ensurePushSubscription() {
    try {
      if (!("Notification" in window) || !("serviceWorker" in navigator) || !("PushManager" in window)) return false;

      // Web Push requires a secure context (valid HTTPS or localhost).
      if (!window.isSecureContext) {
        try { showToast("لا يمكن تفعيل إشعارات الخلفية لأن الاتصال غير آمن. استخدم HTTPS بشهادة موثوقة أو افتح على localhost.", "warning"); } catch (_) {}
        return false;
      }

      // Do not force the permission prompt unless it's needed.
      // (Some browsers will treat repeated prompts as an error and return "denied" / block.)
      let perm = Notification.permission;
      if (perm === "default") {
        perm = await Notification.requestPermission();
      }
      if (perm !== "granted") return false;

      const reg = await registerServiceWorkerIfNeeded();
      if (!reg) return false;

      const keyRes = await fetch("/api/push/vapid_public_key", { credentials: "same-origin" });
      if (!keyRes.ok) return false;
      const keyJson = await keyRes.json();
      if (!keyJson || !keyJson.publicKey) return false;

      let sub = await reg.pushManager.getSubscription();
      if (!sub) {
        sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(keyJson.publicKey)
        });
      }

      const ok = await fetch("/api/push/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ subscription: sub })
      });

      return ok.ok;
    } catch (e) {
      console.warn("ensurePushSubscription failed", e);
      return false;
    }
  }

  async function unsubscribePush() {
    try {
      if (!("serviceWorker" in navigator)) return;
      const reg = await navigator.serviceWorker.getRegistration("/");
      if (!reg) return;
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        try { await sub.unsubscribe(); } catch (_) {}
      }
      try {
        await fetch("/api/push/unsubscribe", { method: "POST", credentials: "same-origin" });
      } catch (_) {}
    } catch (_) {}
  }

  async function sendTestPush() {
    try {
      // Ensure we have a subscription before asking the server to send.
      const subOk = await ensurePushSubscription();
      if (!subOk) {
        try { showToast("تعذر تفعيل الاشتراك للإشعارات (تحقق من HTTPS والصلاحيات).", "error"); } catch (_) {}
        return false;
      }

      // Send test push request
      const res = await fetch("/api/push/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ title: "اختبار إشعار", body: "هذا إشعار اختبار من السيرفر" })
      });

      let data = null;
      try { data = await res.json(); } catch (_) { data = null; }

      if (!res.ok || !data || data.ok !== true) {
        const reason = (data && (data.error || data.reason)) ? String(data.error || data.reason) : "unknown";
        let msg = "تعذر إرسال إشعار تجريبي.";
        if (reason === "insecure_context") msg = "تعذر إرسال الإشعار لأن الاتصال غير آمن (يلزم HTTPS بشهادة موثوقة أو localhost).";
        else if (reason === "missing_pywebpush") msg = "تعذر إرسال الإشعار لأن مكتبة pywebpush غير مثبتة على السيرفر.";
        else if (reason === "missing_vapid") msg = "تعذر إرسال الإشعار لأن مفاتيح VAPID غير متاحة.";
        else if (reason === "no_subscription") msg = "تعذر إرسال الإشعار لأن الاشتراك غير محفوظ/غير نشط.";
        else if (reason === "send_failed") msg = "تعذر إرسال الإشعار بسبب خطأ أثناء الإرسال من السيرفر.";
        try { showToast(msg, "error"); } catch (_) {}
        return false;
      }

      try { showToast("تم إرسال إشعار تجريبي ✅", "success"); } catch (_) {}
      return true;
    } catch (e) {
      console.warn("sendTestPush failed", e);
      try { showToast("تعذر إرسال إشعار تجريبي.", "error"); } catch (_) {}
      return false;
    }
  }

  function requestNotificationPermission() {
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
  }

  function showNotification(senderName, content, id) {
    if (document.hidden && "Notification" in window && Notification.permission === "granted") {
      const n = new Notification("💬 رسالة جديدة من " + (senderName || "مستخدم"), {
        body: (content || "").substring(0, 100),
        icon: "/static/logo.svg",
        tag: id ? ("chat-" + id) : undefined,
        badge: "/static/logo.svg"
      });
      n.onclick = () => { window.focus(); n.close(); };
      setTimeout(() => n.close(), 6000);
    }
  }

  // ====== API - Load Messages ======
  async function loadMessages({ allowSound = false, forceScrollBottom = false, updateSidebar = true } = {}) {
    if (!currentReceiverId && !currentGroupId) return;
    const url = currentGroupId
      ? `/get_group_messages/${currentGroupId}?last_id=${lastMessageId}`
      : `/get_messages/${currentReceiverId}?since=${lastMessageTimestamp}&last_id=${lastMessageId}`;
    try {
      const res = await fetch(url, { cache: "no-store" });
      const data = await res.json();
      if (!Array.isArray(data)) return;
      if (data.length > 0) hideEmptyState();

      let hasNewIncoming = false, newAdded = false;
      let newestMs = 0;
      for (const msg of data) {
        if (msg.id && Number(msg.id) > lastMessageId) lastMessageId = Number(msg.id);
        if (msg.timestamp_ms && Number(msg.timestamp_ms) > lastMessageTimestamp) lastMessageTimestamp = Number(msg.timestamp_ms);
        const key = String(msg.id);
        if (displayedMessages.has(key)) continue;
        appendMessage(msg);
        displayedMessages.add(key);
        newAdded = true;
        if (msg.timestamp_ms) newestMs = Math.max(newestMs, Number(msg.timestamp_ms));
        if (currentUserId && String(msg.sender_id) !== String(currentUserId)) {
          hasNewIncoming = true;
          const senderLi = document.querySelector(`li[data-user-id="${msg.sender_id}"]`);
          const senderName = senderLi?.getAttribute("data-name") || msg.sender_name || "مستخدم";
          showNotification(senderName, msg.content, msg.id);
        }
      }

      // Keep sidebar order in sync with latest message without refreshing the page
      if (updateSidebar && newestMs > 0) {
        if (currentGroupId) bumpConversation("group", currentGroupId, newestMs);
        else bumpConversation("user", currentReceiverId, newestMs);
      }

      if (newAdded) {
        if (forceScrollBottom) hardScrollToBottom();
        else scrollToBottom(false);
      } else if (forceScrollBottom) hardScrollToBottom();

      if (allowSound && !suppressSound && initialPaintDone && hasNewIncoming) {
        audioManager.play('message');
      }
      if (!initialPaintDone) initialPaintDone = true;
    } catch (_) {}
  }

  // ====== Sidebar Badges Refresh ======
  async function refreshSidebarBadges() {
    try {
      const res = await fetch("/api/unread_counts", { cache: "no-store" });
      const data = await res.json().catch(() => ({}));
      if (!data?.ok) return;

      // Invites
      const invites = Number(data.invites || 0);
      const invItem = document.getElementById("btnGroupInvites");
      const invBadge = document.getElementById("invitesBadge");
      const invText = document.getElementById("invitesText");
      if (invItem && invBadge && invText) {
        if (invites > 0) {
          invItem.classList.remove("hidden");
          invBadge.textContent = String(invites);
          invBadge.style.display = "inline-flex";
          invText.textContent = `لديك ${invites} طلب`;
        } else {
          invBadge.style.display = "none";
          invText.textContent = "لا توجد طلبات";
        }
      }

      // User Messages
      const ucounts = data.users || {};
      document.querySelectorAll('li.user-item[data-user-id]').forEach((li) => {
        const id = Number(li.getAttribute("data-user-id") || 0);
        const c = Number(ucounts[id] || 0);
        let badge = li.querySelector(".badge-unread");
        if (!badge) {
          badge = document.createElement("span");
          badge.className = "badge-unread";
          li.appendChild(badge);
        }
        if (c > 0) {
          badge.textContent = c > 99 ? "99+" : String(c);
          badge.style.display = "inline-flex";
        } else {
          badge.style.display = "none";
        }
      });

      // Group Messages
      const gcounts = data.groups || {};
      document.querySelectorAll('li.user-item[data-group-id]').forEach((li) => {
        const gid = Number(li.getAttribute("data-group-id") || 0);
        const c = Number(gcounts[gid] || 0);
        let badge = li.querySelector(".badge-unread");
        if (!badge) {
          badge = document.createElement("span");
          badge.className = "badge-unread";
          li.appendChild(badge);
        }
        if (c > 0) {
          badge.textContent = c > 99 ? "99+" : String(c);
          badge.style.display = "inline-flex";
        } else {
          badge.style.display = "none";
        }
      });

      // === Update ordering by latest message timestamp (users + groups) ===
      const lastUsers = (data.last_ts && data.last_ts.users) ? data.last_ts.users : {};
      const lastGroups = (data.last_ts && data.last_ts.groups) ? data.last_ts.groups : {};

      document.querySelectorAll('li.user-item[data-user-id]').forEach((li) => {
        const id = Number(li.getAttribute("data-user-id") || 0);
        const ms = Number(lastUsers[id] || li.getAttribute("data-last-ms") || 0);
        li.setAttribute("data-last-ms", String(ms || 0));
      });

      document.querySelectorAll('li.user-item[data-group-id]').forEach((li) => {
        const gid = Number(li.getAttribute("data-group-id") || 0);
        const ms = Number(lastGroups[gid] || li.getAttribute("data-last-ms") || 0);
        li.setAttribute("data-last-ms", String(ms || 0));
      });

      reorderConversationList();
    } catch (_) {}
  }

  function reorderConversationList() {
    const ul = document.getElementById("users-list");
    if (!ul) return;

    // Keep special items on top
    const pinned = [];
    const add = document.getElementById("btnAddGroup");
    const inv = document.getElementById("btnGroupInvites");
    if (add) pinned.push(add);
    if (inv) pinned.push(inv);

    const items = Array.from(ul.querySelectorAll('li.user-item[data-user-id], li.user-item[data-group-id]'));
    items.sort((a, b) => {
      const am = Number(a.getAttribute("data-last-ms") || 0);
      const bm = Number(b.getAttribute("data-last-ms") || 0);
      if (bm !== am) return bm - am;
      const an = (a.getAttribute("data-name") || "").toLowerCase();
      const bn = (b.getAttribute("data-name") || "").toLowerCase();
      return an.localeCompare(bn, "ar");
    });

    pinned.forEach((el) => { if (el.parentElement === ul) ul.appendChild(el); });
    items.forEach((el) => ul.appendChild(el));
  }

  // ====== Realtime (Socket.IO) ======
  function ensureTypingIndicator() {
    if (typingIndicator || !chatTitleWrap) return;
    typingIndicator = document.createElement("span");
    typingIndicator.className = "typing-indicator";
    typingIndicator.id = "typing-indicator";
    chatTitleWrap.appendChild(typingIndicator);
  }

  function setTypingIndicator(text) {
    ensureTypingIndicator();
    if (!typingIndicator) return;
    if (text) {
      typingIndicator.textContent = text;
      typingIndicator.classList.add("active");
    } else {
      typingIndicator.textContent = "";
      typingIndicator.classList.remove("active");
    }
  }

  function setUserOnlineState(userId, isOnline) {
    const li = document.querySelector(`li.user-item[data-user-id="${userId}"]`);
    if (li) li.setAttribute("data-online", isOnline ? "1" : "0");
  }

  function renderNetStatus() {
    if (!netStatus) return;
    if (!socketConnected) {
      netStatus.textContent = "غير متصل";
      return;
    }
    if (currentReceiverId) {
      netStatus.textContent = onlineUsers.has(String(currentReceiverId)) ? "متصل الآن" : "غير متصل";
      return;
    }
    if (currentGroupId) {
      netStatus.textContent = "متصل";
      return;
    }
    netStatus.textContent = "";
  }

  function handleTypingEvent(data) {
    if (!data || String(data.sender_id) === String(currentUserId)) return;
    if (data.group_id) {
      if (String(data.group_id) !== String(currentGroupId)) return;
    } else if (String(data.sender_id) !== String(currentReceiverId)) {
      return;
    }
    const senderName = document.querySelector(`li[data-user-id="${data.sender_id}"]`)?.getAttribute("data-name") || "مستخدم";
    if (data.is_typing) {
      const msg = data.group_id ? `${senderName} يكتب الآن...` : "يكتب الآن...";
      setTypingIndicator(msg);
      if (typingHideTimer) clearTimeout(typingHideTimer);
      typingHideTimer = setTimeout(() => setTypingIndicator(""), 2500);
    } else {
      setTypingIndicator("");
    }
  }

  function handleIncomingMessage(payload) {
    const msg = payload?.message || payload;
    if (!msg || !msg.id) return;
    if (String(msg.sender_id) === String(currentUserId)) return;

    const isGroup = payload?.type === "group" || msg.group_id;
    const activeMatch = isGroup
      ? (currentGroupId && String(msg.group_id) === String(currentGroupId))
      : (currentReceiverId && String(msg.sender_id) === String(currentReceiverId));

    const key = String(msg.id);
    if (activeMatch) {
      setTypingIndicator("");
      if (displayedMessages.has(key)) return;
      hideEmptyState();
      appendMessage(msg);
      displayedMessages.add(key);
      if (msg.timestamp_ms && Number(msg.timestamp_ms) > lastMessageTimestamp) {
        lastMessageTimestamp = Number(msg.timestamp_ms);
      }
      if (msg.id && Number(msg.id) > lastMessageId) {
        lastMessageId = Number(msg.id);
      }
      scrollToBottom(false);
      if (!suppressSound && initialPaintDone) {
        audioManager.play("message");
      }
      if (document.hidden) {
        const senderName = msg.sender_name || document.querySelector(`li[data-user-id="${msg.sender_id}"]`)?.getAttribute("data-name") || "مستخدم";
        showNotification(senderName, msg.content, msg.id);
      }
    } else {
      if (isGroup && msg.group_id) bumpConversation("group", msg.group_id, msg.timestamp_ms);
      if (!isGroup) bumpConversation("user", msg.sender_id, msg.timestamp_ms);
      refreshSidebarBadges();
      if (document.hidden) {
        const senderName = msg.sender_name || document.querySelector(`li[data-user-id="${msg.sender_id}"]`)?.getAttribute("data-name") || "مستخدم";
        showNotification(senderName, msg.content, msg.id);
      }
    }
  }

  function emitTyping(isTyping) {
    if (!socket || !socketConnected) return;
    if (!currentReceiverId && !currentGroupId) return;
    socket.emit("typing", {
      receiver_id: currentReceiverId,
      group_id: currentGroupId,
      is_typing: isTyping,
    });
  }

  function joinGroupRooms(groupIds) {
    if (!socket || !socketConnected) return;
    if (!Array.isArray(groupIds) || groupIds.length === 0) return;
    socket.emit("join_groups", { groups: groupIds });
  }

  function initSocket() {
    if (!window.io) return;
    socket = window.io({ transports: ["websocket", "polling"] });
    socket.on("connect", () => {
      socketConnected = true;
      const groupIds = Array.from(document.querySelectorAll('li.user-item[data-group-id]'))
        .map((li) => li.getAttribute("data-group-id"))
        .filter(Boolean);
      joinGroupRooms(groupIds);
      refreshSidebarBadges();
      renderNetStatus();
    });
    socket.on("disconnect", () => {
      socketConnected = false;
      renderNetStatus();
    });
    socket.on("presence_state", (data) => {
      const ids = Array.isArray(data?.online_user_ids) ? data.online_user_ids : [];
      onlineUsers = new Set(ids.map((id) => String(id)));
      document.querySelectorAll('li.user-item[data-user-id]').forEach((li) => {
        const id = li.getAttribute("data-user-id");
        if (!id) return;
        setUserOnlineState(id, onlineUsers.has(String(id)));
      });
      renderNetStatus();
    });
    socket.on("user_status", (data) => {
      if (!data) return;
      const id = data.user_id;
      if (!id) return;
      const isOnline = data.status === "online";
      if (isOnline) onlineUsers.add(String(id));
      else onlineUsers.delete(String(id));
      setUserOnlineState(id, isOnline);
      renderNetStatus();
    });
    socket.on("typing", handleTypingEvent);
    socket.on("new_message", handleIncomingMessage);
    socket.on("refresh_unread", () => refreshSidebarBadges());
  }

  // ====== Group Actions Visibility ======
  function updateGroupActionsVisibility() {
    if (!groupActionsWrap) return;
    const isGroup = Boolean(currentGroupId);
    groupActionsWrap.style.display = isGroup ? "inline-block" : "none";
    
    // Show/hide specific actions based on ownership
    if (btnEditGroup) btnEditGroup.style.display = (isGroup && currentGroupIsOwner) ? "" : "none";
    if (btnDeleteGroup) btnDeleteGroup.style.display = (isGroup && currentGroupIsOwner) ? "" : "none";
    if (btnLeaveGroup) btnLeaveGroup.style.display = isGroup ? "" : "none";
    if (btnBlockGroup) btnBlockGroup.style.display = isGroup ? "" : "none";
  }

  // ====== Chat Switching ======
  function resetConversation() {
    messagesDiv.innerHTML = `<div class="empty-state" id="empty-state">
      <svg width="120" height="120" viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="60" cy="60" r="50" fill="#667eea" opacity="0.1"/>
        <path d="M60 30C42.3 30 28 42.3 28 57C28 64.8 31.8 71.7 37.8 76.5L35 88L47.4 82.8C51.6 84.6 56.1 85.5 60 85.5C77.7 85.5 92 73.2 92 57C92 42.3 77.7 30 60 30Z" fill="#667eea" opacity="0.3"/>
      </svg>
      <h3>مرحباً بك!</h3>
      <p>اختر محادثة من القائمة أو ابدأ محادثة جديدة</p>
    </div>`;
    displayedMessages.clear();
    lastMessageTimestamp = 0;
    lastMessageId = 0;
    lastDate = null;
    suppressSound = true;
    initialPaintDone = false;
  }

  function changeChat(userId, userName) {
    if (!userId || String(currentReceiverId) === String(userId)) return;
    setTypingIndicator("");
    document.querySelectorAll(".user-item").forEach(li => li.classList.remove("active"));
    document.querySelector(`li[data-user-id="${userId}"]`)?.classList.add("active");
    currentGroupId = null;
    if (groupInput) groupInput.value = "";
    currentReceiverId = String(userId);
    receiverInput.value = String(userId);
    if (chatTitle) chatTitle.textContent = userName || "محادثة";
    const chatAvatar = document.getElementById("chat-avatar");
    const li = document.querySelector(`li[data-user-id="${userId}"]`);
    const av = li?.getAttribute("data-avatar");
    if (chatAvatar) {
      chatAvatar.style.display = "inline-block";
      if (av) chatAvatar.src = av;
    }
    resetConversation();
    updateGroupActionsVisibility();
    loadMessages({ allowSound: false, forceScrollBottom: true, updateSidebar: false }).then(() => {
      hardScrollToBottom();
      setTimeout(() => { suppressSound = false; }, 250);
    });
    renderNetStatus();
    closeDrawer();
    messageInput.focus();
  }

  function changeGroup(groupId, groupName) {
    if (!groupId || String(currentGroupId) === String(groupId)) return;
    setTypingIndicator("");
    document.querySelectorAll(".user-item").forEach(li => li.classList.remove("active"));
    document.querySelector(`li[data-group-id="${groupId}"]`)?.classList.add("active");
    currentGroupId = String(groupId);
    const gLi = document.querySelector(`li[data-group-id="${groupId}"]`);
    currentGroupIsOwner = (gLi?.getAttribute("data-is-owner") || "0") === "1";
    if (groupInput) groupInput.value = String(groupId);
    currentReceiverId = null;
    if (receiverInput) receiverInput.value = "";
    if (chatTitle) chatTitle.textContent = groupName || "مجموعة";
    const chatAvatar = document.getElementById("chat-avatar");
    if (chatAvatar) {
      chatAvatar.src = "";
      chatAvatar.style.display = "none";
    }
    resetConversation();
    updateGroupActionsVisibility();
    suppressSound = true;
    initialPaintDone = false;
    loadMessages({ allowSound: false, forceScrollBottom: true, updateSidebar: false }).then(() => {
      hardScrollToBottom();
      setTimeout(() => { suppressSound = false; }, 250);
    });
    joinGroupRooms([currentGroupId]);
    renderNetStatus();
    closeDrawer();
  }

  // ====== Send Message ======
  async function sendMessage(e) {
    if (e) e.preventDefault();
    if (!currentReceiverId && !currentGroupId) return;
    const content = messageInput.value.trim();
    if (!content) return;
    hideEmptyState();
    const tempId = "temp-" + Date.now();
    const tempMsg = {
      id: tempId,
      sender_id: currentUserId,
      receiver_id: currentReceiverId,
      content,
      message_type: "text",
      timestamp_ms: Date.now(),
      timestamp: new Date().toLocaleTimeString("ar-EG", { hour: "2-digit", minute: "2-digit" }),
      date: new Date().toISOString().split("T")[0],
    };
    appendMessage(tempMsg);
    displayedMessages.add(String(tempId));
    messageInput.value = "";
    hardScrollToBottom();
    if (typingActive) {
      typingActive = false;
      emitTyping(false);
    }

    // Move conversation to top immediately (optimistic)
    if (currentGroupId) bumpConversation("group", currentGroupId, tempMsg.timestamp_ms);
    else bumpConversation("user", currentReceiverId, tempMsg.timestamp_ms);

    try {
      const formData = new FormData();
      formData.append("content", content);
      let endpoint = "/send_message";
      if (currentGroupId) {
        endpoint = "/send_group_message";
        formData.append("group_id", currentGroupId);
      } else {
        formData.append("receiver_id", currentReceiverId);
      }
      const res = await fetch(endpoint, { method: "POST", body: formData });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data?.status === "ok" && data.message) {
        const realId = String(data.message.id);
        const tempDiv = document.querySelector(`div[data-msg-id="${tempId}"]`);
        if (tempDiv) tempDiv.dataset.msgId = realId;
        displayedMessages.delete(String(tempId));
        displayedMessages.add(realId);
        if (data.message.timestamp_ms && Number(data.message.timestamp_ms) > lastMessageTimestamp) {
          lastMessageTimestamp = Number(data.message.timestamp_ms);
        }
        if (data.message.id && Number(data.message.id) > lastMessageId) {
          lastMessageId = Number(data.message.id);
        }

        // Confirm last timestamp in sidebar
        const serverMs = Number(data.message.timestamp_ms || Date.now());
        if (currentGroupId) bumpConversation("group", currentGroupId, serverMs);
        else bumpConversation("user", currentReceiverId, serverMs);
      } else {
        const tempDiv = document.querySelector(`div[data-msg-id="${tempId}"]`);
        if (tempDiv) {
          tempDiv.style.opacity = "0.7";
          tempDiv.innerHTML += `<div class="message-time" style="color:#dc3545;">⚠️ فشل الإرسال</div>`;
        }
      }
    } catch (_) {
      const tempDiv = document.querySelector(`div[data-msg-id="${tempId}"]`);
      if (tempDiv) {
        tempDiv.style.opacity = "0.7";
        tempDiv.innerHTML += `<div class="message-time" style="color:#dc3545;">⚠️ فشل الإرسال</div>`;
      }
    } finally {
      messageInput.focus();
    }
  }

  // ====== User List Bindings ======
  const usersList = document.getElementById("users-list");
  usersList?.addEventListener("click", (ev) => {
    const li = ev.target.closest("li");
    if (!li) return;
    if (li.id === "btnAddGroup") { openCreateGroupModal(); return; }
    if (li.id === "btnGroupInvites") { openInvitesModal(); return; }
    const groupId = li.getAttribute("data-group-id");
    const userId = li.getAttribute("data-user-id");
    const name = li.getAttribute("data-name");
    if (groupId) { changeGroup(groupId, name); return; }
    if (userId) { changeChat(userId, name); return; }
  });

  form.addEventListener("submit", sendMessage);
  messageInput?.addEventListener("input", () => {
    if (!currentReceiverId && !currentGroupId) return;
    if (!typingActive) {
      typingActive = true;
      emitTyping(true);
    }
    if (typingTimer) clearTimeout(typingTimer);
    typingTimer = setTimeout(() => {
      typingActive = false;
      emitTyping(false);
    }, 1500);
  });
  messageInput?.addEventListener("blur", () => {
    if (typingActive) {
      typingActive = false;
      emitTyping(false);
    }
  });

  // ====== Attachments ======
  function ensureTarget() {
    const gid = (groupInput?.value || "").trim();
    const rid = (receiverInput?.value || "").trim();
    if (gid) { currentGroupId = gid; return { kind: "group", id: gid }; }
    if (rid) { currentReceiverId = rid; return { kind: "user", id: rid }; }
    showToast("⚠️ اختر محادثة أولاً");
    return null;
  }

  async function sendAttachment(file) {
    const t = ensureTarget();
    if (!t || !file) return;
    const mime = (file.type || "").toLowerCase();
    const isImage = mime.startsWith("image/");
    const isAudio = mime.startsWith("audio/");
    const fd = new FormData();
    if (t.kind === "group") fd.append("group_id", t.id);
    else fd.append("receiver_id", t.id);
    let endpoint = "";
    if (isImage) {
      endpoint = (t.kind === "group") ? "/send_group_image" : "/send_image";
      fd.append("image", file, file.name || "image");
    } else if (isAudio) {
      endpoint = (t.kind === "group") ? "/send_group_audio" : "/send_audio";
      fd.append("audio", file, file.name || "audio");
    } else {
      endpoint = (t.kind === "group") ? "/send_group_file" : "/send_file";
      fd.append("file", file, file.name || "file");
    }
    try {
      showToast("📤 جارٍ رفع الملف...");
      const res = await fetch(endpoint, { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data || data.status !== "ok") throw new Error((data?.error) || "فشل إرسال المرفق");
      if (data.message) {
        appendMessage(data.message);
        displayedMessages.add(String(data.message.id));
        hardScrollToBottom();
        showToast("✅ تم إرسال الملف بنجاح");
      }
    } catch (e) {
      showToast("❌ " + (e.message || "فشل إرسال المرفق"));
    } finally {
      if (fileInput) fileInput.value = "";
      if (cameraInput) cameraInput.value = "";
    }
  }

  attachBtn?.addEventListener("click", () => fileInput?.click());
  fileInput?.addEventListener("change", (e) => {
    const f = e.target.files?.[0];
    if (f) sendAttachment(f);
  });
  cameraBtn?.addEventListener("click", () => cameraInput?.click());
  cameraInput?.addEventListener("change", (e) => {
    const f = e.target.files?.[0];
    if (f) sendAttachment(f);
  });

  // ====== Voice Recording ======
  let mediaRecorder = null;
  let audioChunks = [];
  let isRecording = false;

  async function uploadAudioBlob(blob) {
    const t = ensureTarget();
    if (!t) return;
    const formData = new FormData();
    if (t.kind === "group") formData.append("group_id", t.id);
    else formData.append("receiver_id", t.id);
    formData.append("audio", blob, "voice.webm");
    try {
      showToast("📤 جارٍ إرسال التسجيل...");
      const endpoint = (t.kind === "group") ? "/send_group_audio" : "/send_audio";
      const res = await fetch(endpoint, { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "فشل إرسال الصوت");
      if (data.message) {
        appendMessage(data.message);
        displayedMessages.add(String(data.message.id));
        hardScrollToBottom();
        showToast("✅ تم إرسال التسجيل");
      }
    } catch (e) {
      showToast("❌ " + (e.message || "فشل إرسال الصوت"));
    }
  }

  async function toggleRecording() {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      showToast("⚠️ تسجيل الصوت غير مدعوم، اختر ملف صوتي");
      document.getElementById("audio-input")?.click();
      return;
    }
    if (!isRecording) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioChunks = [];
        mediaRecorder = new MediaRecorder(stream);
        mediaRecorder.ondataavailable = (ev) => {
          if (ev.data?.size > 0) audioChunks.push(ev.data);
        };
        mediaRecorder.onstop = async () => {
          try { stream.getTracks().forEach(t => t.stop()); } catch (e) {}
          const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || "audio/webm" });
          if (blob.size > 0) await uploadAudioBlob(blob);
        };
        mediaRecorder.start();
        isRecording = true;
        micBtn?.classList.add("recording");
        if (micBtn) micBtn.innerHTML = '<i class="bi bi-stop-circle-fill"></i>';
        showToast("🎤 جارٍ التسجيل...");
      } catch (e) {
        showToast("❌ لم يتم السماح بالمايكروفون");
      }
    } else {
      try { mediaRecorder?.stop(); } catch (e) {}
      isRecording = false;
      micBtn?.classList.remove("recording");
      if (micBtn) micBtn.innerHTML = '<i class="bi bi-mic"></i>';
    }
  }

  micBtn?.addEventListener("click", toggleRecording);

  const audioInput = document.getElementById("audio-input");
  async function sendAudioFile(file) {
    const t = ensureTarget();
    if (!t || !file) return;
    const fd = new FormData();
    if (t.kind === "group") fd.append("group_id", t.id);
    else fd.append("receiver_id", t.id);
    fd.append("audio", file, file.name || "audio");
    try {
      showToast("📤 جارٍ رفع الملف الصوتي...");
      const res = await fetch(t.kind === "group" ? "/send_group_audio" : "/send_audio", { method: "POST", body: fd });
      const j = await res.json().catch(() => ({}));
      if (!res.ok || !j || j.status !== "ok") { showToast("❌ " + (j?.error || "فشل إرسال الصوت")); return; }
      if (j.message) {
        appendMessage(j.message);
        displayedMessages.add(String(j.message.id));
        hardScrollToBottom();
        showToast("✅ تم إرسال الملف الصوتي");
      }
    } catch (e) {
      showToast("❌ فشل إرسال الصوت");
    }
  }

  audioInput?.addEventListener("change", async () => {
    const f = audioInput.files?.[0];
    audioInput.value = "";
    await sendAudioFile(f);
  });

  messageInput.addEventListener("focus", () => setTimeout(() => hardScrollToBottom(), 150));

  // ====== 🎯 Logout Modal ======
  logoutBtn?.addEventListener("click", () => {
    const modal = document.getElementById("logoutModal");
    const btnCancel = document.getElementById("logoutCancel");
    const btnConfirm = document.getElementById("logoutConfirm");
    if (!modal) return;
    
    modal.classList.remove("hidden");
    
    const closeModal = () => modal.classList.add("hidden");
    
    btnCancel?.addEventListener("click", closeModal, { once: true });
    modal.addEventListener("click", (ev) => { if (ev.target === modal) closeModal(); }, { once: true });
    
    btnConfirm?.addEventListener("click", async () => {
      closeModal();
      showToast("👋 جارٍ تسجيل الخروج...");
      try { 
        await fetch("/logout", { method: "POST" }); 
      } catch (_) {}
      setTimeout(() => {
        window.location.href = "/login";
      }, 500);
    }, { once: true });
  });

  // ====== Emoji Picker ======
  if (emojiToggle && emojiPicker) {
    emojiToggle.addEventListener("click", (e) => {
      e.stopPropagation();
      initEmojiPicker();
      emojiPicker.style.display = emojiPicker.style.display === "grid" ? "none" : "grid";
    });
    document.addEventListener("click", (e) => {
      if (!emojiPicker.contains(e.target) && e.target !== emojiToggle) {
        emojiPicker.style.display = "none";
      }
    });
  }

  // ====== Visibility Change ======
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && (currentReceiverId || currentGroupId)) {
      suppressSound = true;
      initialPaintDone = false;
      loadMessages({ allowSound: false, forceScrollBottom: false }).then(() => {
        setTimeout(() => { suppressSound = false; }, 250);
      });
    }
  });

  window.addEventListener("beforeunload", () => {
    if (typingActive) emitTyping(false);
  });

  // ====== 🎯 Group Modals - Enhanced & Professional ======
  
  // Helper function for API calls
  async function apiPost(url, bodyObj) {
    const opts = { method: "POST", headers: {} };
    if (bodyObj && typeof bodyObj === "object") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(bodyObj);
    }
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    return { res, data };
  }

  // Create Group Modal
  function openCreateGroupModal() {
    const modal = document.getElementById("createGroupModal");
    const cancel = document.getElementById("createGroupCancel");
    const confirm = document.getElementById("createGroupConfirm");
    const nameInput = document.getElementById("groupNameInput");
    const box = document.getElementById("groupMembersBox");
    if (!modal) return;
    
    modal.classList.remove("hidden");
    nameInput?.focus();
    
    const close = () => {
      modal.classList.add("hidden");
      if (nameInput) nameInput.value = "";
      box?.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
    };
    
    cancel?.addEventListener("click", close, { once: true });
    modal.addEventListener("click", (ev) => { if (ev.target === modal) close(); }, { once: true });
    
    if (confirm && !confirm.dataset.bound) {
      confirm.dataset.bound = "1";
      confirm.addEventListener("click", async () => {
        if (confirm.disabled) return;
        const name = (nameInput?.value || "").replace(/\s+/g, " ").trim();
        if (!name) { showToast("⚠️ اسم المجموعة إلزامي"); nameInput?.focus(); return; }
        const members = [];
        box?.querySelectorAll('input[type="checkbox"]:checked')?.forEach((c) => members.push(c.value));
        if (members.length === 0) { showToast("⚠️ اختر عضواً واحداً على الأقل"); return; }
        
        const oldText = confirm.innerHTML;
        confirm.disabled = true;
        confirm.innerHTML = '<i class="bi bi-hourglass-split"></i> جارٍ الإنشاء...';
        
        try {
          const res = await fetch("/api/groups/create", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, members }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.ok) throw new Error((data?.error) || "فشل إنشاء المجموعة");
          
          showToast("✅ تم إنشاء المجموعة بنجاح");
          close();
          setTimeout(() => {
            window.location.href = `/chat?group=${data.group.id}`;
          }, 500);
        } catch (e) {
          showToast("❌ " + (e.message || "فشل إنشاء المجموعة"));
        } finally {
          confirm.disabled = false;
          confirm.innerHTML = oldText;
        }
      });
    }
  }

  // Group Invites Modal
  async function openInvitesModal() {
    const modal = document.getElementById("groupInvitesModal");
    const closeBtn = document.getElementById("invitesClose");
    const list = document.getElementById("invitesList");
    if (!modal || !list) return;
    
    modal.classList.remove("hidden");
    
    const close = () => modal.classList.add("hidden");
    closeBtn?.addEventListener("click", close, { once: true });
    modal.addEventListener("click", (ev) => { if (ev.target === modal) close(); }, { once: true });
    
    list.innerHTML = '<div style="text-align:center;padding:20px;color:#999;"><i class="bi bi-hourglass-split"></i> جارٍ التحميل...</div>';
    
    let invites = [];
    try {
      const res = await fetch("/api/groups/invites", { cache: "no-store" });
      const data = await res.json();
      if (data?.ok) invites = data.invites || [];
    } catch (_) {}
    
    if (!invites || invites.length === 0) {
      list.innerHTML = '<div style="padding:20px;text-align:center;opacity:.7;"><i class="bi bi-inbox" style="font-size:48px;display:block;margin-bottom:12px;"></i><p>لا توجد طلبات حالياً</p></div>';
      return;
    }
    
    list.innerHTML = "";
    invites.forEach((inv) => {
      const card = document.createElement("div");
      card.className = "invite-card";
      card.innerHTML = `
        <div style="display:flex;align-items:center;gap:12px;">
          <div style="width:48px;height:48px;border-radius:50%;background:linear-gradient(135deg,#667eea,#764ba2);display:flex;align-items:center;justify-content:center;color:#fff;font-size:20px;">
            <i class="bi bi-people-fill"></i>
          </div>
          <div>
            <div style="font-weight:700;font-size:15px;">${escapeHtml(inv.group_name || "مجموعة")}</div>
            <div style="font-size:12px;opacity:.8;">دعوة للانضمام</div>
          </div>
        </div>
        <div style="display:flex;gap:8px;">
          <button class="modal-btn secondary" data-act="decline"><i class="bi bi-x-lg"></i> رفض</button>
          <button class="modal-btn primary" data-act="accept"><i class="bi bi-check-lg"></i> قبول</button>
        </div>
      `;
      
      card.querySelectorAll("button").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const action = btn.getAttribute("data-act");
          const oldHtml = btn.innerHTML;
          btn.disabled = true;
          btn.innerHTML = '<i class="bi bi-hourglass-split"></i>';
          
          try {
            const res = await fetch("/api/groups/respond", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ invite_id: inv.invite_id, action }),
            });
            const data = await res.json();
            if (!res.ok || !data.ok) throw new Error((data?.error) || "فشل");
            
            if (action === "accept") {
              showToast("✅ تم قبول الدعوة");
              setTimeout(() => {
                window.location.href = `/chat?group=${inv.group_id}`;
              }, 500);
            } else {
              showToast("✅ تم رفض الدعوة");
              card.remove();
              if (list.childElementCount === 0) {
                list.innerHTML = '<div style="padding:20px;text-align:center;opacity:.7;">لا توجد طلبات حالياً</div>';
              }
            }
          } catch (e) {
            showToast("❌ " + (e.message || "فشل"));
            btn.disabled = false;
            btn.innerHTML = oldHtml;
          }
        });
      });
      
      list.appendChild(card);
    });
  }

  // Edit Group Modal
  function openEditGroupModal(gid, currentName) {
    const modal = document.getElementById("editGroupModal");
    if (!modal || !editGroupNameInput) return;
    
    editGroupNameInput.value = currentName || "";
    modal.classList.remove("hidden");
    modal.dataset.editingGroupId = String(gid);
    editGroupNameInput.focus();
  }

  document.getElementById("editGroupCancel")?.addEventListener("click", () => {
    editGroupModalEl?.classList.add("hidden");
  });

  btnSaveGroupName?.addEventListener("click", async () => {
    const gid = editGroupModalEl?.dataset.editingGroupId;
    if (!gid) return;
    const name = (editGroupNameInput?.value || "").trim();
    if (!name) { showToast("⚠️ اكتب اسم المجموعة"); editGroupNameInput?.focus(); return; }
    
    const oldHtml = btnSaveGroupName.innerHTML;
    btnSaveGroupName.disabled = true;
    btnSaveGroupName.innerHTML = '<i class="bi bi-hourglass-split"></i> جارٍ الحفظ...';
    
    try {
      const { res, data } = await apiPost(`/api/groups/${gid}/update`, { name });
      if (!res.ok || !data?.ok) throw new Error((data?.error) || "فشل التعديل");
      
      // Update sidebar
      const li = document.querySelector(`li[data-group-id="${gid}"]`);
      if (li) {
        li.setAttribute("data-name", data.group?.name || name);
        const nameEl = li.querySelector(".user-name");
        if (nameEl) nameEl.textContent = data.group?.name || name;
      }
      
      // Update chat title if active
      if (String(currentGroupId) === String(gid) && chatTitle) {
        chatTitle.textContent = data.group?.name || name;
      }
      
      editGroupModalEl?.classList.add("hidden");
      showToast("✅ تم تحديث اسم المجموعة");
    } catch (e) {
      showToast("❌ " + (e.message || "فشل التعديل"));
    } finally {
      btnSaveGroupName.disabled = false;
      btnSaveGroupName.innerHTML = oldHtml;
    }
  });

  // Delete Group Modal
  async function deleteGroup(gid) {
    const modal = document.getElementById("deleteGroupModal");
    const btnCancel = document.getElementById("deleteGroupCancel");
    const btnConfirm = document.getElementById("deleteGroupConfirm");
    if (!modal) return;
    
    modal.classList.remove("hidden");
    
    const close = () => modal.classList.add("hidden");
    btnCancel?.addEventListener("click", close, { once: true });
    modal.addEventListener("click", (ev) => { if (ev.target === modal) close(); }, { once: true });
    
    btnConfirm?.addEventListener("click", async () => {
      const oldHtml = btnConfirm.innerHTML;
      btnConfirm.disabled = true;
      btnConfirm.innerHTML = '<i class="bi bi-hourglass-split"></i> جارٍ الحذف...';
      
      try {
        const { data } = await apiPost(`/api/groups/${gid}/delete`);
        if (!data?.ok) throw new Error("فشل الحذف");
        
        close();
        showToast("✅ تم حذف المجموعة");
        setTimeout(() => {
          window.location.href = "/chat";
        }, 500);
      } catch (e) {
        showToast("❌ " + (e.message || "فشل الحذف"));
        btnConfirm.disabled = false;
        btnConfirm.innerHTML = oldHtml;
      }
    }, { once: true });
  }

  // Leave Group Modal
  async function leaveGroup(gid) {
    const modal = document.getElementById("leaveGroupModal");
    const btnCancel = document.getElementById("leaveGroupCancel");
    const btnConfirm = document.getElementById("leaveGroupConfirm");
    if (!modal) return;
    
    modal.classList.remove("hidden");
    
    const close = () => modal.classList.add("hidden");
    btnCancel?.addEventListener("click", close, { once: true });
    modal.addEventListener("click", (ev) => { if (ev.target === modal) close(); }, { once: true });
    
    btnConfirm?.addEventListener("click", async () => {
      const oldHtml = btnConfirm.innerHTML;
      btnConfirm.disabled = true;
      btnConfirm.innerHTML = '<i class="bi bi-hourglass-split"></i> جارٍ المغادرة...';
      
      try {
        const { data } = await apiPost(`/api/groups/${gid}/leave`);
        if (!data?.ok) throw new Error("فشل المغادرة");
        
        close();
        showToast("✅ تم الخروج من المجموعة");
        setTimeout(() => {
          window.location.href = "/chat";
        }, 500);
      } catch (e) {
        showToast("❌ " + (e.message || "فشل المغادرة"));
        btnConfirm.disabled = false;
        btnConfirm.innerHTML = oldHtml;
      }
    }, { once: true });
  }

  // Block/Unblock Group
  async function toggleBlockGroup(gid) {
    try {
      const { data } = await apiPost(`/api/groups/${gid}/block`);
      if (!data?.ok) throw new Error("فشل تنفيذ العملية");
      
      showToast(data.blocked ? "🔕 تم حظر الإشعارات" : "🔔 تم تفعيل الإشعارات");
      groupActionsWrap?.querySelector(".dropdown-menu")?.classList.remove("show");
      groupActionsBtn?.setAttribute("aria-expanded", "false");
    } catch (e) {
      showToast("❌ " + (e.message || "فشل تنفيذ العملية"));
    }
  }

  // Group Actions Bindings
  btnEditGroup?.addEventListener("click", () => {
    if (!currentGroupId) return;
    groupActionsWrap?.querySelector(".dropdown-menu")?.classList.remove("show");
    groupActionsBtn?.setAttribute("aria-expanded", "false");
    const li = document.querySelector(`li[data-group-id="${currentGroupId}"]`);
    const nm = li?.getAttribute("data-name") || "";
    openEditGroupModal(currentGroupId, nm);
  });

  btnDeleteGroup?.addEventListener("click", () => {
    if (currentGroupId) {
      groupActionsWrap?.querySelector(".dropdown-menu")?.classList.remove("show");
      groupActionsBtn?.setAttribute("aria-expanded", "false");
      deleteGroup(currentGroupId);
    }
  });

  btnLeaveGroup?.addEventListener("click", () => {
    if (currentGroupId) {
      groupActionsWrap?.querySelector(".dropdown-menu")?.classList.remove("show");
      groupActionsBtn?.setAttribute("aria-expanded", "false");
      leaveGroup(currentGroupId);
    }
  });

  btnBlockGroup?.addEventListener("click", () => {
    if (currentGroupId) {
      groupActionsWrap?.querySelector(".dropdown-menu")?.classList.remove("show");
      groupActionsBtn?.setAttribute("aria-expanded", "false");
      toggleBlockGroup(currentGroupId);
    }
  });

  // ====== Profile Picture Upload ======
  const profileUpload = document.getElementById("profile-upload");
  profileUpload?.addEventListener("change", async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    
    showToast("📤 جارٍ تحديث الصورة...");
    const fd = new FormData();
    fd.append("profile_pic", file);
    
    try {
      const res = await fetch("/api/update_profile_pic", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok || data.status !== "ok") throw new Error(data.error || "فشل التحديث");
      
      const img = document.getElementById("current-user-img");
      if (img && data.new_pic_url) {
        img.src = data.new_pic_url + "?t=" + Date.now();
      }
      
      showToast("✅ تم تحديث صورة الملف الشخصي");
    } catch (e) {
      showToast("❌ " + (e.message || "فشل تحديث الصورة"));
    } finally {
      profileUpload.value = "";
    }
  });

  // ====== Initialization ======

  document.addEventListener("DOMContentLoaded", () => {
    const bodyId = document.body.getAttribute("data-user-id");
    if (bodyId) currentUserId = parseInt(bodyId, 10);

    requestNotificationPermission();
    initNotificationSettingsUI();
    initSocket();
    // Auto-enable Push subscription if user enabled it previously
    if (readPushEnabled()) {
      ensurePushSubscription().then((ok) => {
        if (!ok) setPushEnabled(false);
      });
    }
    registerServiceWorkerIfNeeded();
    updateGroupActionsVisibility();

    
    if (receiverInput.value || groupInput.value) {
      if (groupInput.value) {
        currentGroupId = String(groupInput.value);
        const gLi = document.querySelector(`li[data-group-id="${currentGroupId}"]`);
        currentGroupIsOwner = (gLi?.getAttribute("data-is-owner") || "0") === "1";
      } else {
        currentReceiverId = String(receiverInput.value);
      }
      
      suppressSound = true;
      initialPaintDone = false;
      updateGroupActionsVisibility();
      
      loadMessages({ allowSound: false, forceScrollBottom: true, updateSidebar: false }).then(() => {
        hardScrollToBottom();
        setTimeout(() => { suppressSound = false; }, 250);
      });

      renderNetStatus();
      closeDrawer();
    } else {
      if (window.innerWidth <= 992) openDrawer();
    }
    refreshSidebarBadges();
    renderNetStatus();
  });

})();
