(function () {
  const SECRET_KEY = "chat_agent_admin_secret";

  function getSecret() {
    return (sessionStorage.getItem(SECRET_KEY) || "").trim();
  }

  function headers() {
    const s = getSecret();
    const h = { Accept: "application/json" };
    if (s) h["X-Chat-Agent-Secret"] = s;
    return h;
  }

  async function api(path) {
    const r = await fetch(path, { headers: headers() });
    const text = await r.text();
    let data;
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      throw new Error(text || r.statusText);
    }
    if (!r.ok) throw new Error(data.detail || r.statusText || String(r.status));
    return data;
  }

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function renderBotBody(html, plain, parseMode) {
    const el = document.createElement("div");
    el.className = "body";
    const mode = (parseMode || "").toLowerCase();
    if (mode === "html" && typeof DOMPurify !== "undefined") {
      el.innerHTML = DOMPurify.sanitize(html, {
        ALLOWED_TAGS: ["b", "strong", "i", "em", "code", "pre", "a", "br"],
        ALLOWED_ATTR: ["href"],
      });
    } else {
      el.textContent = plain;
    }
    return el;
  }

  const userList = document.getElementById("userList");
  const chatLog = document.getElementById("chatLog");
  const activeUserEl = document.getElementById("activeUser");
  const loadOlder = document.getElementById("loadOlder");
  let selectedUser = null;
  let oldestId = null;

  document.getElementById("saveSecret").onclick = () => {
    const v = document.getElementById("secret").value.trim();
    sessionStorage.setItem(SECRET_KEY, v);
    document.getElementById("authStatus").textContent = v ? "сохранено" : "очищено";
  };

  async function loadUsers() {
    userList.innerHTML = "";
    const data = await api("/admin/api/users?limit=300");
    for (const u of data.users || []) {
      const li = document.createElement("li");
      li.dataset.userId = u.user_id;
      li.innerHTML =
        "<span class=\"uid\">" +
        esc(u.user_id) +
        '</span><span class="meta">' +
        esc(u.last_at) +
        "</span>";
      li.onclick = () => selectUser(u.user_id, li);
      userList.appendChild(li);
    }
  }

  function selectUser(uid, li) {
    selectedUser = uid;
    oldestId = null;
    activeUserEl.textContent = uid ? "— " + uid : "";
    chatLog.innerHTML = "";
    loadOlder.classList.add("hidden");
    userList.querySelectorAll("li").forEach((x) => x.classList.remove("active"));
    if (li) li.classList.add("active");
    loadHistory(null);
  }

  async function loadHistory(beforeId) {
    if (!selectedUser) return;
    let path =
      "/admin/api/history?user_id=" +
      encodeURIComponent(selectedUser) +
      "&limit=50";
    if (beforeId != null) path += "&before_id=" + beforeId;
    const data = await api(path);
    const turns = data.turns || [];
    if (!turns.length && beforeId == null) {
      chatLog.innerHTML = "<p class=\"hint\">Нет записей.</p>";
      return;
    }
    const frag = document.createDocumentFragment();
    if (beforeId != null) {
      turns.forEach((t) => frag.appendChild(renderTurn(t)));
      chatLog.insertBefore(frag, chatLog.firstChild);
    } else {
      turns.forEach((t) => frag.appendChild(renderTurn(t)));
      chatLog.appendChild(frag);
      chatLog.scrollTop = chatLog.scrollHeight;
    }
    oldestId = turns.length ? turns[0].id : oldestId;
    loadOlder.classList.toggle("hidden", turns.length < 50);
  }

  function renderTurn(t) {
    const wrap = document.createDocumentFragment();
    const u = document.createElement("div");
    u.className = "msg user";
    u.innerHTML =
      '<div class="ts">' +
      esc(t.created_at) +
      "</div><div class=\"body\"></div>";
    u.querySelector(".body").textContent = t.user_message;
    wrap.appendChild(u);

    const b = document.createElement("div");
    b.className = "msg bot";
    b.innerHTML = '<div class="ts">ответ</div>';
    const body = renderBotBody(
      t.assistant_message,
      t.assistant_message,
      t.reply_parse_mode
    );
    b.appendChild(body);
    if (t.error) {
      const er = document.createElement("div");
      er.className = "err";
      er.textContent = "Ошибка: " + t.error;
      b.appendChild(er);
    }
    wrap.appendChild(b);
    return wrap;
  }

  document.getElementById("reloadUsers").onclick = () => {
    loadUsers().catch((e) => alert(e.message || String(e)));
  };

  loadOlder.onclick = () => {
    if (oldestId == null) return;
    loadHistory(oldestId).catch((e) => alert(e.message || String(e)));
  };

  const saved = getSecret();
  if (saved) document.getElementById("secret").value = saved;

  loadUsers().catch((e) => {
    userList.innerHTML =
      "<li class=\"hint\">" + esc(e.message || String(e)) + "</li>";
  });
})();
