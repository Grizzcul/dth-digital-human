// 使用真实 Web Streams/UTF-8 解码器，模拟 DOM 和网络；不调用云端模型。
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const source = fs.readFileSync(require("node:path").join(__dirname, "../script.js"), "utf8");
const CID = "00000000-0000-4000-8000-000000000001";
const TID = "00000000-0000-4000-8000-000000000010";
const tick = () => new Promise(resolve => setImmediate(resolve));
const encode = event => new TextEncoder().encode(`data: ${JSON.stringify(event)}\r\n\r\n`);

function harness() {
  const elements = new Map();
  const storage = new Map();
  const calls = [];
  let stream;
  let streamController;
  let stopReply = async () => new Response(JSON.stringify({ status: "stopped" }));
  function element() {
    const classes = new Set();
    return {
      value: "", textContent: "", hidden: true, checked: false, disabled: false,
      readyState: 0, paused: true, scrollHeight: 0, scrollTop: 0, children: [], events: {},
      classList: { add: (...names) => names.forEach(n => classes.add(n)), remove: n => classes.delete(n), contains: n => classes.has(n) },
      addEventListener(name, callback) { this.events[name] = callback; },
      setAttribute() {}, focus() {}, pause() {}, load() {},
      append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } },
      appendChild(child) { this.append(child); },
      replaceChildren() { this.children = []; },
      querySelector(selector) { return this.children.find(c => c.classList.contains(selector.slice(1))); },
    };
  }
  const context = vm.createContext({
    console, AbortController, TextDecoder,
    setTimeout: () => 1, clearTimeout() {},
    document: { hidden: false, getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); }, createElement: element, addEventListener() {} },
    window: { location: { protocol: "http:" }, addEventListener() {}, matchMedia: () => ({ matches: false, addEventListener() {} }) },
    localStorage: { getItem: k => storage.get(k), setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k) },
    fetch: async (_url, options) => {
      calls.push(options);
      if (_url === "/api/chat/stop") return stopReply();
      const currentController = streamController;
      options.signal.addEventListener("abort", () => {
        try { currentController.error(new DOMException("Aborted", "AbortError")); } catch {}
      }, { once: true });
      return new Response(stream, { headers: { "Content-Type": "text/event-stream; charset=utf-8" } });
    },
  });
  vm.runInContext(source, context);
  return {
    elements, storage, calls,
    submit(text) { elements.get("message-input").value = text; return elements.get("chat-form").events.submit({ preventDefault() {} }); },
    stream() { stream = new ReadableStream({ start(c) { streamController = c; } }); return streamController; },
    stop() { elements.get("stop-button").events.click(); },
    stopResult(fn) { stopReply = fn; },
  };
}

(async () => {
  const app = harness();
  await app.submit("你好");
  assert.equal(app.calls.length, 0);
  assert.equal(app.elements.get("reply-text").textContent, "你好，很高兴见到你。");
  app.elements.get("model-toggle").checked = true;
  const controller = app.stream();
  const pending = app.submit("介绍一下");
  controller.enqueue(encode({ event: "conversation", conversation_id: CID }));
  // 一个字节一个分包，包含多字节中文和 emoji。
  for (const byte of encode({ event: "delta", text: "你好🙂" })) controller.enqueue(Uint8Array.of(byte));
  await tick();
  assert.equal(app.elements.get("reply-text").textContent, "你好🙂");
  assert.equal(app.elements.get("send-button").disabled, true);
  assert.equal(app.storage.get("dth.conversation.Grace"), CID);
  await app.submit("重复提交");
  assert.equal(app.calls.length, 1);
  controller.enqueue(encode({ event: "replace", text: "更新后的回答" }));
  controller.enqueue(encode({ event: "delta", text: "。" }));
  controller.enqueue(encode({ event: "done", conversation_id: CID }));
  controller.close();
  await pending;
  assert.equal(app.elements.get("reply-text").textContent, "更新后的回答。");
  assert.equal(app.elements.get("send-button").disabled, false);
  assert.equal(app.elements.get("message-input").value, "");

  for (const failure of ["error", "eof"]) {
    const c = app.stream();
    const next = app.submit("下一轮");
    c.enqueue(encode({ event: "delta", text: "保留的部分文字" }));
    if (failure === "error") c.enqueue(encode({ event: "error", error: "模拟错误" }));
    c.close();
    await next;
    assert.equal(JSON.parse(app.calls.at(-1).body).conversation_id, CID);
    assert.equal(app.elements.get("reply-text").textContent, "保留的部分文字");
    assert.equal(app.elements.get("send-button").disabled, false);
    assert.equal(app.elements.get("conversation").children.at(-1).classList.contains("incomplete"), true);
    assert.notEqual(app.elements.get("connection-message").textContent, "");
  }
  const c = app.stream();
  const empty = app.submit("无回答");
  c.close();
  await empty;
  assert.equal(app.elements.get("reply-text").textContent, "本次未收到完整回答。");
  app.elements.get("new-conversation").events.click();
  assert.equal(app.storage.size, 0);
  assert.equal(app.elements.get("conversation").children.length, 1);
  assert.match(app.elements.get("reply-text").textContent, /你好，我是 Grace/);

  const early = harness();
  early.elements.get("model-toggle").checked = true;
  const earlyStream = early.stream();
  const earlyPending = early.submit("首字前停止");
  early.stop();
  assert.equal(early.calls.length, 1); // 暂无任务编号，不发送无效停止请求。
  earlyStream.enqueue(encode({ event: "conversation", conversation_id: CID }));
  earlyStream.enqueue(encode({ event: "task", task_id: TID }));
  await earlyPending;
  assert.equal(early.elements.get("status-text").textContent, "已停止");
  assert.equal(early.storage.get("dth.conversation.Grace"), CID);
  assert.equal(early.calls.length, 2);
  assert.equal(JSON.parse(early.calls[1].body).task_id, TID);
  assert.equal(early.elements.get("send-button").hidden, false);
  assert.equal(early.elements.get("stop-button").hidden, true);
  assert.equal(early.elements.get("connection-message").textContent, "");

  const partial = harness();
  partial.elements.get("model-toggle").checked = true;
  const partialStream = partial.stream();
  const partialPending = partial.submit("中途停止");
  partialStream.enqueue(encode({ event: "conversation", conversation_id: CID }));
  partialStream.enqueue(encode({ event: "task", task_id: TID }));
  partialStream.enqueue(encode({ event: "delta", text: "已经显示的回答" }));
  await tick();
  let acknowledge;
  partial.stopResult(() => new Promise(resolve => acknowledge = resolve));
  partial.stop();
  partial.stop();
  assert.equal(partial.calls.length, 2); // 重复点击不能重复调用停止接口。
  partialStream.enqueue(encode({ event: "error", error: "云端任务已中止" }));
  partialStream.close();
  await tick();
  assert.equal(partial.elements.get("send-button").disabled, true);
  acknowledge(new Response(JSON.stringify({ status: "stopped" })));
  await partialPending;
  assert.equal(partial.elements.get("reply-text").textContent, "已经显示的回答");
  assert.equal(partial.elements.get("conversation").children.at(-1).classList.contains("stopped"), true);
  assert.equal(partial.elements.get("status-text").textContent, "已停止");
  const followStream = partial.stream();
  const follow = partial.submit("停止后的下一轮");
  followStream.enqueue(encode({ event: "delta", text: "新回答" }));
  followStream.enqueue(encode({ event: "done", conversation_id: CID }));
  followStream.close();
  await follow;
  assert.equal(JSON.parse(partial.calls.at(-1).body).conversation_id, CID);
  assert.equal(partial.elements.get("reply-text").textContent, "新回答");

  const retry = harness();
  retry.elements.get("model-toggle").checked = true;
  const retryStream = retry.stream();
  const retryPending = retry.submit("停止失败后重试");
  retryStream.enqueue(encode({ event: "task", task_id: TID }));
  await tick();
  retry.stopResult(async () => new Response(JSON.stringify({ error: "停止失败" }), { status: 502 }));
  retry.stop();
  await tick();
  assert.equal(retry.elements.get("stop-button").textContent, "重试停止");
  assert.equal(retry.elements.get("stop-button").disabled, false);
  assert.notEqual(retry.elements.get("status-text").textContent, "已停止");
  retry.stopResult(async () => new Response(JSON.stringify({ status: "stopped" })));
  retry.stop();
  await retryPending;
  assert.equal(retry.elements.get("status-text").textContent, "已停止");

  const race = harness();
  race.elements.get("model-toggle").checked = true;
  const raceStream = race.stream();
  const racePending = race.submit("停止时恰好完成");
  raceStream.enqueue(encode({ event: "task", task_id: TID }));
  await tick();
  let lateResponse;
  race.stopResult(() => new Promise(resolve => lateResponse = resolve));
  race.stop();
  raceStream.enqueue(encode({ event: "delta", text: "自然完成的回答" }));
  raceStream.enqueue(encode({ event: "done", conversation_id: CID }));
  raceStream.close();
  await racePending;
  lateResponse(new Response(JSON.stringify({ error: "任务已结束" }), { status: 404 }));
  await tick();
  assert.equal(race.elements.get("reply-text").textContent, "自然完成的回答");
  assert.equal(race.elements.get("connection-message").textContent, "");
  console.log("PASS: streaming regressions; early stop; confirmed stop preserves partial text; stop/stream races; duplicate stop prevention; stop failure and retry; continuation after stopping; natural completion wins.");
})().catch(error => { console.error(error); process.exitCode = 1; });
