// ===== 0. 单角色：替换 png/avatar.png 和配套 avatar.webm / avatar.mp4 即可 =====
// 不按人物姓名分配接口；动作素材由本地后端检测，缺少视频时使用图片。
const activeCharacter = { name: "数字人助手", image: "png/avatar.png", video: null };

// ===== 1. 找到要操作的页面元素 =====
// getElementById("名字") 对应 HTML 中 id="名字" 的元素。
const avatar = document.getElementById("avatar");
// 找到角色视频，以及没有视频或播放失败时显示的静态图。
const avatarVideo = document.getElementById("avatar-video");
const avatarFallback = document.getElementById("avatar-fallback");
const assetMessage = document.getElementById("asset-message");
const statusText = document.getElementById("status-text");
const replyText = document.getElementById("reply-text");
const conversation = document.getElementById("conversation");
const chatForm = document.getElementById("chat-form");
const messageInput = document.getElementById("message-input");
// 【新增：Python 通信】等待回复时控制按钮，并显示连接错误。
const sendButton = document.getElementById("send-button");
const stopButton = document.getElementById("stop-button");
const connectionMessage = document.getElementById("connection-message");
const speechToggle = document.getElementById("speech-toggle");
const speechPreview = document.getElementById("speech-preview");
const speechStop = document.getElementById("speech-stop");
const speechStatus = document.getElementById("speech-status");
const spokenCaption = document.getElementById("spoken-caption");
const spokenText = document.getElementById("spoken-text");
let currentSpeechText = "";
// 模型开关：checked 为 true 才走 Python / Dify；否则直接由 JS 回应。
const modelToggle = document.getElementById("model-toggle");
let isSending = false;
let activeRequest = null;
const testToggle = document.getElementById("test-toggle");
const testCaseButtons = document.getElementById("test-case-buttons");
const cacheCheck = document.getElementById("cache-check");
const cachePrepare = document.getElementById("cache-prepare");
const cacheCancel = document.getElementById("cache-cancel");
const cacheStatus = document.getElementById("cache-status");
const replySource = document.getElementById("reply-source");
let testCases = [], testRevision = "", cachePlan = null;
let cacheBusy = false, preparation = null;
testToggle.checked = false;
// ===== 会话存储：变量像工作台，localStorage 像浏览器里的小抽屉 =====
// 单角色只保留一个会话入口；不自动接入旧 Grace / Liz 的会话。
const CONVERSATION_KEY = "dth.conversation.avatar";
let conversationId = "";
try {
  // getItem 读取抽屉里的编号；没存过时得到 null，|| "" 将它变成空字符串。
  // 每次刷新都会重新执行这里，因此能从浏览器恢复编号。
  conversationId = localStorage.getItem(CONVERSATION_KEY) || "";
} catch {
  // 浏览器禁止存储时仍可聊天，只是刷新后不能恢复编号。
  console.warn("无法读取浏览器会话存储，本次使用页面内存继续聊天。");
}

function rememberConversation(id) {
  conversationId = id; // 先更新工作台：当前页面马上使用新编号。
  try {
    if (id) {
      // setItem 写入小抽屉：页面关闭或刷新后，编号仍保留在这个浏览器中。
      localStorage.setItem(CONVERSATION_KEY, id);
    } else {
      // 只移除当前会话的编号，不清空其他网站数据，也不删除 Dify 云端对话。
      localStorage.removeItem(CONVERSATION_KEY);
    }
  } catch {
    console.warn("无法保存浏览器会话编号；当前聊天正常，但刷新后可能无法恢复。");
  }
}

// ===== 找到右侧记录抽屉和箭头按钮 =====
const historyPanel = document.getElementById("history-panel");
const historyToggle = document.getElementById("history-toggle");
const newConversationButton = document.getElementById("new-conversation");

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

// 页面显示的名字和消息署名来自角色配置。
// 模型的身份和语气由 Dify 画布中的 SYSTEM 提示词决定。
const assistantName = activeCharacter.name;
document.getElementById("assistant-name").textContent = assistantName;

modelToggle.addEventListener("change", () => {
  speech.stop();
  // 保留直接打开 HTML 时，开启模型模式的连接提醒。
  connectionMessage.textContent = modelToggle.checked && window.location.protocol === "file:"
    ? "模型模式需要 Python 后端，请运行 server.py 后通过本机地址打开网页。"
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

// ===== 播放角色视频：播放结束后随机等待，再从第一帧开始 =====
// 【视频可改 1】视频播完后，停留在睁眼画面，随机等待 0.5～1 秒再播放。
// 单位是毫秒；完整眨眼周期约等于“视频时长 + 这段等待时间”。
const VIDEO_WAIT_MIN = 500;
const VIDEO_WAIT_MAX = 1000;
let videoTimer = null;
let waitingForNextPlay = false;
let pageActive = true;
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
// 除了 HTML 的 muted 属性，再明确设置静音，便于浏览器允许自动播放。
avatarVideo.muted = true;

function canPlayVideo() {
  // 静态角色没有视频，页面点击、切回前台时也不会启动视频播放。
  return Boolean(activeCharacter.video) && !avatarVideo.hidden
    && pageActive && !document.hidden && !reducedMotion.matches;
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

avatarVideo.addEventListener("loadeddata", () => {
  if (!avatarVideo.error && activeCharacter.video) {
    avatarVideo.hidden = false;
    avatarFallback.hidden = true;
    assetMessage.textContent = "";
    updateVideoPlayback();
  }
});
avatarVideo.addEventListener("ended", () => {
  pauseAvatarVideo();
  waitingForNextPlay = true;
  // 素材末尾已经融合回第一帧，所以这次回到开头不会换成另一张人物照片。
  avatarVideo.currentTime = 0;
  scheduleNextVideo();
});
avatarVideo.addEventListener("error", () => {
  pauseAvatarVideo();
  // 隐藏视频也会让 canPlayVideo() 返回 false，不需要另一份失败状态。
  avatarVideo.hidden = true;
  avatarFallback.hidden = false;
  assetMessage.textContent = "动作视频无法播放，已显示静态图片。请检查视频格式。";
});
avatarFallback.addEventListener("error", () => {
  // 视频能正常播放时，不让备用图片错误遮住人物。
  if (avatarVideo.hidden) assetMessage.textContent = "人物图片无法读取，请检查 png/avatar.png。聊天仍可使用。";
});
document.addEventListener("visibilitychange", updateVideoPlayback);
reducedMotion.addEventListener("change", updateVideoPlayback);
window.addEventListener("pagehide", () => {
  speech.stop();
  pageActive = false;
  pauseAvatarVideo();
});
window.addEventListener("pageshow", () => {
  pageActive = true;
  updateVideoPlayback();
});
// 等待期间的普通点击不提前触发眨眼，只重试可能被拦截的自动播放。
function retryAvatarPlayback() {
  if (!waitingForNextPlay && avatarVideo.paused) playAvatarVideo();
}
document.addEventListener("pointerdown", retryAvatarPlayback);
document.addEventListener("keydown", retryAvatarPlayback);
// 先显示图片；检测素材只在打开/刷新页面时执行一次，不轮询、不调用模型。
avatarFallback.src = activeCharacter.image;
avatarFallback.alt = `${assistantName} 的角色图片`;
avatarVideo.hidden = true;
avatarFallback.hidden = false;
async function loadAvatarAssets() {
  if (window.location.protocol === "file:") {
    assetMessage.textContent = "请启动 server.py 并通过本机地址打开网页，以加载动作视频。";
    return;
  }
  try {
    const response = await fetch("/api/avatar", { cache: "no-store", signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error("无法检测素材");
    const assets = await response.json();
    // 只接受固定素材名，不让配置改变页面将要加载的来源。
    if (assets.image !== null && assets.image !== "png/avatar.png") throw new Error("图片路径无效");
    if (![null, "png/avatar.webm", "png/avatar.mp4"].includes(assets.video)) throw new Error("视频路径无效");
    activeCharacter.video = assets.video;
    if (!assets.image) assetMessage.textContent = "未找到 png/avatar.png，请放入人物图片。";
    else if (!assets.video) assetMessage.textContent = "当前使用静态图片；加入配套动作视频后刷新即可播放。";
    if (assets.video) {
      avatarVideo.poster = assets.image ? activeCharacter.image : "";
      avatarVideo.setAttribute("aria-label", `${assistantName} 的动作动画`);
      avatarVideo.preload = "auto";
      avatarVideo.src = assets.video;
      avatarVideo.load();
    }
  } catch {
    assetMessage.textContent = "无法检测动作素材，已尝试显示图片。请确认后端已更新；聊天仍可使用。";
  }
}
void loadAvatarAssets();

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
  // 返回这一行，流式回答持续更新同一个气泡。
  return messageRow;
}

function updateReply(row, text) {
  row.querySelector(".bubble").textContent = text;
  replyText.textContent = text;
  replyText.scrollTop = replyText.scrollHeight;
  if (!historyPanel.hidden) conversation.scrollTop = conversation.scrollHeight;
}

function markIncomplete(row, label, state = "incomplete") {
  row.classList.add(state);
  const note = document.createElement("span");
  note.classList.add("message-state");
  note.textContent = label;
  row.append(note);
}

async function stopCurrentReply(request) {
  if (activeRequest !== request || request.completed || request.stopping) return;
  request.stopRequested = true;
  stopButton.disabled = true;
  stopButton.textContent = "正在停止…";
  statusText.textContent = "正在停止";
  // 首个 Dify 事件到来前没有 task_id，先记住点击意图，收到编号立即停止。
  if (!request.taskId) return;
  request.stopping = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  request.stopPromise = (async () => {
    try {
      const response = await fetch("/api/chat/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: request.taskId }),
        signal: controller.signal,
      });
      const data = await response.json();
      if (!response.ok || data.status !== "stopped") throw new Error(data.error || "未能确认云端停止。");
      if (activeRequest !== request || request.completed) return;
      // 只有云端确认停止后才取消本地读取，防止界面把停止失败显示成成功。
      request.stopped = true;
      request.controller.abort();
    } catch (error) {
      if (activeRequest !== request || request.completed) return;
      request.stopRequested = false;
      request.stopping = false;
      stopButton.disabled = false;
      stopButton.textContent = "重试停止";
      statusText.textContent = "停止未确认";
      connectionMessage.textContent = error.name === "AbortError"
        ? "停止请求超时，云端可能仍在生成，可以重试停止。"
        : error.message;
    } finally {
      clearTimeout(timeout);
    }
  })();
  await request.stopPromise;
}

stopButton.addEventListener("click", () => {
  speech.stop(); // 本地声音立即停止，不等待 Dify 的停止确认。
  if (activeRequest) void stopCurrentReply(activeRequest);
});

// 从同一个起点计时，帮助区分：模型慢、等分句、语音慢，还是播放慢。
const latencyValues = document.getElementById("latency-values");
let latencyStart = 0;
let latencyMarks = {};
let latencyGaps = 0;
let latencyCache = { hit: 0, miss: 0, unsaved: 0 };
function resetLatency() {
  latencyStart = Date.now();
  latencyMarks = {};
  latencyGaps = 0;
  latencyCache = { hit: 0, miss: 0, unsaved: 0 };
  latencyValues.textContent = "等待响应…";
}
function markLatency(stage) {
  if (!latencyStart) return;
  if (stage === "underrun") latencyGaps++;
  else if (stage.startsWith("cache_")) latencyCache[stage.slice(6)]++;
  else {
    if (stage in latencyMarks) return;
    latencyMarks[stage] = Date.now() - latencyStart;
  }
  const seconds = value => (value / 1000).toFixed(2) + " 秒";
  const labels = { first_text: "首字", first_clause: "首段可朗读", first_audio: "首包音频", first_play: "开始播放", text_done: "文字完成" };
  const parts = Object.entries(labels).filter(([key]) => key in latencyMarks)
    .map(([key, label]) => label + "：" + seconds(latencyMarks[key]));
  if ("tts_request" in latencyMarks && "first_audio" in latencyMarks) {
    parts.push("其中语音首包等待：" + seconds(latencyMarks.first_audio - latencyMarks.tts_request));
  }
  if ("first_audio" in latencyMarks) parts.push("缓冲不足：" + latencyGaps + " 次");
  if (latencyCache.hit || latencyCache.miss) parts.push(`音频缓存 ${latencyCache.hit} 段 · 请求云端合成 ${latencyCache.miss} 段`);
  if (latencyCache.unsaved) parts.push("部分音频未能保存到缓存");
  latencyValues.textContent = parts.join(" · ") || "等待语音首包…";
}

// 语音层独立于模型请求：朗读失败不会阻塞文字输出。
const speech = new window.ReplySpeech({
  onMetric: markLatency,
  timers: { setTimeout: (fn, delay) => setTimeout(fn, delay), clearTimeout: id => clearTimeout(id) },
  onState: ({ state, text, active }) => {
    speechStop.hidden = !active;
    speechPreview.disabled = isSending || active || !speech.ready;
    if (state === "speaking") {
      spokenCaption.hidden = false;
      spokenText.textContent = text;
      speechStatus.textContent = "正在朗读";
    } else if (state === "waiting") {
      speechStatus.textContent = "正在等待文字或合成语音…";
    } else if (state === "idle") {
      speechStatus.textContent = "朗读完成";
    } else if (state === "stopped") {
      speechStatus.textContent = speechToggle.checked ? "本轮朗读已停止" : "语音已关闭";
    } else if (state === "replaced") {
      speechStatus.textContent = "回答已更新，旧内容朗读已停止。";
    } else if (state === "error") {
      speechStatus.textContent = text === "not-allowed"
        ? "浏览器未允许朗读，请先点击“试听”，文字聊天仍可使用。"
        : `${text} 文字聊天仍可使用。`;
    }
    syncDevelopmentControls();
  },
});
speechToggle.checked = false; // 刷新和恢复历史时均不自动出声。
speechToggle.disabled = speechPreview.disabled = true;
// 删除旧的浏览器音色偏好；只清理本项目这一项设置。
try { localStorage.removeItem("dth.speech.voice"); } catch {}
async function loadSpeechSettings() {
  try {
    if (!speech.supported) throw new Error("当前浏览器不支持音频播放。");
    if (window.location.protocol === "file:") throw new Error("请通过启动快捷方式打开网页，以连接语音后端。");
    const response = await fetch("/api/tts", { cache: "no-store", signal: AbortSignal.timeout(5000) });
    const settings = await response.json();
    if (!response.ok || typeof settings.configured !== "boolean") throw new Error("无法检测语音配置，请重启最新后端。");
    // 有本机缓存时，没有密钥也能播放；生成新音频才要求密钥。
    speech.ready = settings.configured || settings.cache_available === true;
    speechToggle.disabled = !speech.ready;
    speechPreview.disabled = isSending || !speech.ready;
    speechStatus.textContent = settings.configured
      ? "固定音色：魅力女友 2.0。音频优先读缓存；普通模式未命中会合成并产生用量。"
      : settings.cache_available ? "可播放本机缓存；生成新音频需要语音 API Key。"
      : "请在 local_config.py 填写 DOUBAO_TTS_API_KEY，并重启后端。";
  } catch (error) {
    speechStatus.textContent = `${error.message} 文字聊天仍可使用。`;
  }
  syncDevelopmentControls();
}
void loadSpeechSettings();
speechToggle.addEventListener("change", () => {
  if (speechToggle.checked) speech.warmup();
  if (!speechToggle.checked) speech.stop();
  else if (isSending) speech.begin(currentSpeechText, { cacheOnly: testToggle.checked });
  else speechStatus.textContent = "语音已开启，下一条回答会自动朗读。";
});
speechPreview.addEventListener("click", () => {
  if (isSending || cacheBusy || !speech.ready || speech.active) return;
  // 试听只读这一句，不顺便开启自动播报，也不调用 Dify。
  resetLatency();
  speech.begin("", { cacheOnly: testToggle.checked });
  speech.update(testToggle.checked ? testCases[0].reply : "你回来啦，今天过得怎么样，有什么想跟我聊聊的吗？");
  speech.finish();
});
speechStop.addEventListener("click", () => speech.stop());

// ===== 开发测试：只读预设回答；准备按钮是独立、明确的付费合成入口 =====
function syncDevelopmentControls() {
  const busy = isSending || cacheBusy;
  testToggle.disabled = busy || !testCases.length;
  modelToggle.disabled = busy || testToggle.checked;
  newConversationButton.disabled = busy || testToggle.checked;
  cacheCheck.disabled = busy || speech.active;
  cachePrepare.hidden = !cachePlan?.missing.length;
  cachePrepare.disabled = busy || speech.active || !cachePlan?.can_generate;
  cacheCancel.hidden = !preparation;
  speechPreview.disabled = busy || speech.active || !speech.ready;
  speechToggle.disabled = cacheBusy || !speech.ready;
  if (cacheBusy) { sendButton.disabled = true; messageInput.disabled = true; }
}

async function cacheJSON(url, data, signal = AbortSignal.timeout(5000)) {
  const response = await fetch(url, data === undefined ? { cache: "no-store", signal } : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data), signal,
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "测试缓存接口不可用，请重启最新后端。");
  return result;
}

async function loadTestCases() {
  const data = await cacheJSON("/api/test/cases");
  if (!Array.isArray(data.cases) || !data.cases.length || typeof data.revision !== "string") {
    throw new Error("无法读取测试台词，请重启最新后端。");
  }
  testCases = data.cases;
  testRevision = data.revision;
  testCaseButtons.replaceChildren();
  for (const item of testCases) {
    const button = document.createElement("button");
    button.type = "button";
    button.classList.add("speech-button");
    button.textContent = item.inputs[0];
    button.addEventListener("click", () => {
      if (isSending || cacheBusy) return;
      messageInput.value = item.inputs[0];
      messageInput.focus(); // 只填入，不自动发送、不开模型。
    });
    testCaseButtons.appendChild(button);
  }
}
void loadTestCases().then(() => {
  cacheStatus.textContent = `已加载 ${testCases.length} 条测试问答。可先检查音频，准备后重复测试不调用云端。`;
}).catch(error => { cacheStatus.textContent = error.message; }).finally(syncDevelopmentControls);

testToggle.addEventListener("change", () => {
  speech.stop();
  currentSpeechText = "";
  spokenCaption.hidden = true;
  replySource.textContent = testToggle.checked
    ? "开发测试已开启：仅匹配预设问答和本机音频，未命中不调用云端。"
    : "已恢复普通模式。测试记录未发送给 Dify，原模型会话保持不变。";
  syncDevelopmentControls();
});

cacheCheck.addEventListener("click", async () => {
  if (isSending || cacheBusy || speech.active) return;
  cacheBusy = true;
  cachePlan = null;
  syncDevelopmentControls();
  cacheStatus.textContent = "正在检查台词和音频缓存…";
  try {
    await loadTestCases();
    const manifest = { revision: testRevision, cases: testCases.map(item => ({ id: item.id, segments: window.ReplySpeech.split(item.reply) })) };
    const plan = await cacheJSON("/api/test/plan", manifest);
    cachePlan = { ...plan, manifest, missing: plan.items.map((item, index) => ({ ...item, index })).filter(item => !item.cached) };
    const chars = cachePlan.missing.reduce((sum, item) => sum + Array.from(item.text).length, 0);
    cacheStatus.textContent = cachePlan.missing.length
      ? `缺少 ${cachePlan.missing.length} 段音频，共 ${chars} 字。点击“生成缺失音频”会调用${plan.voice}并产生语音用量。${plan.can_generate ? "" : "请先填写语音密钥并重启。"}`
      : `全部 ${plan.items.length} 段音频已缓存，可断开外网进行开发测试。`;
  } catch (error) { cacheStatus.textContent = error.message; }
  finally { cacheBusy = false; sendButton.disabled = messageInput.disabled = false; syncDevelopmentControls(); }
});

cachePrepare.addEventListener("click", async () => {
  if (isSending || cacheBusy || speech.active || !cachePlan?.can_generate || !cachePlan.missing.length) return;
  const plan = cachePlan;
  const controller = new AbortController();
  preparation = controller;
  cacheBusy = true;
  syncDevelopmentControls();
  let completed = 0;
  try {
    // 逐段保存，已成功的段不会因下一段失败丢失；不自动重试，不重复生成命中的内容。
    for (const item of plan.missing) {
      if (controller.signal.aborted) throw new Error("准备已停止。");
      cacheStatus.textContent = `正在准备第 ${completed + 1}/${plan.missing.length} 段，已保存 ${completed} 段…`;
      const response = await fetch("/api/test/prepare", {
        method: "POST", headers: { "Content-Type": "application/json", "Accept": "application/x-ndjson" },
        body: JSON.stringify({ ...plan.manifest, plan_id: plan.plan_id, index: item.index }),
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(40000)]),
      });
      const result = await window.ReplySpeech.consume(response, () => {}, () => !controller.signal.aborted);
      if (!result?.cache_saved) throw new Error("音频没有保存成功，请重新检查缓存。");
      completed++;
    }
    cachePlan = null;
    cacheStatus.textContent = `准备完成，已保存 ${completed} 段音频。开启开发测试和语音播报后输入测试句即可。`;
    await loadSpeechSettings();
  } catch (error) {
    cachePlan = null; // 重新检查后继续，只补缺少的段。
    cacheStatus.textContent = `已保存 ${completed} 段。${controller.signal.aborted ? "已停止本地准备，当前云端合成可能仍会计费。" : error.message} 请重新检查后继续。`;
  } finally {
    preparation = null;
    cacheBusy = false;
    sendButton.disabled = messageInput.disabled = false;
    syncDevelopmentControls();
  }
});
cacheCancel.addEventListener("click", () => preparation?.abort());
window.addEventListener("pagehide", () => preparation?.abort());

async function showTestReply(text) {
  isSending = true;
  sendButton.disabled = messageInput.disabled = true;
  syncDevelopmentControls();
  const row = addMessage("user", `【测试】${text}`);
  row.classList.add("test-message");
  try {
    const data = await cacheJSON("/api/test/reply", { message: text });
    if (data.source !== "test_cache" || typeof data.reply !== "string") throw new Error("测试回答格式不正确。");
    markLatency("first_text");
    markLatency("text_done");
    const answer = addMessage("assistant", data.reply);
    answer.classList.add("test-message");
    updateReply(answer, data.reply);
    replySource.textContent = "测试回答命中 · 本轮未调用模型 · 语音仅使用本机缓存";
    currentSpeechText = data.reply;
    speech.update(data.reply);
    speech.finish();
    showReaction();
    messageInput.value = "";
  } catch (error) {
    speech.stop();
    replySource.textContent = "测试未完成 · 没有自动调用云端";
    replyText.textContent = error.message;
    connectionMessage.textContent = error.message;
  } finally {
    isSending = false;
    sendButton.disabled = messageInput.disabled = false;
    syncDevelopmentControls();
    messageInput.focus();
  }
}

// ===== 3. 两种回复方式：本地规则，或 Python / Dify =====
// 【可以改】这里是关闭模型后的即时回复。只操作文字，不发网络请求。
function getLocalReply(text) {
  if (text.includes("你是谁")) {
    return `我是 ${assistantName}，你的数字人助手。现在使用本地规则回应。`;
  }
  if (text.includes("你好")) return "你好，很高兴见到你。";
  return `我收到了：${text}`;
}

// async / await 表示异步等待：等网络回复时，网页仍然可以播放视频和操作抽屉。
async function requestReply(text, onText, request) {
  if (window.location.protocol === "file:") {
    throw new Error("请先启动 server.py，再打开 http://127.0.0.1:8000，不能直接双击 HTML 聊天。");
  }
  const controller = request.controller;
  // 页面整轮最多等待 100 秒；超时不会自动重发，也不等于云端停止生成。
  const timeout = setTimeout(() => controller.abort(), 100000);
  let reader;
  try {
    // /api/chat 相对于当前网页地址：网页和接口由同一个 Python 服务提供。
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
      // stringify 把 JS 对象变为 JSON 文字；Python 会把它读回字典。
      // 每轮带上同一个会话编号，Dify 才知道我们在接着哪一段对话聊。
      body: JSON.stringify({ message: text, conversation_id: conversationId }),
      signal: controller.signal,
    });
    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.error || "后端未能处理消息。");
    }
    if (!response.headers.get("Content-Type")?.includes("text/event-stream") || !response.body) {
      throw new Error("后端未启用流式回复，请重启 Python 后端后刷新网页。");
    }
    reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8", { fatal: true });
    let buffer = "";
    let answer = "";
    let receivedId = conversationId;
    const saveId = (id) => {
      if (typeof id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)
          || (receivedId && receivedId !== id)) {
        throw new Error("后端返回的会话编号格式不正确。");
      }
      receivedId = id;
      // 尽早保存新会话，避免生成中断后丢失云端已经创建的会话引用。
      rememberConversation(id);
    };
    while (true) {
      const { value, done } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let separator;
      while ((separator = /\r?\n\r?\n/.exec(buffer))) {
        const frame = buffer.slice(0, separator.index);
        buffer = buffer.slice(separator.index + separator[0].length);
        const data = frame.split(/\r?\n/).filter(line => line.startsWith("data:"))
          .map(line => line.slice(5).replace(/^ /, "")).join("\n");
        if (!data) continue;
        const event = JSON.parse(data);
        if (event.event === "error") throw new Error(event.error || "回答生成中断。");
        if (event.event === "conversation") saveId(event.conversation_id);
        if (event.event === "task") {
          if (typeof event.task_id !== "string" || !/^[0-9a-f-]{36}$/i.test(event.task_id)
              || (request.taskId && request.taskId !== event.task_id)) {
            throw new Error("后端返回的任务编号格式不正确。");
          }
          request.taskId = event.task_id;
          if (request.stopRequested) void stopCurrentReply(request);
        }
        if (event.event === "delta" || event.event === "replace") {
          if (typeof event.text !== "string") throw new Error("回答文字格式不正确。");
          answer = event.event === "replace" ? event.text : answer + event.text;
          onText(answer);
        }
        if (event.event === "done") {
          saveId(event.conversation_id);
          if (!answer.trim()) throw new Error("模型没有返回回答正文。");
          request.completed = true;
          return answer;
        }
      }
      if (buffer.length > 1048576) throw new Error("回复数据格式异常。");
      if (done) throw new Error("连接已中断，回答可能未完成。没有自动重试。");
    }
  } finally {
    // 不管成功还是失败，都取消这个超时计时器。
    clearTimeout(timeout);
    if (reader) {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
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
  if (isSending || cacheBusy) return;

  // value 读取输入框内容；trim 去掉首尾空格。
  const text = messageInput.value.trim();

  // 空字符串表示没有实际内容，直接结束，不添加消息。
  if (text === "") {
    messageInput.focus();
    return;
  }

  connectionMessage.textContent = "";
  resetLatency();
  currentSpeechText = "";
  spokenCaption.hidden = true;
  if (speechToggle.checked) speech.begin("", { cacheOnly: testToggle.checked });
  else speech.stop(false);
  if (testToggle.checked) {
    await showTestReply(text);
    return; // 绝不落入 Dify 或本地规则分支。
  }
  // 【关键分支】关闭模型时立即回复，return 保证不会继续调用下面的接口。
  if (!modelToggle.checked) {
    replySource.textContent = "本地规则 · 未调用模型";
    addMessage("user", text);
    const reply = getLocalReply(text);
    markLatency("first_text");
    markLatency("text_done");
    updateReply(addMessage("assistant", reply), reply);
    speech.update(reply);
    speech.finish();
    showReaction();
    messageInput.value = "";
    messageInput.focus();
    return;
  }

  isSending = true;
  replySource.textContent = "Dify 模型回答";
  syncDevelopmentControls();
  const request = { controller: new AbortController(), taskId: "", stopRequested: false, stopping: false, stopped: false, completed: false, stopPromise: null };
  activeRequest = request;
  sendButton.disabled = true;
  sendButton.hidden = true;
  stopButton.hidden = false;
  stopButton.disabled = false;
  stopButton.textContent = "停止回答";
  messageInput.disabled = true;
  // 当前模型请求完成前暂时锁住开关，避免把关闭开关误认为取消已发送的请求。
  modelToggle.disabled = true;
  newConversationButton.disabled = true;
  speechPreview.disabled = true;
  sendButton.textContent = "等待中";
  chatForm.setAttribute("aria-busy", "true");
  connectionMessage.textContent = "";
  // 先取消旧的回应计时，避免它把“处理中”提前改成“待机中”。
  clearTimeout(resetTimer);
  avatar.classList.remove("greeting");
  statusText.textContent = "处理中";
  replyText.textContent = "正在思考…";
  const userRow = addMessage("user", text);
  const assistantRow = addMessage("assistant", "正在思考…");
  let partialReply = "";

  try {
    const reply = await requestReply(text, (reply) => {
      if (request.stopRequested) return;
      partialReply = reply;
      if (reply.trim()) markLatency("first_text");
      currentSpeechText = reply;
      updateReply(assistantRow, reply);
      speech.update(reply);
      statusText.textContent = "正在回答";
      sendButton.textContent = "回答中";
    }, request);
    updateReply(assistantRow, reply);
    markLatency("text_done");
    speech.update(reply);
    speech.finish();
    showReaction();
    messageInput.value = "";
  } catch (error) {
    speech.stop();
    // Dify 的停止可能先让原流结束，再返回停止响应；等确认后再判断结果。
    if (request.stopPromise) await request.stopPromise;
    if (request.stopped) {
      updateReply(assistantRow, partialReply || "已停止，本次没有生成回答。");
      markIncomplete(assistantRow, "已停止", "stopped");
      statusText.textContent = "已停止";
      connectionMessage.textContent = "";
      messageInput.value = "";
      return;
    }
    // 云端可能已保存本轮：保留用户消息与部分回答，明确标记未完成。
    if (partialReply) {
      markIncomplete(assistantRow, "回答未完成");
    } else {
      updateReply(assistantRow, "本次未收到完整回答。");
      markIncomplete(assistantRow, "发送或生成失败");
    }
    markIncomplete(userRow, "本轮未完成，请勿重复发送");
    statusText.textContent = "连接失败";
    connectionMessage.textContent = error.name === "AbortError"
      ? "等待超过 100 秒，请检查 Python 后端。原请求可能仍在处理，请勿连续重试。"
      : error instanceof TypeError
        ? "无法连接后端，请检查 server.py 是否仍在运行，然后重试。"
        : error.message;
  } finally {
    // finally 不论成功失败都会执行，确保输入框能够继续使用。
    isSending = false;
    activeRequest = null;
    sendButton.disabled = false;
    sendButton.hidden = false;
    stopButton.hidden = true;
    messageInput.disabled = false;
    modelToggle.disabled = false;
    newConversationButton.disabled = false;
    speechPreview.disabled = speech.active || !speech.ready;
    sendButton.textContent = "发送";
    chatForm.setAttribute("aria-busy", "false");
    messageInput.focus();
    syncDevelopmentControls();
  }
});

// ===== 6. 页面打开时显示第一条问候 =====
// 【可以试着改 4】这里决定数字人的开场白；恢复会话时显示不同的提示。
function addWelcomeMessage() {
  const welcome = conversationId
    ? `我是 ${assistantName}，可以接着上次聊。上次的消息暂时不会自动显示在这里。`
    : `你好，我是 ${assistantName}。你想聊些什么？`;
  updateReply(addMessage("assistant", welcome), welcome);
}
addWelcomeMessage();

newConversationButton.addEventListener("click", () => {
  if (isSending || cacheBusy || testToggle.checked) return;
  speech.stop();
  currentSpeechText = "";
  spokenCaption.hidden = true;
  rememberConversation(""); // 工作台和浏览器里的编号一起清空。
  conversation.replaceChildren(); // 只清空网页中的消息显示。
  clearTimeout(resetTimer);
  avatar.classList.remove("greeting");
  statusText.textContent = "待机中";
  connectionMessage.textContent = "";
  addWelcomeMessage();
  messageInput.focus();
});

// 每次加载都从本地模式开始，不因浏览器恢复表单状态而意外调用模型。
modelToggle.checked = false;

// 当前步骤只恢复会话编号，不恢复历史消息；读取 Dify 历史消息是下一步。
// 浏览器只保存编号，不保存 API 密钥；不同浏览器和电脑不会自动共享此编号。
// “开始新对话”只清除本地引用，历史模型对话仍保留在 Dify 云端。
// 本地模式的消息不会发送给 Dify；开场白和本地回应不调用模型。
