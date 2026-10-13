// ===== 1. 找到要操作的页面元素 =====
// getElementById("名字") 对应 HTML 中 id="名字" 的元素。
const avatar = document.getElementById("avatar");
// 【修改：视频眨眼】找到 video，以及视频加载失败时显示的静态图。
const avatarVideo = document.getElementById("avatar-video");
const avatarFallback = document.getElementById("avatar-fallback");
const statusText = document.getElementById("status-text");
const conversation = document.getElementById("conversation");
const chatForm = document.getElementById("chat-form");
const messageInput = document.getElementById("message-input");
// 【新增：Python 通信】等待回复时控制按钮，并显示连接错误。
const sendButton = document.getElementById("send-button");
const connectionMessage = document.getElementById("connection-message");
// 【新增：模型开关】checked 为 true 才走 Python / Kimi；否则直接由 JS 回应。
const modelToggle = document.getElementById("model-toggle");
let isSending = false;

// ===== 新增：找到右侧记录抽屉和箭头按钮 =====
const historyPanel = document.getElementById("history-panel");
const historyToggle = document.getElementById("history-toggle");

// isOpen 为 true 时展开，为 false 时收起。
// 这是控制展开状态的唯一函数，之后修改开关行为时，从这里开始。
function setHistoryOpen(isOpen) {
  // hidden 是 HTML 元素自带的属性。! 表示取反：展开时不隐藏。
  historyPanel.hidden = !isOpen;

  // 【修改：页面边缘抽屉】不再给中央卡片添加类，也不改变卡片宽度。
  // 收起时箭头朝左，提示打开右边的面板；展开时朝右，提示收回。
  historyToggle.textContent = isOpen ? "›" : "‹";
  historyToggle.setAttribute("aria-expanded", String(isOpen));
  const label = isOpen ? "收起对话记录" : "展开对话记录";
  historyToggle.setAttribute("aria-label", label);
  historyToggle.title = label;

  // 隐藏期间也在添加消息；展开后再滚动，才能正确定位最新消息。
  if (isOpen) {
    conversation.scrollTop = conversation.scrollHeight;
  }
}

historyToggle.addEventListener("click", () => {
  // 当前 hidden 为 true（已隐藏），下次就应该展开；反之收起。
  setHistoryOpen(historyPanel.hidden);
});

// 可选的键盘操作：展开后按 Escape 收起，并把焦点放回箭头。
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !historyPanel.hidden) {
    setHistoryOpen(false);
    historyToggle.focus();
  }
});

// 从 HTML 读取你设置的名字，避免在两个文件里重复修改。
const assistantName = document.getElementById("assistant-name").textContent.trim();

modelToggle.addEventListener("change", () => {
  // 保留直接打开 HTML 时，开启模型模式的连接提醒。
  connectionMessage.textContent = modelToggle.checked && window.location.protocol === "file:"
    ? "模型模式需要 Python 后端，请双击启动数字人.cmd，再使用自动打开的网页。"
    : "";
  // 切换模式后清除上一种模式的状态提示。
  clearTimeout(resetTimer);
  avatar.classList.remove("greeting");
  statusText.textContent = "待机中";
});

// 【可以试着改 1】1800 毫秒 = 1.8 秒，控制人物轮廓回应效果保持多久。
// 收到后端回答后显示；这个时间只控制轮廓光，不模拟模型处理耗时。
const REACTION_DURATION = 1800;
let resetTimer = null;

// ===== 修改：播放透明眨眼视频，取代三张完整图片的轮播 =====
// 【视频可改 1】视频播完后，停留在睁眼画面，随机等待 0.5～1 秒再播放。
// 单位是毫秒；完整眨眼周期约等于“视频时长 + 这段等待时间”。
const VIDEO_WAIT_MIN = 500;
const VIDEO_WAIT_MAX = 1000;
let videoTimer = null;
let waitingForNextPlay = false;
let videoFailed = false;
let pageActive = true;
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
// 除了 HTML 的 muted 属性，再明确设置静音，便于浏览器允许自动播放。
avatarVideo.muted = true;

function canPlayVideo() {
  return pageActive && !document.hidden && !reducedMotion.matches && !videoFailed;
}

function pauseAvatarVideo() {
  clearTimeout(videoTimer);
  videoTimer = null;
  avatarVideo.pause();
}

function playAvatarVideo() {
  clearTimeout(videoTimer);
  videoTimer = null;
  // readyState >= 2 表示浏览器已有当前帧数据，可以开始播放。
  if (!canPlayVideo() || avatarVideo.readyState < 2) return;
  waitingForNextPlay = false;
  // play() 返回 Promise，因为浏览器可能限制自动播放或在切后台时取消播放。
  const playback = avatarVideo.play();
  if (playback) {
    playback.then(() => {
      // 异步播放刚成功时，页面可能已经切到后台；再检查一次。
      if (!canPlayVideo()) avatarVideo.pause();
    }).catch(() => {
      // 自动播放被浏览器拦截时，保留当前画面；点击页面后再尝试。
    });
  }
}

function scheduleNextVideo() {
  clearTimeout(videoTimer);
  if (!canPlayVideo()) return;
  const delay = VIDEO_WAIT_MIN + Math.random() * (VIDEO_WAIT_MAX - VIDEO_WAIT_MIN);
  videoTimer = setTimeout(playAvatarVideo, delay);
}

function updateVideoPlayback() {
  pauseAvatarVideo();
  if (reducedMotion.matches && avatarVideo.readyState >= 1) {
    // 减少动画时回到第一帧，避免一直停在闭眼画面。
    avatarVideo.currentTime = 0;
  }
  if (!canPlayVideo() || avatarVideo.readyState < 2) return;
  if (waitingForNextPlay) scheduleNextVideo();
  else playAvatarVideo();
}

avatarVideo.addEventListener("loadeddata", updateVideoPlayback);
avatarVideo.addEventListener("ended", () => {
  pauseAvatarVideo();
  waitingForNextPlay = true;
  // 素材末尾已经融合回第一帧，所以这次回到开头不会换成另一张人物照片。
  avatarVideo.currentTime = 0;
  scheduleNextVideo();
});
avatarVideo.addEventListener("error", () => {
  videoFailed = true;
  pauseAvatarVideo();
  avatarVideo.hidden = true;
  avatarFallback.hidden = false;
  console.warn("眨眼视频无法播放，请检查 HTML 中的 WebM 路径或浏览器支持情况。");
});
document.addEventListener("visibilitychange", updateVideoPlayback);
reducedMotion.addEventListener("change", updateVideoPlayback);
window.addEventListener("pagehide", () => {
  pageActive = false;
  pauseAvatarVideo();
});
window.addEventListener("pageshow", () => {
  pageActive = true;
  updateVideoPlayback();
});
// 等待期间的普通点击不提前触发眨眼，只重试可能被拦截的自动播放。
document.addEventListener("pointerdown", () => {
  if (!waitingForNextPlay && avatarVideo.paused) playAvatarVideo();
});
document.addEventListener("keydown", () => {
  if (!waitingForNextPlay && avatarVideo.paused) playAvatarVideo();
});
// defer 脚本运行前视频也可能已经加载，因此主动检查一次。
updateVideoPlayback();

// ===== 2. 把一条消息添加到页面 =====
// role 是消息来自谁（"user" 或 "assistant"），text 是消息内容。
function addMessage(role, text) {
  // createElement 创建一个新元素，此时它还没有出现在页面上。
  const messageRow = document.createElement("div");
  messageRow.classList.add("chat-message", role);

  const sender = document.createElement("span");
  sender.classList.add("sender");
  // 这是 if / else 的简短写法：用户消息显示“你”，其余显示数字人名字。
  sender.textContent = role === "user" ? "你" : assistantName;

  const bubble = document.createElement("p");
  bubble.classList.add("bubble");
  // textContent 把输入当成普通文字，输入 HTML 代码也不会被执行。
  bubble.textContent = text;

  // 先把名字和气泡放进消息，再把消息放进对话区域。
  messageRow.append(sender, bubble);
  conversation.appendChild(messageRow);

  // 【新增：抽屉】仅在记录可见时滚动；隐藏时继续添加并保留消息。
  if (!historyPanel.hidden) {
    conversation.scrollTop = conversation.scrollHeight;
  }
  // 返回这一行，发送失败时可以移除它，重试就不会出现重复记录。
  return messageRow;
}

// ===== 3. 两种回复方式：本地规则，或 Python / Kimi =====
// 【可以改】这里是关闭模型后的即时回复。只操作文字，不发网络请求。
function getLocalReply(text) {
  if (text.includes("你是谁")) {
    return `我是 ${assistantName}，你的数字人助手。现在使用本地规则回应。`;
  }
  if (text.includes("你好")) return "你好，很高兴见到你。";
  return `我收到了：${text}`;
}

// async / await 表示异步等待：等网络回复时，网页仍然可以播放视频和操作抽屉。
async function requestReply(text) {
  if (window.location.protocol === "file:") {
    throw new Error("请先启动 server.py，再打开 http://127.0.0.1:8000，不能直接双击 HTML 聊天。");
  }
  const controller = new AbortController();
  // Python 最多等待模型 90 秒；网页留出 100 秒，避免先于后端取消。
  const timeout = setTimeout(() => controller.abort(), 100000);
  try {
    // /api/chat 相对于当前网页地址：网页和接口由同一个 Python 服务提供。
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // stringify 把 JS 对象变为 JSON 文字；Python 会把它读回字典。
      body: JSON.stringify({ message: text, assistant_name: assistantName }),
      signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "后端未能处理消息。");
    if (typeof data.reply !== "string") throw new Error("后端返回的回答格式不正确。");
    return data.reply;
  } finally {
    // 不管成功还是失败，都取消这个超时计时器。
    clearTimeout(timeout);
  }
}

// ===== 4. 让数字人回应，然后恢复待机 =====
function showReaction() {
  // 【修改：视频数字人】greeting 只触发轮廓亮起，眨眼仍由视频自身播放。
  avatar.classList.add("greeting");
  statusText.textContent = "回应中";

  // 如果上一条消息的计时还没结束，就取消它，重新计时。
  // 这样可以连续发送消息，而不会被旧计时器提前恢复图片状态。
  clearTimeout(resetTimer);
  resetTimer = setTimeout(() => {
    avatar.classList.remove("greeting");
    statusText.textContent = "待机中";
  }, REACTION_DURATION);
}

// ===== 5. 处理一次发送 =====
// 点击“发送”或在输入框按 Enter，都会触发 form 的 submit 事件。
chatForm.addEventListener("submit", async (event) => {
  // form 默认会重新加载页面；这里阻止默认行为，保留当前对话。
  event.preventDefault();
  if (isSending) return;

  // value 读取输入框内容；trim 去掉首尾空格。
  const text = messageInput.value.trim();

  // 空字符串表示没有实际内容，直接结束，不添加消息。
  if (text === "") {
    messageInput.focus();
    return;
  }

  connectionMessage.textContent = "";
  // 【关键分支】关闭模型时立即回复，return 保证不会继续调用下面的接口。
  if (!modelToggle.checked) {
    addMessage("user", text);
    addMessage("assistant", getLocalReply(text));
    showReaction();
    messageInput.value = "";
    messageInput.focus();
    return;
  }

  isSending = true;
  sendButton.disabled = true;
  messageInput.disabled = true;
  // 当前模型请求完成前暂时锁住开关，避免把关闭开关误认为取消已发送的请求。
  modelToggle.disabled = true;
  sendButton.textContent = "等待中";
  chatForm.setAttribute("aria-busy", "true");
  connectionMessage.textContent = "";
  // 先取消旧的回应计时，避免它把“处理中”提前改成“待机中”。
  clearTimeout(resetTimer);
  avatar.classList.remove("greeting");
  statusText.textContent = "处理中";
  const userRow = addMessage("user", text);

  try {
    const reply = await requestReply(text);
    addMessage("assistant", reply);
    showReaction();
    messageInput.value = "";
  } catch (error) {
    // 失败时保留输入，移除未完成的消息；用户恢复连接后可以直接重试。
    userRow.remove();
    statusText.textContent = "连接失败";
    connectionMessage.textContent = error.name === "AbortError"
      ? "等待超过 100 秒，请检查 Python 后端。原请求可能仍在处理，请勿连续重试。"
      : error instanceof TypeError
        ? "无法连接后端，请检查 server.py 是否仍在运行，然后重试。"
        : error.message;
  } finally {
    // finally 不论成功失败都会执行，确保输入框能够继续使用。
    isSending = false;
    sendButton.disabled = false;
    messageInput.disabled = false;
    modelToggle.disabled = false;
    sendButton.textContent = "发送";
    chatForm.setAttribute("aria-busy", "false");
    messageInput.focus();
  }
});

// ===== 6. 页面打开时显示第一条问候 =====
// 【可以试着改 4】这里决定数字人的开场白。
addMessage("assistant", `你好，我是 ${assistantName}。你想聊些什么？`);

// 每次加载都从本地模式开始，不因浏览器恢复表单状态而意外调用模型。
modelToggle.checked = false;

// 当前消息只存在于页面元素里，没有保存到文件或数据库。
// 所以刷新后会消失。开场白和本地模式不调用模型；模型模式发送时产生 token 用量。
