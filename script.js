// ===== 1. 找到要操作的页面元素 =====
// getElementById("名字") 对应 HTML 中 id="名字" 的元素。
const avatar = document.getElementById("avatar");
const statusText = document.getElementById("status-text");
const conversation = document.getElementById("conversation");
const chatForm = document.getElementById("chat-form");
const messageInput = document.getElementById("message-input");


// 从 HTML 读取你设置的名字，避免在两个文件里重复修改。
const assistantName = document.getElementById("assistant-name").textContent.trim();

// 【可以试着改 1】1800 毫秒 = 1.8 秒，控制微笑保持多久。
// 回答会立即显示；这个时间只控制表情，不模拟模型处理耗时。
const REACTION_DURATION = 1800;
let resetTimer = null;

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

  // 每添加一条消息，滚动到底部，让最新的消息可见。
  conversation.scrollTop = conversation.scrollHeight;
}

// ===== 3. 根据输入生成“规则回复” =====
// 【可以试着改 2】修改下面的回复，或仿照已有 if 增加自己的规则。
// includes 表示“是否包含”；return 返回回答并结束这个函数。
// 多条规则同时匹配时，先写的规则先执行。
function getReply(text) {
  if (text.includes("你是谁")) {
    return `我是 ${assistantName}，你的数字人助手。现在我会按你写的规则回应。`;
  }

  if (text.includes("你好")) {
    return "你好，很高兴见到你。";
  }

  // 【可以试着改 3】上面的规则都没有匹配时，用这一句回应。
  // 反引号中的 ${text} 会被替换成用户实际输入的文字。
  return `我收到了：${text}`;
}

// ===== 4. 让数字人回应，然后恢复待机 =====
function showReaction() {
  avatar.classList.add("greeting");
  statusText.textContent = "回应中";

  // 如果上一条消息的计时还没结束，就取消它，重新计时。
  // 这样可以连续发送消息，而不会被旧计时器提前恢复表情。
  clearTimeout(resetTimer);
  resetTimer = setTimeout(() => {
    avatar.classList.remove("greeting");
    statusText.textContent = "待机中";
  }, REACTION_DURATION);
}

// ===== 5. 处理一次发送 =====
// 点击“发送”或在输入框按 Enter，都会触发 form 的 submit 事件。
chatForm.addEventListener("submit", (event) => {
  // form 默认会重新加载页面；这里阻止默认行为，保留当前对话。
  event.preventDefault();

  // value 读取输入框内容；trim 去掉首尾空格。
  const text = messageInput.value.trim();

  // 空字符串表示没有实际内容，直接结束，不添加消息。
  if (text === "") {
    messageInput.focus();
    return;
  }

  addMessage("user", text);
  const reply = getReply(text);
  addMessage("assistant", reply);
  showReaction();

  // 发送后清空输入框，并把光标放回去，方便输入下一句。
  messageInput.value = "";
  messageInput.focus();
});

// ===== 6. 页面打开时显示第一条问候 =====
// 【可以试着改 4】这里决定数字人的开场白。
addMessage("assistant", `你好，我是 ${assistantName}。你想聊些什么？`);

// 当前消息只存在于页面元素里，没有保存到文件或数据库。
// 所以刷新后会消失。这一步没有调用模型，也不会消耗 token。


