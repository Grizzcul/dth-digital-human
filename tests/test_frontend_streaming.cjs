// 使用真实 Web Streams/UTF-8 解码器，模拟 DOM 和网络；不调用云端模型。
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const source = fs.readFileSync(require("node:path").join(__dirname, "../script.js"), "utf8");
const CID = "00000000-0000-4000-8000-000000000001";
const TID = "00000000-0000-4000-8000-000000000010";
const tick = () => new Promise(resolve => setImmediate(resolve));
const encode = event => new TextEncoder().encode(`data: ${JSON.stringify(event)}\r\n\r\n`);

function harness({ audioFactory = null, assets = { image: "png/avatar.png", video: "png/avatar.webm" }, assetFailure = false, testAudioCached = true, prepareResponse = null } = {}) {
  const elements = new Map();
  const storage = new Map();
  const calls = [];
  const testCalls = [], ttsCalls = [];
  const fixtures = JSON.parse(fs.readFileSync(require("node:path").join(__dirname, "../test_cases.json"), "utf8"));
  const scheduled = new Map();
  let timerId = 0;
  const audioContext = {
    state: "suspended", currentTime: 0, destination: {},
    resume() { this.state = "running"; return Promise.resolve(); },
    suspend() { this.state = "suspended"; return Promise.resolve(); },
    createBuffer(_channels, count, rate) { return { duration: count / rate, getChannelData: () => new Float32Array(count) }; },
    createBufferSource() {
      const audio = audioFactory();
      return Object.assign(audio, { connect() {}, disconnect() {}, start() { audio.play(); }, stop() { audio.pause(); } });
    },
  };
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
      setAttribute() {}, focus() {}, pause() {}, load() {}, play() { this.paused = false; return Promise.resolve(); },
      append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } },
      appendChild(child) { this.append(child); },
      replaceChildren() { this.children = []; },
      querySelector(selector) { return this.children.find(c => c.classList.contains(selector.slice(1))); },
    };
  }
  const context = vm.createContext({
    console, AbortController, AbortSignal, TextDecoder, atob,
    AudioContext: audioFactory ? function () { return audioContext; } : undefined,
    Audio: audioFactory ? function () { return audioFactory(); } : undefined,
    URL: Object.assign(function () {}, { createObjectURL: () => "blob:test", revokeObjectURL() {} }),
    setTimeout(fn, delay) { const id = ++timerId; scheduled.set(id, { fn, at: audioContext.currentTime + delay / 1000 }); return id; },
    clearTimeout(id) { scheduled.delete(id); },
    document: { hidden: false, getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); }, createElement: element, addEventListener() {} },
    window: { location: { protocol: "http:" }, addEventListener() {}, matchMedia: () => ({ matches: false, addEventListener() {} }) },
    localStorage: { getItem: k => storage.get(k), setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k) },
    fetch: async (_url, options) => {
      if (_url === "/api/test/cases") return new Response(JSON.stringify({ cases: fixtures, revision: "fixture-version" }));
      if (_url === "/api/test/reply") {
        testCalls.push({ url: _url, body: JSON.parse(options.body) });
        const fixture = fixtures.find(item => item.inputs.includes(JSON.parse(options.body).message.trim()));
        return new Response(JSON.stringify(fixture ? { reply: fixture.reply, source: "test_cache" } : { error: "测试句未收录" }), { status: fixture ? 200 : 404 });
      }
      if (_url === "/api/test/plan") {
        testCalls.push({ url: _url, body: JSON.parse(options.body) });
        const items = JSON.parse(options.body).cases.flatMap(item => item.segments.map(text => ({ text, cached: testAudioCached })));
        return new Response(JSON.stringify({ items, plan_id: "plan-version", can_generate: true, voice: "魅力女友 2.0" }));
      }
      if (["/api/tts", "/api/test/tts", "/api/test/prepare"].includes(_url)) {
        if (options.method === "POST") {
          ttsCalls.push({ url: _url, body: JSON.parse(options.body) });
          if (_url === "/api/test/prepare" && prepareResponse) return prepareResponse(options);
          if (_url === "/api/test/tts" && !testAudioCached) return new Response(JSON.stringify({ error: "没有对应音频缓存" }), { status: 412 });
          return new Response([
          { event: "start", format: "pcm_s16le", sample_rate: 24000 },
          { event: "audio", data: "AAAAAA==" }, { event: "done", cache_saved: true },
        ].map(event => JSON.stringify(event) + "\n").join(""), { headers: { "Content-Type": "application/x-ndjson" } });
        }
        return new Response(JSON.stringify({ configured: true, voice: "魅力女友 2.0" }));
      }
      if (_url === "/api/avatar") {
        if (assetFailure) throw new TypeError("offline");
        return new Response(JSON.stringify(assets), { headers: { "Content-Type": "application/json" } });
      }
      calls.push(options);
      if (_url === "/api/chat/stop") return stopReply();
      const currentController = streamController;
      options.signal.addEventListener("abort", () => {
        try { currentController.error(new DOMException("Aborted", "AbortError")); } catch {}
      }, { once: true });
      return new Response(stream, { headers: { "Content-Type": "text/event-stream; charset=utf-8" } });
    },
  });
  vm.runInContext(fs.readFileSync(require("node:path").join(__dirname, "../speech.js"), "utf8"), context);
  vm.runInContext(source, context);
  return {
    elements, storage, calls, testCalls, ttsCalls,
    advanceAudio(time) {
      audioContext.currentTime = time;
      for (const [id, timer] of [...scheduled]) if (timer.at <= time) { scheduled.delete(id); timer.fn(); }
    },
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
  assert.equal(app.storage.get("dth.conversation.avatar"), CID);
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
  assert.match(app.elements.get("reply-text").textContent, /你好，我是 数字人助手/);

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
  assert.equal(early.storage.get("dth.conversation.avatar"), CID);
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

  const spoken = [];
  let cancels = 0;
  const voiced = harness({ audioFactory: () => ({
    play() { spoken.push(this); return Promise.resolve(); },
    pause() { cancels++; }, removeAttribute() {},
  }) });
  await tick();
  assert.equal(voiced.elements.get("speech-toggle").checked, false);
  voiced.elements.get("speech-toggle").checked = true;
  voiced.elements.get("speech-toggle").events.change();
  await voiced.submit("你好");
  await tick();
  assert.equal(spoken.length, 1);
  assert.equal(voiced.elements.get("spoken-caption").hidden, true);
  voiced.advanceAudio(0.12);
  assert.equal(voiced.elements.get("spoken-text").textContent, "你好，很高兴见到你。");
  spoken[0].onended();
  assert.equal(voiced.elements.get("speech-status").textContent, "朗读完成");
  voiced.elements.get("model-toggle").checked = true;
  const voiceStream = voiced.stream();
  const voicePending = voiced.submit("边生成边朗读");
  voiceStream.enqueue(encode({ event: "task", task_id: TID }));
  voiceStream.enqueue(encode({ event: "delta", text: "这是第一句测试语音。下一句还没完成" }));
  await tick();
  assert.equal(spoken.length, 2);
  voiced.advanceAudio(0.24);
  assert.equal(voiced.elements.get("spoken-text").textContent, "这是第一句测试语音。");
  const beforeStop = cancels;
  const lateEnd = spoken[1].onended;
  voiced.stop();
  assert.ok(cancels > beforeStop); // 立即静音，不等待云端确认。
  await voicePending;
  lateEnd();
  assert.equal(spoken.length, 2);
  assert.equal(voiced.elements.get("speech-stop").hidden, true);
  assert.equal(app.elements.get("speech-toggle").disabled, true); // 不支持语音的环境仍可聊天。
  // 素材检测不调用模型；视频加载完成前保留图片，缺失/损坏时仍可聊天。
  const animated = harness();
  await tick();
  const video = animated.elements.get("avatar-video");
  const fallback = animated.elements.get("avatar-fallback");
  assert.equal(video.src, "png/avatar.webm");
  assert.equal(video.hidden, true);
  assert.equal(fallback.hidden, false);
  video.readyState = 2;
  video.events.loadeddata();
  await tick();
  assert.equal(video.hidden, false);
  assert.equal(fallback.hidden, true);
  assert.equal(video.paused, false);
  video.events.error();
  assert.equal(video.hidden, true);
  assert.equal(fallback.hidden, false);
  assert.match(animated.elements.get("asset-message").textContent, /无法播放/);
  assert.equal(animated.calls.length, 0);
  for (const settings of [
    { assets: { image: "png/avatar.png", video: null } },
    { assets: { image: null, video: null } },
    { assetFailure: true },
    { assets: { image: "png/avatar.png", video: "https://example.invalid/video" } },
  ]) {
    const pictureOnly = harness(settings);
    await tick();
    assert.equal(pictureOnly.elements.get("avatar-video").hidden, true);
    assert.equal(pictureOnly.elements.get("avatar-fallback").hidden, false);
    assert.notEqual(pictureOnly.elements.get("asset-message").textContent, "");
    await pictureOnly.submit("你好");
    assert.equal(pictureOnly.elements.get("reply-text").textContent, "你好，很高兴见到你。");
    assert.equal(pictureOnly.calls.length, 0);
  }
  const mp4 = harness({ assets: { image: "png/avatar.png", video: "png/avatar.mp4" } });
  await tick();
  assert.equal(mp4.elements.get("avatar-video").src, "png/avatar.mp4");

  // 先创建真实模型会话，再进入测试模式；测试命中与未命中都不能改变原编号。
  const rehearsal = harness({ audioFactory: () => ({ play() { return Promise.resolve(); }, pause() {} }) });
  await tick();
  rehearsal.elements.get("model-toggle").checked = true;
  const modelStream = rehearsal.stream();
  const modelPending = rehearsal.submit("你好"); // 普通模式中，即使是已收录的问题也继续调用模型。
  modelStream.enqueue(encode({ event: "delta", text: "模型回答。" }));
  modelStream.enqueue(encode({ event: "done", conversation_id: CID }));
  modelStream.close(); await modelPending;
  const modelCalls = rehearsal.calls.length;
  rehearsal.elements.get("test-toggle").checked = true;
  rehearsal.elements.get("test-toggle").events.change();
  assert.equal(rehearsal.elements.get("model-toggle").disabled, true);
  rehearsal.elements.get("speech-toggle").checked = true;
  await rehearsal.submit(" 你好呀 "); await tick();
  assert.equal(rehearsal.elements.get("reply-text").textContent, "你好呀，很高兴又见到你。");
  assert.equal(rehearsal.ttsCalls.at(-1).url, "/api/test/tts");
  assert.equal(rehearsal.calls.length, modelCalls);
  assert.equal(rehearsal.storage.get("dth.conversation.avatar"), CID);
  const voiceCalls = rehearsal.ttsCalls.length;
  await rehearsal.submit("这是未收录的测试句"); await tick();
  assert.equal(rehearsal.ttsCalls.length, voiceCalls);
  assert.equal(rehearsal.calls.length, modelCalls);
  assert.match(rehearsal.elements.get("reply-source").textContent, /没有自动调用云端/);
  rehearsal.elements.get("test-toggle").checked = false;
  rehearsal.elements.get("test-toggle").events.change();
  assert.equal(rehearsal.elements.get("model-toggle").disabled, false);
  assert.equal(rehearsal.storage.get("dth.conversation.avatar"), CID);

  const preparation = harness({ testAudioCached: false });
  await tick();
  assert.equal(preparation.ttsCalls.length, 0); // 页面加载和检查不会合成。
  await preparation.elements.get("cache-check").events.click();
  assert.equal(preparation.ttsCalls.length, 0);
  assert.equal(preparation.elements.get("cache-prepare").hidden, false);
  assert.match(preparation.elements.get("cache-status").textContent, /产生语音用量/);
  await preparation.elements.get("cache-prepare").events.click();
  assert.equal(preparation.ttsCalls.length, 3);
  assert.ok(preparation.ttsCalls.every(call => call.url === "/api/test/prepare"));
  assert.equal(preparation.calls.length, 0);
  assert.match(preparation.elements.get("cache-status").textContent, /准备完成/);
  const alreadyCached = harness(); await tick();
  await alreadyCached.elements.get("cache-check").events.click();
  assert.equal(alreadyCached.elements.get("cache-prepare").hidden, true);
  assert.equal(alreadyCached.ttsCalls.length, 0);
  const cancellable = harness({ testAudioCached: false, prepareResponse: options => new Promise((_resolve, reject) => {
    options.signal.addEventListener("abort", () => reject(new DOMException("cancelled", "AbortError")), { once: true });
  }) });
  await tick();
  await cancellable.elements.get("cache-check").events.click();
  const preparing = cancellable.elements.get("cache-prepare").events.click();
  await tick();
  assert.equal(cancellable.elements.get("test-toggle").disabled, true);
  await cancellable.elements.get("cache-prepare").events.click(); // 重复点击不能再次合成。
  assert.equal(cancellable.ttsCalls.length, 1);
  cancellable.elements.get("cache-cancel").events.click();
  await preparing;
  assert.equal(cancellable.ttsCalls.length, 1);
  assert.match(cancellable.elements.get("cache-status").textContent, /停止本地准备/);
  assert.equal(cancellable.elements.get("cache-check").disabled, false);
  console.log("PASS: isolated test mode, exact aliases, no cloud fallback, conversation preservation, explicit preparation, shared segmentation, cache hits skip preparation.");
  console.log("PASS: single-avatar assets; static fallback; missing or invalid assets preserve chat; WebM/MP4 loading.");
  console.log("PASS: streaming regressions; early stop; confirmed stop preserves partial text; stop/stream races; duplicate stop prevention; stop failure and retry; continuation after stopping; natural completion wins.");
})().catch(error => { console.error(error); process.exitCode = 1; });
