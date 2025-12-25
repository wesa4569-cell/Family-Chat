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

      t.style.cssText = `padding:14px 20px;border-radius:14px;background:${bg};color:#fff;max-width:90vw;font-size:15px;box-shadow:0 12px 30px rgba(0,0,0,.3);animation:toastSlide 0.3s ease;`;
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
  let scrollRafId = null;
  let reorderRafId = null;
  let badgePollIntervalId = null;
  let badgePollTimeoutId = null;
  let badgeRefreshEnabled = false;

  const MESSAGE_DOM_LIMIT = 100;
  const MESSAGE_PAGE_LIMIT = 50;
  const BADGE_POLL_INTERVAL_MS = 15000;
  const BADGE_POLL_DELAY_MS = 3000;

  // ====== DOM Elements ======
  const messagesDiv = document.getElementById("messages");
  const form = document.getElementById("message-form");
  const messageInput = document.getElementById("message-content");
  const receiverInput = document.getElementById("receiver-id");
  const groupInput = document.getElementById("group-id");
  const chatTitle = document.getElementById("chat-title");
  const chatSubtitle = document.getElementById("chat-subtitle");
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
  const bodyEl = document.body;

  // Reply / Edit bars (WhatsApp-like)
  const replyBar = document.getElementById("replyBar");
  const replyTitle = document.getElementById("replyTitle");
  const replySnippet = document.getElementById("replySnippet");
  const replyClose = document.getElementById("replyClose");
  const editBar = document.getElementById("editBar");
  const editSnippet = document.getElementById("editSnippet");
  const editCancel = document.getElementById("editCancel");
  const replyToInput = document.getElementById("reply-to-id");
  const editMessageIdInput = document.getElementById("edit-message-id");

  // Header actions & modals
  const btnStarred = document.getElementById("btnStarred");
  const btnGroupInfo = document.getElementById("btnGroupInfo");
  const starredModal = document.getElementById("starredModal");
  const starredList = document.getElementById("starredList");
  const starredClose = document.getElementById("starredClose");
  const groupInfoModal = document.getElementById("groupInfoModal");
  const groupMembersList = document.getElementById("groupMembersList");
  const groupInfoClose = document.getElementById("groupInfoClose");
  const btnCreateInviteLink = document.getElementById("btnCreateInviteLink");
  const btnCopyInviteLink = document.getElementById("btnCopyInviteLink");
  const inviteLinkInput = document.getElementById("inviteLinkInput");

  // Message context menu
  const msgMenu = document.getElementById("msgMenu");
  const msgActReply = document.getElementById("msgActReply");
  const msgActForward = document.getElementById("msgActForward");
  const msgActCopy = document.getElementById("msgActCopy");
  const msgActStar = document.getElementById("msgActStar");
  const msgActEdit = document.getElementById("msgActEdit");
  const msgActDeleteMe = document.getElementById("msgActDeleteMe");
  const msgActDeleteAll = document.getElementById("msgActDeleteAll");

  const forwardModal = document.getElementById("forwardModal");
  const forwardTargets = document.getElementById("forwardTargets");
  const forwardCancel = document.getElementById("forwardCancel");
  const forwardConfirm = document.getElementById("forwardConfirm");

  // Cache messages by id for quick actions (reply/edit/copy)
  const messageCache = new Map();
  const groupMessageCache = new Map();
  let selectedMessage = null; // {type:'dm'|'group', id, sender_id, content, message_type}
  let forwardSourceMessage = null; // message being forwarded
  let forwardSelections = new Map(); // key -> {type:'dm'|'group', id}

  if (!messagesDiv || !form || !messageInput || !receiverInput) return;

  // Cache last seen values for quick subtitle rendering
  const lastSeenByUserId = new Map();

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
    // If Bootstrap bundle is present, it can conflict with our manual dropdown.
    // Remove the bootstrap toggle attribute and handle everything here.
    try { groupActionsBtn.removeAttribute("data-bs-toggle"); } catch (_) {}
    groupActionsBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const menu = groupActionsWrap?.querySelector(".dropdown-menu");
      const willOpen = !menu?.classList.contains("show");
      if (willOpen) {
        // Place menu in viewport (fix for mobile clipping)
        if (menu) {
          menu.classList.add("show");
          menu.style.position = "fixed";
          menu.style.inset = "auto";
          menu.style.zIndex = "2600";
          const r = groupActionsBtn.getBoundingClientRect();
          const menuW = Math.min(menu.offsetWidth || 220, window.innerWidth - 16);

          // Open from LEFT and keep inside viewport
          let left = Math.max(8, r.left);
          if (left + menuW > window.innerWidth - 8) left = Math.max(8, window.innerWidth - menuW - 8);

          let top = r.bottom + 8;
          const menuH = menu.offsetHeight || 200;
          if (top + menuH > window.innerHeight - 8) {
            top = Math.max(8, r.top - menuH - 8);
          }

          menu.style.top = `${top}px`;
          menu.style.left = `${left}px`;
          menu.style.right = "auto";
          menu.style.maxWidth = `${window.innerWidth - 16}px`;
        }
        groupActionsBtn.setAttribute("aria-expanded", "true");
      } else {
        if (menu) {
          menu.classList.remove("show");
          menu.style.position = "";
          menu.style.zIndex = "";
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
          menu.style.zIndex = "";
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

  function showMessageSkeleton() {
    if (document.getElementById("messages-skeleton")) return;
    const skel = document.createElement("div");
    skel.id = "messages-skeleton";
    skel.className = "messages-skeleton";
    skel.innerHTML = `
      <div class="skeleton-row"></div>
      <div class="skeleton-row wide"></div>
      <div class="skeleton-row"></div>
      <div class="skeleton-row wide"></div>
      <div class="skeleton-row"></div>
    `;
    messagesDiv.appendChild(skel);
  }

  function hideMessageSkeleton() {
    document.getElementById("messages-skeleton")?.remove();
  }

  // Update sidebar ordering instantly without refreshing the page
  function scheduleReorderConversations() {
    if (reorderRafId) return;
    reorderRafId = requestAnimationFrame(() => {
      reorderRafId = null;
      reorderConversationList();
    });
  }

  function bumpConversation(kind, id, ms) {
    const ts = Number(ms || Date.now());
    let li = null;
    if (kind === "group") li = document.querySelector(`li.user-item[data-group-id="${id}"]`);
    else li = document.querySelector(`li.user-item[data-user-id="${id}"]`);
    if (li) {
      li.setAttribute("data-last-ms", String(ts));
      scheduleReorderConversations();
    }
  }

  function scrollToBottom(force = false) {
    const nearBottom = (messagesDiv.scrollHeight - messagesDiv.scrollTop - messagesDiv.clientHeight) < 120;
    if (force || nearBottom) messagesDiv.scrollTop = messagesDiv.scrollHeight;
  }

  function scheduleScrollToBottom(force = false) {
    if (force) scheduleScrollToBottom.force = true;
    if (scrollRafId) return;
    scrollRafId = requestAnimationFrame(() => {
      scrollRafId = null;
      const doForce = scheduleScrollToBottom.force;
      scheduleScrollToBottom.force = false;
      scrollToBottom(Boolean(doForce));
    });
  }
  scheduleScrollToBottom.force = false;

  function hardScrollToBottom() {
    scheduleScrollToBottom(true);
    requestAnimationFrame(() => scheduleScrollToBottom(true));
  }

  function addDateSeparator(dateStr, container) {
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
    container.appendChild(dateDiv);
  }

  function appendMessageToFragment(msg, fragment) {
    const dkey = localDateKey(msg.timestamp_ms || 0);
    if (dkey && dkey !== lastDate) {
      addDateSeparator(dkey, fragment);
      lastDate = dkey;
    }
    const div = document.createElement("div");
    const isSent = String(msg.sender_id) === String(currentUserId);
    div.className = isSent ? "message sender" : "message receiver";
    div.dataset.msgId = String(msg.id);
    // Type is needed for context menu actions
    const isGroup = Boolean(currentGroupId || msg.group_id);
    div.dataset.msgType = isGroup ? "group" : "dm";
    div.innerHTML = buildMessageInner(msg, isSent);
    fragment.appendChild(div);

    // Cache for quick operations (reply/edit/copy)
    try {
      if (isGroup) groupMessageCache.set(String(msg.id), msg);
      else messageCache.set(String(msg.id), msg);
    } catch (_) {}
  }

  function appendMessageNow(msg) {
    const fragment = document.createDocumentFragment();
    appendMessageToFragment(msg, fragment);
    messagesDiv.appendChild(fragment);
    pruneOldMessages();
  }

  function buildStatusHtml(msg, isSent, isGroup) {
    // WhatsApp-like ticks for direct messages only.
    if (!isSent) return "";
    if (isGroup) return "";
    const isRead = Boolean(msg.read_at || msg.is_read);
    const isDelivered = Boolean(msg.delivered_at) || isRead;
    if (isRead) {
      return `<span class="msg-status read" title="مقروءة"><i class="bi bi-check2-all"></i></span>`;
    }
    if (isDelivered) {
      return `<span class="msg-status" title="تم التسليم"><i class="bi bi-check2-all"></i></span>`;
    }
    return `<span class="msg-status" title="تم الإرسال"><i class="bi bi-check2"></i></span>`;
  }

  function buildMessageInner(msg, isSent) {
    const t = escapeHtml(formatLocalTime(msg.timestamp_ms || 0) || (msg.timestamp ?? ""));
    const type = (msg.message_type || msg.type || "text");
    const mediaUrl = msg.media_url || msg.media || null;
    const isGroup = Boolean(currentGroupId || msg.group_id);
    const senderName = (msg.sender_name || msg.sender || "").trim();
    const senderLine = (isGroup && !isSent && senderName) ? `<div class="msg-sender">${escapeHtml(senderName)}</div>` : "";
    const statusHtml = buildStatusHtml(msg, isSent, isGroup);
    const timeLine = `<div class="message-time">${t}${statusHtml}</div>`;

    // Reply quote (if API provides reply_to)
    let quoteHtml = "";
    try {
      if (msg.reply_to && (msg.reply_to.content || msg.reply_to.id)) {
        const qName = escapeHtml((msg.reply_to.sender_name || "").toString());
        const qText = escapeHtml((msg.reply_to.content || "").toString());
        quoteHtml = `<div class="msg-reply-quote" data-reply-to="${escapeHtml(String(msg.reply_to.id || ""))}"><div class="q-name">${qName || "رسالة"}</div><div class="q-snippet">${qText || ""}</div></div>`;
      }
    } catch (_) {}

    if (type === "image" && mediaUrl) {
      const safeUrl = escapeHtml(mediaUrl);
      return `${senderLine}${quoteHtml}<div class="media-wrap"><a href="${safeUrl}" target="_blank" rel="noopener"><img class="msg-image" src="${safeUrl}" alt="image" loading="lazy"></a></div>${timeLine}`;
    }
    if (type === "audio" && mediaUrl) {
      const safeUrl = escapeHtml(mediaUrl);
      return `${senderLine}${quoteHtml}<div class="media-wrap"><audio controls preload="none" src="${safeUrl}"></audio></div>${timeLine}`;
    }
    if ((type === "video" || (type === "file" && (msg.media_mime || "").toLowerCase().startsWith("video/"))) && mediaUrl) {
      const safeUrl = escapeHtml(mediaUrl);
      return `${senderLine}${quoteHtml}<div class="media-wrap"><video controls preload="metadata" src="${safeUrl}" style="max-width:100%;border-radius:12px;"></video></div>${timeLine}`;
    }
    if (type === "file" && mediaUrl) {
      const safeUrl = escapeHtml(mediaUrl);
      const fname = escapeHtml(msg.content || "ملف");
      const mime2 = escapeHtml(msg.media_mime || "");
      return `${senderLine}${quoteHtml}<div class="file-wrap"><a class="file-link" href="${safeUrl}" target="_blank" rel="noopener"><i class="bi bi-paperclip"></i><span class="file-name">${fname}</span></a>${mime2 ? `<div class="file-mime">${mime2}</div>` : ``}</div>${timeLine}`;
    }
    return `${senderLine}${quoteHtml}<div>${escapeHtml(msg.content || "")}</div>${timeLine}`;
  }

  function pruneOldMessages() {
    const messages = messagesDiv.querySelectorAll(".message");
    if (messages.length <= MESSAGE_DOM_LIMIT) return;
    const excess = messages.length - MESSAGE_DOM_LIMIT;
    for (let i = 0; i < excess; i += 1) {
      messages[i].remove();
    }
  }

  // ====== Message actions (reply / edit / delete / star / forward) ======
  function hideMsgMenu() {
    if (!msgMenu) return;
    msgMenu.classList.add("hidden");
    selectedMessage = null;
  }

  function positionMsgMenu(x, y) {
    if (!msgMenu) return;
    const pad = 10;
    const rect = msgMenu.getBoundingClientRect();
    let left = x;
    let top = y;
    // Keep inside viewport
    if (left + rect.width + pad > window.innerWidth) left = window.innerWidth - rect.width - pad;
    if (top + rect.height + pad > window.innerHeight) top = window.innerHeight - rect.height - pad;
    if (left < pad) left = pad;
    if (top < pad) top = pad;
    msgMenu.style.left = `${left}px`;
    msgMenu.style.top = `${top}px`;
  }

  function getMsgFromCache(type, id) {
    try {
      if (type === "group") return groupMessageCache.get(String(id)) || null;
      return messageCache.get(String(id)) || null;
    } catch (_) {
      return null;
    }
  }

  function openMsgMenuForElement(msgEl, clientX, clientY) {
    if (!msgEl || !msgMenu) return;
    const id = msgEl.dataset.msgId;
    const type = msgEl.dataset.msgType || (currentGroupId ? "group" : "dm");
    const msg = getMsgFromCache(type, id) || { id };
    const isSent = msgEl.classList.contains("sender") || String(msg.sender_id) === String(currentUserId);

    selectedMessage = {
      type,
      id: String(id),
      isSent,
      message_type: (msg.message_type || msg.type || "text"),
      content: (msg.content || ""),
      sender_id: msg.sender_id,
    };

    // Toggle available actions
    if (msgActDeleteAll) msgActDeleteAll.style.display = (type === "dm" && isSent) ? "flex" : "none";
    if (msgActEdit) msgActEdit.style.display = (isSent && (selectedMessage.message_type === "text" || selectedMessage.message_type === "system")) ? "flex" : "none";
    if (msgActStar) msgActStar.style.display = (type === "dm") ? "flex" : "none";

    msgMenu.classList.remove("hidden");
    // Ensure dimensions exist before positioning
    requestAnimationFrame(() => {
      positionMsgMenu(clientX, clientY);
    });
  }

  function setReplyTo(msg) {
    if (!replyBar || !replyToInput || !msg) return;
    replyToInput.value = String(msg.id);
    if (replySnippet) replySnippet.textContent = (msg.content || "").toString().slice(0, 120);
    if (replyTitle) replyTitle.textContent = "رد";
    replyBar.classList.remove("hidden");
    // Cancel edit if active
    if (editBar && !editBar.classList.contains("hidden")) {
      editBar.classList.add("hidden");
      editMessageIdInput.value = "";
    }
    messageInput.focus();
  }

  function clearReply() {
    if (replyBar) replyBar.classList.add("hidden");
    if (replyToInput) replyToInput.value = "";
  }

  function setEdit(msg) {
    if (!editBar || !editMessageIdInput || !msg) return;
    editMessageIdInput.value = String(msg.id);
    if (editSnippet) editSnippet.textContent = (msg.content || "").toString().slice(0, 120);
    editBar.classList.remove("hidden");
    // Cancel reply if active
    clearReply();
    messageInput.value = (msg.content || "").toString();
    messageInput.focus();
  }

  function clearEdit() {
    if (editBar) editBar.classList.add("hidden");
    if (editMessageIdInput) editMessageIdInput.value = "";
    if (editSnippet) editSnippet.textContent = "";
  }

  async function apiDeleteForMe(sel) {
    if (!sel) return;
    const url = sel.type === "group" ? `/api/group_messages/${sel.id}/delete_for_me` : `/api/messages/${sel.id}/delete_for_me`;
    const r = await fetch(url, { method: "POST", credentials: "same-origin" });
    if (r.ok) {
      // Remove from DOM immediately
      const el = messagesDiv.querySelector(`.message[data-msg-id="${CSS.escape(String(sel.id))}"]`);
      if (el) el.remove();
      showToast("تم الحذف عندك", "success");
    } else {
      showToast("تعذر الحذف", "error");
    }
  }

  async function apiDeleteForAll(sel) {
    if (!sel) return;
    if (sel.type !== "dm") return;
    const r = await fetch(`/api/messages/${sel.id}/delete_for_all`, { method: "POST", credentials: "same-origin" });
    if (r.ok) {
      showToast("تم الحذف للجميع", "success");
    } else {
      showToast("تعذر الحذف للجميع", "error");
    }
  }

  async function apiStar(sel) {
    if (!sel || sel.type !== "dm") return;
    // Toggle using local marker (frontend only). Backend stores per-user.
    const enabled = !Boolean(sel._starred);
    const r = await fetch(`/api/messages/${sel.id}/star`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ enable: enabled })
    });
    if (r.ok) {
      sel._starred = enabled;
      showToast(enabled ? "تم تمييز الرسالة" : "تم إلغاء التمييز", "success");
    } else {
      showToast("تعذر تمييز الرسالة", "error");
    }
  }

  function openForwardModal(sel) {
    if (!forwardModal || !forwardTargets || !sel) return;
    forwardTargets.innerHTML = "";
    forwardSourceMessage = sel;
    forwardSelections = new Map();

    const items = Array.from(document.querySelectorAll("#users-list li.user-item[data-user-id], #users-list li.user-item[data-group-id]"));
    if (!items.length) {
      forwardTargets.innerHTML = `<div class="members-loading">لا توجد محادثات</div>`;
    }
    items.forEach((li) => {
      const isGroup = li.hasAttribute("data-group-id");
      const id = isGroup ? li.getAttribute("data-group-id") : li.getAttribute("data-user-id");
      const name = (li.getAttribute("data-name") || "").toString();
      const row = document.createElement("div");
      row.className = "member-row";
      row.style.cursor = "pointer";
      row.setAttribute("role", "button");
      row.setAttribute("tabindex", "0");
      row.dataset.ftType = isGroup ? "group" : "dm";
      row.dataset.ftId = String(id || "");
      row.innerHTML = `<div style="font-weight:800;">${escapeHtml(name || (isGroup ? "مجموعة" : "مستخدم"))}</div><div style="margin-inline-start:auto;opacity:.75;font-size:12px;">${isGroup ? "مجموعة" : "محادثة"}</div>`;

      const togglePick = () => {
        const t = row.dataset.ftType;
        const i = row.dataset.ftId;
        if (!t || !i) return;
        const target = { type: t === "group" ? "group" : "dm", id: String(i) };
        const key = `${target.type}:${target.id}`;
        if (forwardSelections.has(key)) {
          forwardSelections.delete(key);
          row.classList.remove("selected");
        } else {
          forwardSelections.set(key, target);
          row.classList.add("selected");
        }
        if (forwardConfirm) forwardConfirm.disabled = (forwardSelections.size === 0);
      };

      // Some browsers/devices may not fire click reliably inside scrollable containers,
      // so we listen to both click and pointer/touch events.
      row.addEventListener("click", togglePick);
      row.addEventListener("pointerdown", togglePick, { passive: true });
      row.addEventListener("touchstart", togglePick, { passive: true });
      row.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); togglePick(); }
      });
      forwardTargets.appendChild(row);
    });

    forwardModal.classList.remove("hidden");
    if (forwardConfirm) forwardConfirm.disabled = true;
  }

  async function apiForward(sel, target, opts = {}) {
    if (!sel || !target) return;
    if ((sel.message_type || "text") !== "text") {
      if (!opts.silent) showToast("إعادة توجيه الوسائط غير مدعوم حالياً", "warning");
      return;
    }
    // Use lightweight endpoint if available, else fallback to sending new text message.
    try {
      const url = sel.type === "group" ? `/api/group_messages/${sel.id}/forward` : `/api/messages/${sel.id}/forward`;
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ target_type: target.type, target_id: target.id })
      });
      if (r.ok) {
        if (!opts.silent) showToast("تمت إعادة التوجيه", "success");
        return;
      }
    } catch (_) {}

    // Fallback
    const fd = new FormData();
    fd.append("content", sel.content || "");
    if (target.type === "group") {
      fd.append("group_id", target.id);
      await fetch("/send_group_message", { method: "POST", body: fd, credentials: "same-origin" });
    } else {
      fd.append("receiver_id", target.id);
      await fetch("/send_message", { method: "POST", body: fd, credentials: "same-origin" });
    }
    if (!opts.silent) showToast("تمت إعادة التوجيه", "success");
  }

  function initMessageActionsUI() {
    if (!messagesDiv) return;

    // Close menu on outside click / scroll
    document.addEventListener("click", (e) => {
      if (!msgMenu) return;
      if (msgMenu.classList.contains("hidden")) return;
      const t = e.target;
      if (t && msgMenu.contains(t)) return;
      hideMsgMenu();
    });
    messagesDiv.addEventListener("scroll", () => hideMsgMenu(), { passive: true });
    window.addEventListener("resize", () => hideMsgMenu());

    // Right click
    messagesDiv.addEventListener("contextmenu", (e) => {
      const el = e.target?.closest?.(".message");
      if (!el) return;
      e.preventDefault();
      openMsgMenuForElement(el, e.clientX, e.clientY);
    });

    // Long-press for touch
    let lpTimer = null;
    messagesDiv.addEventListener("touchstart", (e) => {
      const el = e.target?.closest?.(".message");
      if (!el) return;
      const t = e.touches && e.touches[0];
      if (!t) return;
      lpTimer = setTimeout(() => {
        openMsgMenuForElement(el, t.clientX, t.clientY);
      }, 550);
    }, { passive: true });
    messagesDiv.addEventListener("touchend", () => { if (lpTimer) clearTimeout(lpTimer); lpTimer = null; }, { passive: true });
    messagesDiv.addEventListener("touchmove", () => { if (lpTimer) clearTimeout(lpTimer); lpTimer = null; }, { passive: true });

    // Quote click: scroll to replied message if exists in DOM
    messagesDiv.addEventListener("click", (e) => {
      const q = e.target?.closest?.(".msg-reply-quote");
      if (!q) return;
      const id = q.getAttribute("data-reply-to");
      if (!id) return;
      const mEl = messagesDiv.querySelector(`.message[data-msg-id="${CSS.escape(String(id))}"]`);
      if (mEl) mEl.scrollIntoView({ behavior: "smooth", block: "center" });
    });

    // Action buttons
    msgActReply?.addEventListener("click", () => {
      const sel = selectedMessage;
      hideMsgMenu();
      const msg = getMsgFromCache(sel?.type, sel?.id);
      if (msg) setReplyTo(msg);
    });
    msgActEdit?.addEventListener("click", () => {
      const sel = selectedMessage;
      hideMsgMenu();
      const msg = getMsgFromCache(sel?.type, sel?.id);
      if (msg) setEdit(msg);
    });
    msgActCopy?.addEventListener("click", async () => {
      const sel = selectedMessage;
      hideMsgMenu();
      const txt = (sel?.content || "").toString();
      try {
        await navigator.clipboard.writeText(txt);
        showToast("تم النسخ", "success");
      } catch (_) {
        showToast("تعذر النسخ", "error");
      }
    });
    msgActStar?.addEventListener("click", async () => {
      const sel = selectedMessage;
      hideMsgMenu();
      if (!sel) return;
      await apiStar(sel);
    });
    msgActDeleteMe?.addEventListener("click", async () => {
      const sel = selectedMessage;
      hideMsgMenu();
      await apiDeleteForMe(sel);
    });
    msgActDeleteAll?.addEventListener("click", async () => {
      const sel = selectedMessage;
      hideMsgMenu();
      await apiDeleteForAll(sel);
    });
    msgActForward?.addEventListener("click", () => {
      const sel = selectedMessage;
      hideMsgMenu();
      openForwardModal(sel);
    });

    // Reply/Edit close
    replyClose?.addEventListener("click", () => clearReply());
    editCancel?.addEventListener("click", () => clearEdit());

    // Forward modal
    forwardCancel?.addEventListener("click", () => {
      if (forwardModal) forwardModal.classList.add("hidden");
    });
    forwardConfirm?.addEventListener("click", async () => {
      const sel = forwardSourceMessage;
      const targets = Array.from(forwardSelections.values());
      if (!sel || !targets.length) return;
      if (forwardModal) forwardModal.classList.add("hidden");
      // Forward sequentially to keep the UI responsive and avoid flooding the server
      for (const t of targets) {
        try {
          await apiForward(sel, t, { silent: true });
        } catch (_) {}
      }
      showToast("تمت إعادة التوجيه", "success");
    });
  }

  function initHeaderModalsUI() {
    // Starred
    btnStarred?.addEventListener("click", async () => {
      if (!starredModal || !starredList) return;
      starredList.innerHTML = `<div class="members-loading">جارٍ التحميل...</div>`;
      starredModal.classList.remove("hidden");
      try {
        const r = await fetch("/api/starred", { credentials: "same-origin" });
        if (!r.ok) throw new Error("bad");
        const j = await r.json();
        if (!j || !j.ok) throw new Error("bad");
        const arr = Array.isArray(j.messages) ? j.messages : [];
        if (!arr.length) {
          starredList.innerHTML = `<div class="members-loading">لا توجد رسائل مميزة</div>`;
          return;
        }
        const frag = document.createDocumentFragment();
        arr.forEach((m) => {
          const row = document.createElement("div");
          row.className = "starred-row";
          // Include an "unstar" button so user can remove the highlight.
          const top = `
            <div class="sr-top">
              <div class="sr-name">${escapeHtml(m.chat_name || "")}</div>
              <div style="margin-inline-start:auto;display:flex;align-items:center;gap:10px;">
                <div class="sr-time">${escapeHtml(m.timestamp || "")}</div>
                <button type="button" class="modal-btn secondary" data-unstar="1" data-msg-id="${escapeHtml(String(m.message_id))}" style="padding:6px 10px;border-radius:10px;">إلغاء</button>
              </div>
            </div>`;
          const text = `<div class="sr-text">${escapeHtml(m.content || "")}</div>`;
          row.innerHTML = top + text;
          frag.appendChild(row);
        });
        starredList.innerHTML = "";
        starredList.appendChild(frag);

        // Delegate unstar actions
        starredList.onclick = async (ev) => {
          const btn = ev.target?.closest?.("button[data-unstar][data-msg-id]");
          if (!btn) return;
          const mid = btn.getAttribute("data-msg-id");
          if (!mid) return;
          btn.disabled = true;
          try {
            const r2 = await fetch(`/api/messages/${mid}/star`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              credentials: "same-origin",
              body: JSON.stringify({ enable: false })
            });
            if (!r2.ok) throw new Error("bad");
            // Remove the row from UI
            const card = btn.closest?.(".starred-row");
            card?.remove();
            showToast("تم إلغاء التمييز", "success");
            if (!starredList.querySelector(".starred-row")) {
              starredList.innerHTML = `<div class="members-loading">لا توجد رسائل مميزة</div>`;
            }
          } catch (_) {
            btn.disabled = false;
            showToast("تعذر إلغاء التمييز", "error");
          }
        };
      } catch (_) {
        starredList.innerHTML = `<div class="members-loading error">تعذر تحميل الرسائل المميزة</div>`;
      }
    });
    starredClose?.addEventListener("click", () => { starredModal?.classList.add("hidden"); });

    // Group info
    btnGroupInfo?.addEventListener("click", async () => {
      if (!groupInfoModal || !groupMembersList || !currentGroupId) return;
      groupMembersList.innerHTML = `<div class="members-loading">جارٍ التحميل...</div>`;
      inviteLinkInput && (inviteLinkInput.value = "");
      if (btnCopyInviteLink) { btnCopyInviteLink.disabled = true; }
      groupInfoModal.classList.remove("hidden");
      try {
        const r = await fetch(`/api/groups/${currentGroupId}/members`, { credentials: "same-origin" });
        if (!r.ok) throw new Error("bad");
        const j = await r.json();
        if (!j || !j.ok) throw new Error("bad");
        const arr = Array.isArray(j.members) ? j.members : [];
        if (!arr.length) {
          groupMembersList.innerHTML = `<div class="members-loading">لا يوجد أعضاء</div>`;
          return;
        }
        const meRole = (j.my_role || "member").toString();
        const frag = document.createDocumentFragment();
        arr.forEach((u) => {
          const row = document.createElement("div");
          row.className = "member-row";
          const roleLabel = u.role === "owner" ? "المالك" : (u.role === "admin" ? "مشرف" : "عضو");
          const canManage = (meRole === "owner") && String(u.user_id) !== String(currentUserId);
          row.innerHTML = `
            <div style="display:flex;align-items:center;gap:10px;width:100%;">
              <div style="font-weight:900;">${escapeHtml(u.name || "")}</div>
              <div style="opacity:.75;font-size:12px;">${escapeHtml(u.phone_number || "")}</div>
              <div style="margin-inline-start:auto;display:flex;gap:8px;align-items:center;">
                <span style="font-size:12px;opacity:.85;font-weight:900;">${roleLabel}</span>
                ${canManage && u.role !== "admin" ? `<button class="modal-btn secondary" data-act="promote" data-uid="${escapeHtml(String(u.user_id))}" style="padding:6px 8px;border-radius:10px;">ترقية</button>` : ""}
                ${canManage && u.role === "admin" ? `<button class="modal-btn secondary" data-act="demote" data-uid="${escapeHtml(String(u.user_id))}" style="padding:6px 8px;border-radius:10px;">إلغاء</button>` : ""}
              </div>
            </div>
          `;
          frag.appendChild(row);
        });
        groupMembersList.innerHTML = "";
        groupMembersList.appendChild(frag);

        // Wire promote/demote
        groupMembersList.querySelectorAll("button[data-act]").forEach((btn) => {
          btn.addEventListener("click", async (ev) => {
            ev.preventDefault();
            const act = btn.getAttribute("data-act");
            const uid = btn.getAttribute("data-uid");
            if (!uid || !act) return;
            const url = act === "promote" ? `/api/groups/${currentGroupId}/members/${uid}/promote` : `/api/groups/${currentGroupId}/members/${uid}/demote`;
            const rr = await fetch(url, { method: "POST", credentials: "same-origin" });
            if (rr.ok) {
              showToast("تم التحديث", "success");
              // Refresh list
              btnGroupInfo.click();
            } else {
              showToast("تعذر تحديث الصلاحيات", "error");
            }
          });
        });
      } catch (_) {
        groupMembersList.innerHTML = `<div class="members-loading error">تعذر تحميل الأعضاء</div>`;
      }
    });
    groupInfoClose?.addEventListener("click", () => { groupInfoModal?.classList.add("hidden"); });

    btnCreateInviteLink?.addEventListener("click", async () => {
      if (!currentGroupId) return;
      try {
        const r = await fetch(`/api/groups/${currentGroupId}/invite_link`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({})
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || !j || !j.ok) throw new Error("bad");
        if (inviteLinkInput) inviteLinkInput.value = j.url || "";
        if (btnCopyInviteLink) btnCopyInviteLink.disabled = !(j.url);
        showToast("تم إنشاء رابط الدعوة", "success");
      } catch (_) {
        showToast("تعذر إنشاء رابط الدعوة", "error");
      }
    });
    btnCopyInviteLink?.addEventListener("click", async () => {
      const v = inviteLinkInput?.value || "";
      if (!v) return;
      try {
        await navigator.clipboard.writeText(v);
        showToast("تم نسخ الرابط", "success");
      } catch (_) {
        showToast("تعذر نسخ الرابط", "error");
      }
    });
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
    const isInitial = lastMessageId === 0 && lastMessageTimestamp === 0;
    const limitParam = isInitial ? `&limit=${MESSAGE_PAGE_LIMIT}` : "";
    const url = currentGroupId
      ? `/get_group_messages/${currentGroupId}?last_id=${lastMessageId}${limitParam}`
      : `/get_messages/${currentReceiverId}?since=${lastMessageTimestamp}&last_id=${lastMessageId}${limitParam}`;
    try {
      if (isInitial) showMessageSkeleton();
      console.time("loadMessages");
      const res = await fetch(url, { cache: "no-store" });
      const data = await res.json();
      if (!Array.isArray(data)) return;
      if (data.length > 0) hideEmptyState();

      let hasNewIncoming = false, newAdded = false;
      let newestMs = 0;
      const fragment = document.createDocumentFragment();
      for (const msg of data) {
        if (msg.id && Number(msg.id) > lastMessageId) lastMessageId = Number(msg.id);
        if (msg.timestamp_ms && Number(msg.timestamp_ms) > lastMessageTimestamp) lastMessageTimestamp = Number(msg.timestamp_ms);
        const key = String(msg.id);
        if (displayedMessages.has(key)) continue;
        appendMessageToFragment(msg, fragment);
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

      if (fragment.childNodes.length) {
        requestAnimationFrame(() => {
          messagesDiv.appendChild(fragment);
          pruneOldMessages();
          if (newAdded) {
            if (forceScrollBottom) hardScrollToBottom();
            else scheduleScrollToBottom(false);
          } else if (forceScrollBottom) {
            hardScrollToBottom();
          }
        });
      } else if (forceScrollBottom) {
        hardScrollToBottom();
      }

      // Keep sidebar order in sync with latest message without refreshing the page
      if (updateSidebar && newestMs > 0) {
        if (currentGroupId) bumpConversation("group", currentGroupId, newestMs);
        else bumpConversation("user", currentReceiverId, newestMs);
      }

      if (allowSound && !suppressSound && initialPaintDone && hasNewIncoming) {
        audioManager.play('message');
      }
      if (!initialPaintDone) initialPaintDone = true;
    } catch (_) {
    } finally {
      hideMessageSkeleton();
      console.timeEnd("loadMessages");
    }
  }

  // ====== Sidebar Badges Refresh ======
  async function refreshSidebarBadges() {
    if (!badgeRefreshEnabled) return;
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

  function startBadgePolling() {
    if (badgePollIntervalId) return;
    badgePollIntervalId = setInterval(() => refreshSidebarBadges(), BADGE_POLL_INTERVAL_MS);
  }

  function stopBadgePolling() {
    if (!badgePollIntervalId) return;
    clearInterval(badgePollIntervalId);
    badgePollIntervalId = null;
  }

  function updateConversationFlags(li){
    if(!li) return;
    const pin = li.querySelector('.flag-pin');
    const mute = li.querySelector('.flag-mute');
    const arch = li.querySelector('.flag-arch');

    const pinnedRankRaw = (li.getAttribute('data-pinned-rank') || '').trim();
    const isPinned = pinnedRankRaw !== '';
    const isArchived = (li.getAttribute('data-archived') || '0') === '1';
    const mutedUntil = (li.getAttribute('data-muted-until') || '').trim();
    const isMuted = mutedUntil !== '' && (new Date(mutedUntil).getTime() > Date.now());

    if(pin) pin.style.display = isPinned ? 'inline' : 'none';
    if(mute) mute.style.display = isMuted ? 'inline' : 'none';
    if(arch) arch.style.display = isArchived ? 'inline' : 'none';

    li.classList.toggle('archived', isArchived);
  }

  function reorderConversationList() {
    const ul = document.getElementById("users-list");
    if (!ul) return;

    // Keep special items on top
    const special = [];
    const add = document.getElementById("btnAddGroup");
    const inv = document.getElementById("btnGroupInvites");
    if (add) special.push(add);
    if (inv) special.push(inv);

    const items = Array.from(ul.querySelectorAll('li.user-item[data-user-id], li.user-item[data-group-id]'));
    items.forEach(updateConversationFlags);

    items.sort((a, b) => {
      const aArchived = (a.getAttribute("data-archived") || "0") === "1";
      const bArchived = (b.getAttribute("data-archived") || "0") === "1";
      if (aArchived !== bArchived) return aArchived ? 1 : -1;

      const aPinRaw = (a.getAttribute("data-pinned-rank") || "").trim();
      const bPinRaw = (b.getAttribute("data-pinned-rank") || "").trim();
      const aPin = aPinRaw === "" ? 1e9 : Number(aPinRaw);
      const bPin = bPinRaw === "" ? 1e9 : Number(bPinRaw);
      if (aPin !== bPin) return aPin - bPin;

      const am = Number(a.getAttribute("data-last-ms") || 0);
      const bm = Number(b.getAttribute("data-last-ms") || 0);
      if (bm !== am) return bm - am;

      const an = (a.getAttribute("data-name") || "").toLowerCase();
      const bn = (b.getAttribute("data-name") || "").toLowerCase();
      return an.localeCompare(bn, "ar");
    });

    // Re-append
    special.forEach((el) => { if (el && el.parentElement === ul) ul.appendChild(el); });
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
    // لا نعرض حالة تواجد المستخدم هنا لتجنب تكرارها؛
    // التواجد يعرض أسفل الاسم عبر chatSubtitle.
    netStatus.textContent = "";
  }

  function formatLastSeen(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return "";
      const now = new Date();
      const dateKey = d.toISOString().split("T")[0];
      const todayKey = now.toISOString().split("T")[0];
      const yKey = new Date(now.getTime() - 86400000).toISOString().split("T")[0];
      const timeStr = d.toLocaleTimeString("ar-EG", { hour: "2-digit", minute: "2-digit" });
      if (dateKey === todayKey) return `آخر ظهور اليوم ${timeStr}`;
      if (dateKey === yKey) return `آخر ظهور أمس ${timeStr}`;
      const dateStr = d.toLocaleDateString("ar-EG", { day: "numeric", month: "long" });
      return `آخر ظهور ${dateStr} ${timeStr}`;
    } catch (_) {
      return "";
    }
  }

  async function refreshActivePresence(forceFetch = false) {
    if (!chatSubtitle) return;
    if (!currentReceiverId) {
      chatSubtitle.textContent = "";
      return;
    }
    const uid = String(currentReceiverId);
    const isOnline = onlineUsers.has(uid);
    if (isOnline) {
      chatSubtitle.textContent = "متصل الآن";
      return;
    }
    const cached = lastSeenByUserId.get(uid);
    if (cached && !forceFetch) {
      chatSubtitle.textContent = formatLastSeen(cached) || "غير متصل";
      return;
    }
    try {
      const res = await fetch(`/api/users/${encodeURIComponent(uid)}/presence`, { credentials: "same-origin" });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data?.ok) {
        if (data.last_seen) lastSeenByUserId.set(uid, data.last_seen);
        if (data.online) {
          chatSubtitle.textContent = "متصل الآن";
        } else {
          chatSubtitle.textContent = formatLastSeen(data.last_seen) || "غير متصل";
        }
        return;
      }
    } catch (_) {}
    chatSubtitle.textContent = "غير متصل";
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
      appendMessageNow(msg);
      displayedMessages.add(key);
      if (msg.timestamp_ms && Number(msg.timestamp_ms) > lastMessageTimestamp) {
        lastMessageTimestamp = Number(msg.timestamp_ms);
      }
      if (msg.id && Number(msg.id) > lastMessageId) {
        lastMessageId = Number(msg.id);
      }
      scheduleScrollToBottom(false);
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

  function getInitialGroupIds() {
    const raw = bodyEl?.getAttribute("data-group-ids") || "[]";
    try {
      const ids = JSON.parse(raw);
      return Array.isArray(ids) ? ids.map((id) => String(id)) : [];
    } catch (_) {
      return [];
    }
  }

  function initSocket() {
    if (!window.io) return;
    if (socket) return;
    socket = window.io({ transports: ["websocket", "polling"] });
    socket.on("connect", () => {
      socketConnected = true;
      const groupIds = getInitialGroupIds();
      joinGroupRooms(groupIds);
      renderNetStatus();
    });
    socket.on("disconnect", () => {
      socketConnected = false;
      setTypingIndicator("");
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
      if (String(id) === String(currentReceiverId)) {
        refreshActivePresence(true);
      }
    });
    socket.on("message_status", (data) => {
      try {
        if (!data || data.type !== "dm") return;
        const status = data.status;
        const ids = Array.isArray(data.message_ids) ? data.message_ids : [];
        if (ids.length === 0) return;
        ids.forEach((mid) => {
          const el = document.querySelector(`.message.sender[data-msg-id="${mid}"]`);
          if (!el) return;
          // Update stored attrs for re-render safety
          if (status === "delivered") el.dataset.deliveredAt = data.at || data.delivered_at || "1";
          if (status === "read") el.dataset.readAt = data.at || data.read_at || "1";
          // Update ticks inside the time line
          const timeEl = el.querySelector(".message-time");
          if (!timeEl) return;
          const existing = timeEl.querySelector(".msg-status");
          let html = "";
          if (status === "read") {
            html = `<span class="msg-status read" title="مقروءة"><i class="bi bi-check2-all"></i></span>`;
          } else if (status === "delivered") {
            html = `<span class="msg-status" title="تم التسليم"><i class="bi bi-check2-all"></i></span>`;
          } else {
            html = `<span class="msg-status" title="تم الإرسال"><i class="bi bi-check2"></i></span>`;
          }
          if (existing) existing.outerHTML = html;
          else timeEl.insertAdjacentHTML("beforeend", html);
        });
      } catch (_) {}
    });
    socket.on("typing", handleTypingEvent);
    socket.on("new_message", handleIncomingMessage);
    socket.on("message_edited", (data) => {
      try {
        const msg = data?.message;
        if (!msg || !msg.id) return;
        const t = data?.type || (msg.group_id ? "group" : "dm");
        const el = messagesDiv.querySelector(`.message[data-msg-id="${CSS.escape(String(msg.id))}"]`);
        if (t === "group") groupMessageCache.set(String(msg.id), msg);
        else messageCache.set(String(msg.id), msg);
        if (el) {
          const isSent = String(msg.sender_id) === String(currentUserId);
          el.innerHTML = buildMessageInner(msg, isSent);
        }
      } catch (_) {}
    });
    socket.on("message_deleted", (data) => {
      try {
        if (!data || !data.message_id) return;
        const id = String(data.message_id);
        const el = messagesDiv.querySelector(`.message[data-msg-id="${CSS.escape(id)}"]`);
        if (!el) return;
        // Replace content with placeholder
        const isSent = el.classList.contains("sender");
        const msg = getMsgFromCache(data.type === "group" ? "group" : "dm", id) || { id: id, sender_id: (isSent ? currentUserId : null), content: "" };
        msg.content = "تم حذف هذه الرسالة";
        msg.message_type = "deleted";
        el.innerHTML = buildMessageInner(msg, isSent);
      } catch (_) {}
    });
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
    if (chatSubtitle) chatSubtitle.textContent = "";
    const chatAvatar = document.getElementById("chat-avatar");
    const li = document.querySelector(`li[data-user-id="${userId}"]`);
    const av = li?.getAttribute("data-avatar");
    if (chatAvatar) {
      chatAvatar.style.display = "inline-block";
      if (av) chatAvatar.src = av;
    }
    resetConversation();
    updateGroupActionsVisibility();
    refreshActivePresence(true);
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
    if (chatSubtitle) chatSubtitle.textContent = "";
    if (chatSubtitle) chatSubtitle.textContent = "";
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

    // Edit mode
    const editId = (editMessageIdInput?.value || "").trim();
    if (editId) {
      try {
        const endpoint = currentGroupId ? `/api/group_messages/${editId}/edit` : `/api/messages/${editId}/edit`;
        const r = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ content })
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || !j || !j.ok) throw new Error("bad");
        // Update DOM if exists
        const el = messagesDiv.querySelector(`.message[data-msg-id="${CSS.escape(String(editId))}"]`);
        if (el) {
          const msg = getMsgFromCache(currentGroupId ? "group" : "dm", String(editId)) || {};
          msg.content = content;
          msg.edited_at = j.edited_at || new Date().toISOString();
          if (currentGroupId) groupMessageCache.set(String(editId), msg);
          else messageCache.set(String(editId), msg);
          const isSent = true;
          el.innerHTML = buildMessageInner(msg, isSent);
        }
        showToast("تم تعديل الرسالة", "success");
      } catch (_) {
        showToast("تعذر تعديل الرسالة", "error");
      } finally {
        clearEdit();
        messageInput.value = "";
        messageInput.focus();
      }
      return;
    }

    const replyToId = (replyToInput?.value || "").trim();
    const tempId = "temp-" + Date.now();
    const tempMsg = {
      id: tempId,
      sender_id: currentUserId,
      receiver_id: currentReceiverId,
      content,
      message_type: "text",
      reply_to_id: replyToId ? replyToId : null,
      timestamp_ms: Date.now(),
      timestamp: new Date().toLocaleTimeString("ar-EG", { hour: "2-digit", minute: "2-digit" }),
      date: new Date().toISOString().split("T")[0],
    };
    appendMessageNow(tempMsg);
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
      if (replyToId) formData.append("reply_to_id", replyToId);
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
      if (replyToId) clearReply();
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
        appendMessageNow(data.message);
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
        appendMessageNow(data.message);
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
        appendMessageNow(j.message);
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
    if (document.hidden) {
      stopBadgePolling();
      return;
    }
    if (badgeRefreshEnabled) {
      startBadgePolling();
      refreshSidebarBadges();
    }
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
    if (badgePollTimeoutId) clearTimeout(badgePollTimeoutId);
    stopBadgePolling();
    if (typingTimer) clearTimeout(typingTimer);
    if (typingHideTimer) clearTimeout(typingHideTimer);
    try { socket?.disconnect(); } catch (_) {}
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
  function renderGroupMembers(users, box) {
    if (!box) return;
    const fragment = document.createDocumentFragment();
    users.forEach((user) => {
      const label = document.createElement("label");
      label.className = "member-row";
      label.innerHTML = `
        <input type="checkbox" value="${escapeHtml(String(user.id))}">
        <span>${escapeHtml(user.name)} (${escapeHtml(user.phone_number || "")})</span>
      `;
      fragment.appendChild(label);
    });
    box.innerHTML = "";
    box.appendChild(fragment);
  }

  async function ensureGroupMembersLoaded(box) {
    if (!box || box.dataset.loaded === "1") return;
    box.innerHTML = '<div class="members-loading"><span class="skeleton-line"></span><span class="skeleton-line"></span><span class="skeleton-line"></span></div>';
    try {
      const res = await fetch("/api/users", { cache: "no-store" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data?.ok || !Array.isArray(data.users)) throw new Error("fetch_failed");
      renderGroupMembers(data.users, box);
      box.dataset.loaded = "1";
    } catch (_) {
      box.innerHTML = '<div class="members-loading error">تعذر تحميل الأعضاء. حاول مرة أخرى.</div>';
    }
  }

  function openCreateGroupModal() {
    const modal = document.getElementById("createGroupModal");
    const cancel = document.getElementById("createGroupCancel");
    const confirm = document.getElementById("createGroupConfirm");
    const nameInput = document.getElementById("groupNameInput");
    const box = document.getElementById("groupMembersBox");
    if (!modal) return;
    
    modal.classList.remove("hidden");
    nameInput?.focus();
    ensureGroupMembersLoaded(box);
    
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


  // ===== Conversation actions (pin/archive/mute) =====
  function hideConvMenu() {
    const m = document.getElementById("convCtxMenu");
    if (m) m.remove();
    document.removeEventListener("click", hideConvMenu, true);
    document.removeEventListener("contextmenu", hideConvMenu, true);
  }

  async function postConversationSettings(li, payload) {
    const isGroup = !!li.getAttribute("data-group-id");
    const id = isGroup ? li.getAttribute("data-group-id") : li.getAttribute("data-user-id");
    const type = isGroup ? "group" : "dm";
    const res = await fetch(`/api/conversations/${type}/${id}/settings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {})
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok || !j.ok) throw new Error(j.error || "request_failed");
  }

  function showConvMenuFor(li, x, y) {
    hideConvMenu();
    const menu = document.createElement("div");
    menu.id = "convCtxMenu";
    menu.style.position = "fixed";
    menu.style.left = Math.max(8, Math.min(x, window.innerWidth - 240)) + "px";
    menu.style.top = Math.max(8, Math.min(y, window.innerHeight - 260)) + "px";
    menu.style.width = "220px";
    menu.style.background = "rgba(255,255,255,0.98)";
    menu.style.border = "1px solid rgba(0,0,0,0.08)";
    menu.style.boxShadow = "0 10px 30px rgba(0,0,0,0.18)";
    menu.style.borderRadius = "12px";
    menu.style.zIndex = "9999";
    menu.style.padding = "8px";

    const pinRaw = (li.getAttribute("data-pinned-rank") || "").trim();
    const isPinned = pinRaw !== "";
    const isArchived = (li.getAttribute("data-archived") || "0") === "1";

    const makeBtn = (txt, cls) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn btn-sm w-100 text-start " + (cls || "btn-light");
      b.style.margin = "4px 0";
      b.textContent = txt;
      return b;
    };

    const btnPin = makeBtn(isPinned ? "إلغاء التثبيت" : "تثبيت المحادثة");
    btnPin.addEventListener("click", async () => {
      try {
        await postConversationSettings(li, { pinned_rank: isPinned ? null : 1 });
        li.setAttribute("data-pinned-rank", isPinned ? "" : "1");
        updateConversationFlags(li);
        reorderConversationList();
      } catch (e) {
        showToast("تعذر تحديث التثبيت", "error");
      } finally { hideConvMenu(); }
    });

    const btnArch = makeBtn(isArchived ? "إلغاء الأرشفة" : "أرشفة المحادثة");
    btnArch.addEventListener("click", async () => {
      try {
        await postConversationSettings(li, { is_archived: !isArchived });
        li.setAttribute("data-archived", (!isArchived) ? "1" : "0");
        updateConversationFlags(li);
        reorderConversationList();
      } catch (e) {
        showToast("تعذر تحديث الأرشفة", "error");
      } finally { hideConvMenu(); }
    });

    const btnMute8h = makeBtn("كتم 8 ساعات");
    btnMute8h.addEventListener("click", async () => {
      try {
        await postConversationSettings(li, { muted_until: 8 * 60 * 60 });
        const d = new Date(Date.now() + 8 * 60 * 60 * 1000);
        li.setAttribute("data-muted-until", d.toISOString());
        updateConversationFlags(li);
      } catch (e) {
        showToast("تعذر تحديث الكتم", "error");
      } finally { hideConvMenu(); }
    });

    const btnMute1w = makeBtn("كتم أسبوع");
    btnMute1w.addEventListener("click", async () => {
      try {
        await postConversationSettings(li, { muted_until: 7 * 24 * 60 * 60 });
        const d = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);
        li.setAttribute("data-muted-until", d.toISOString());
        updateConversationFlags(li);
      } catch (e) {
        showToast("تعذر تحديث الكتم", "error");
      } finally { hideConvMenu(); }
    });

    const btnUnmute = makeBtn("إلغاء الكتم");
    btnUnmute.addEventListener("click", async () => {
      try {
        await postConversationSettings(li, { muted_until: null });
        li.setAttribute("data-muted-until", "");
        updateConversationFlags(li);
      } catch (e) {
        showToast("تعذر إلغاء الكتم", "error");
      } finally { hideConvMenu(); }
    });

    menu.appendChild(btnPin);
    menu.appendChild(btnArch);

    const hr = document.createElement("div");
    hr.style.height = "1px";
    hr.style.background = "rgba(0,0,0,0.08)";
    hr.style.margin = "6px 0";
    menu.appendChild(hr);

    menu.appendChild(btnMute8h);
    menu.appendChild(btnMute1w);
    menu.appendChild(btnUnmute);

    document.body.appendChild(menu);

    setTimeout(() => {
      document.addEventListener("click", hideConvMenu, true);
      document.addEventListener("contextmenu", hideConvMenu, true);
    }, 0);
  }

  function initConversationContextMenu() {
    const ul = document.getElementById("users-list");
    if (!ul) return;

    ul.addEventListener("contextmenu", (ev) => {
      const li = ev.target?.closest?.('li.user-item[data-user-id], li.user-item[data-group-id]');
      if (!li) return;
      // don't open menu for special items
      if (li.id === "btnAddGroup" || li.id === "btnGroupInvites") return;
      ev.preventDefault();
      showConvMenuFor(li, ev.clientX, ev.clientY);
    });

    // initial flags
    Array.from(ul.querySelectorAll('li.user-item[data-user-id], li.user-item[data-group-id]')).forEach(updateConversationFlags);
    reorderConversationList();
  }

  // ===== Search modal =====
  function initSearchModalUI() {
    const btn = document.getElementById("btnSearch");
    const modalEl = document.getElementById("modalSearch");
    const input = document.getElementById("searchQuery");
    const resultsEl = document.getElementById("searchResults");
    if (!btn || !modalEl || !input || !resultsEl || typeof bootstrap === "undefined") return;

    const modal = new bootstrap.Modal(modalEl);

    btn.addEventListener("click", () => {
      if (!currentReceiverId && !currentGroupId) {
        showToast("اختر محادثة أولاً", "info");
        return;
      }
      resultsEl.innerHTML = "";
      input.value = "";
      modal.show();
      setTimeout(() => input.focus(), 150);
    });

    let timer = null;
    input.addEventListener("input", () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(async () => {
        const q = (input.value || "").trim();
        resultsEl.innerHTML = "";
        if (q.length < 2) return;

        const type = currentGroupId ? "group" : "dm";
        const id = currentGroupId ? currentGroupId : currentReceiverId;

        try {
          const res = await fetch(`/api/search_messages?type=${encodeURIComponent(type)}&id=${encodeURIComponent(id)}&q=${encodeURIComponent(q)}`);
          const j = await res.json();
          if (!res.ok || !j.ok) throw new Error(j.error || "search_failed");
          const items = j.results || [];
          if (!items.length) {
            resultsEl.innerHTML = '<div class="text-muted">لا توجد نتائج</div>';
            return;
          }
          items.forEach((it) => {
            const card = document.createElement("div");
            card.className = "p-2 border rounded";
            const ts = it.timestamp ? new Date(it.timestamp).toLocaleString("ar") : "";
            card.innerHTML = `<div style="font-size:12px;opacity:.75">${ts}</div><div>${escapeHtml(it.content || "")}</div>`;
            card.style.cursor = "pointer";
            card.addEventListener("click", () => {
              modal.hide();
              // Best effort: if message is currently in DOM, scroll to it
              const msgEl = document.querySelector(`[data-msg-id="${it.id}"]`);
              if (msgEl) {
                msgEl.scrollIntoView({ behavior: "smooth", block: "center" });
                msgEl.classList.add("highlight");
                setTimeout(() => msgEl.classList.remove("highlight"), 1500);
              } else {
                showToast("الرسالة قد تكون خارج الجزء المحمّل. مرّر للأعلى لتحميل المزيد ثم ابحث.", "info");
              }
            });
            resultsEl.appendChild(card);
          });
        } catch (e) {
          resultsEl.innerHTML = '<div class="text-danger">تعذر البحث</div>';
        }
      }, 350);
    });
  }


  document.addEventListener("DOMContentLoaded", () => {
    const bodyId = document.body.getAttribute("data-user-id");
    if (bodyId) currentUserId = parseInt(bodyId, 10);

    // Persist basic profile locally (helps auto-fill/login UX)
    try {
      const nm = document.body.getAttribute("data-user-name") || "";
      const ph = document.body.getAttribute("data-user-phone") || "";
      localStorage.setItem("chat_user_profile", JSON.stringify({ id: currentUserId, name: nm, phone: ph }));
    } catch (_) {}

    if ("PerformanceObserver" in window) {
      try {
        const observer = new PerformanceObserver((list) => {
          list.getEntries().forEach((entry) => {
            if (entry.name === "first-contentful-paint") {
              console.log("FCP:", entry.startTime.toFixed(0), "ms");
            }
          });
        });
        observer.observe({ type: "paint", buffered: true });
      } catch (_) {}
    }

    requestNotificationPermission();
    initNotificationSettingsUI();
    initSocket();
    initMessageActionsUI();
    initHeaderModalsUI();
    initConversationContextMenu();
    initSearchModalUI();
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
      refreshActivePresence(true);
      closeDrawer();
    } else {
      if (window.innerWidth <= 992) openDrawer();
    }
    badgePollTimeoutId = setTimeout(() => {
      badgeRefreshEnabled = true;
      refreshSidebarBadges();
      startBadgePolling();
    }, BADGE_POLL_DELAY_MS);
    renderNetStatus();
  });

})();
