// 真正的 Web Streams + 模拟音频时钟；不请求云端，不产生费用。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const context = vm.createContext({ window: {}, AbortController, TextDecoder, atob });
vm.runInContext(fs.readFileSync(require("node:path").join(__dirname, "../speech.js"), "utf8"), context);
const ReplySpeech = context.window.ReplySpeech;
const tick = () => new Promise(resolve => setImmediate(resolve));
const start = { event: "start", format: "pcm_s16le", sample_rate: 24000 };
const audio = { event: "audio", data: Buffer.alloc(24000).toString("base64") };
const done = { event: "done" };
const frame = event => new TextEncoder().encode(JSON.stringify(event) + "\n");

function setup(customFetch) {
  const events = [], sources = [], requests = [], metrics = [], timers = new Map();
  let timerId = 0;
  const clock = {
    currentTime: 0, state: "suspended", destination: {},
    resume() { this.state = "running"; return Promise.resolve(); },
    suspend() { this.state = "suspended"; return Promise.resolve(); },
    createBuffer(_channels, count, rate) {
      const values = new Float32Array(count);
      return { duration: count / rate, getChannelData: () => values };
    },
    createBufferSource() {
      const source = { connect() {}, disconnect() {}, stop() { this.stopped = true; },
        start(when) { this.when = when; sources.push(this); } };
      return source;
    },
  };
  const speech = new ReplySpeech({
    fetcher: async (url, options) => {
      requests.push({ text: JSON.parse(options.body).text, signal: options.signal, url });
      if (customFetch) return customFetch(url, options);
      return new Response([start, audio, done].map(e => JSON.stringify(e) + "\n").join(""),
        { headers: { "Content-Type": "application/x-ndjson" } });
    },
    createContext: () => clock,
    onState: event => events.push(event), onMetric: name => metrics.push(name),
    timers: { setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, at: clock.currentTime + delay / 1000 }); return id; },
      clearTimeout(id) { timers.delete(id); } },
  });
  speech.ready = true;
  return { speech, clock, events, sources, requests, metrics, timers,
    advance(time) {
      clock.currentTime = time;
      for (const [id, timer] of [...timers]) if (timer.at <= time) { timers.delete(id); timer.fn(); }
    },
    end(source) { clock.currentTime = source.when + source.buffer.duration; source.onended?.(); },
  };
}

(async () => {
  const warmed = setup(); warmed.speech.warmup(); await tick();
  assert.equal(warmed.requests.length, 0);
  assert.equal(warmed.sources.length, 0);
  assert.equal(warmed.speech.active, false);
  assert.equal(warmed.clock.state, "running");
  warmed.advance(10);
  assert.equal(warmed.clock.state, "suspended");
  // 首块已经进入播放时钟，而上游还没返回 done：证明不是整句下载后播放。
  let stream;
  const streamed = setup(() => new Response(new ReadableStream({ start(c) { stream = c; } }),
    { headers: { "Content-Type": "application/x-ndjson" } }));
  streamed.speech.begin(); streamed.speech.update("这是第一句测试语音。"); await tick();
  stream.enqueue(frame(start)); stream.enqueue(frame(audio)); await tick();
  assert.equal(streamed.sources.length, 1);
  assert.equal(streamed.speech.downloading.done, false);
  assert.equal(streamed.events.at(-1).state, "waiting");
  streamed.advance(0.12);
  assert.equal(streamed.events.at(-1).state, "speaking");
  stream.enqueue(frame(done)); stream.close(); streamed.speech.finish(); await tick();
  streamed.end(streamed.sources[0]);
  assert.equal(streamed.events.at(-1).state, "idle");
  streamed.advance(streamed.clock.currentTime + 10);
  assert.equal(streamed.clock.state, "suspended");

  // 同时最多准备两句，调度不重叠，播放结束后才继续预取第三句。
  const ahead = setup();
  ahead.speech.begin(); ahead.speech.update("这是第一句测试语音。这是第二句测试语音。这是第三句测试语音。"); ahead.speech.finish(); await tick();
  assert.equal(ahead.requests.length, 2);
  assert.equal(ahead.sources.length, 2);
  assert.ok(ahead.sources[1].when >= ahead.sources[0].when + ahead.sources[0].buffer.duration);
  ahead.advance(0.12);
  assert.equal(ahead.events.at(-1).text, "这是第一句测试语音。");
  ahead.end(ahead.sources[0]); await tick();
  assert.equal(ahead.requests.length, 3);
  assert.equal(ahead.sources.length, 3);

  // 停止取消当前和预取，旧事件不能让新一轮出声；不泄漏音频源/定时器。
  const oldEnd = ahead.sources[1].onended;
  ahead.speech.stop();
  assert.equal(ahead.speech.active, false);
  assert.equal(ahead.timers.size, 0);
  assert.ok(ahead.requests.slice(1).every(r => r.signal.aborted)); // 第一句已经正常播放结束。
  assert.ok(ahead.sources.slice(1).every(s => s.stopped));
  const eventCount = ahead.events.length;
  oldEnd();
  assert.equal(ahead.events.length, eventCount);

  // PCM 在一个采样中间分包，仍应正确还原小端数值。
  const odd = setup(() => new Response([start,
    { event: "audio", data: Buffer.from([0]).toString("base64") },
    { event: "audio", data: Buffer.from([64, 0, 192]).toString("base64") }, done,
  ].map(e => JSON.stringify(e) + "\n").join(""), { headers: { "Content-Type": "application/x-ndjson" } }));
  odd.speech.begin(); odd.speech.update("正确还原。"); odd.speech.finish(); await tick();
  assert.equal(odd.sources[0].buffer.getChannelData(0)[0], 0.5);
  assert.equal(odd.sources[0].buffer.getChannelData(0)[1], -0.5);

  // 晚到的网络响应不能播放；超时也必须立即清理。
  let deliver;
  const late = setup(() => new Promise(resolve => { deliver = resolve; }));
  late.speech.begin(); late.speech.update("晚到响应。"); late.speech.finish(); await tick();
  late.speech.stop();
  deliver(new Response("late")); await tick();
  assert.equal(late.sources.length, 0);
  const timeout = setup(() => new Promise(() => {}));
  timeout.speech.begin(); timeout.speech.update("超时测试。"); timeout.speech.finish(); await tick();
  timeout.advance(35);
  assert.equal(timeout.events.at(-1).state, "error");
  assert.equal(timeout.speech.active, false);
  assert.equal(timeout.requests[0].signal.aborted, true);

  // 第一段在自然逗号处提前送出，尾句停顿 450ms 后送出，前缀不重复朗读。
  const clauses = setup();
  clauses.speech.begin(); clauses.speech.update("这是一个超过十二个汉字的开场白，后续还没结束");
  await tick(); assert.equal(clauses.requests[0].text, "这是一个超过十二个汉字的开场白，");
  clauses.advance(0.45); await tick();
  assert.equal(clauses.requests[1].text, "后续还没结束");
  clauses.speech.begin("已经显示过。"); clauses.speech.update("已经显示过。只读新内容。"); clauses.speech.finish(); await tick();
  assert.equal(clauses.requests.at(-1).text, "只读新内容。");
  clauses.speech.begin(); clauses.speech.update("这是尚未生成完成的回答。"); await tick();
  clauses.speech.update("替换后的回答。");
  assert.equal(clauses.events.at(-1).state, "replaced");
  assert.equal(clauses.speech.active, false);

  // EOF、缺少结束标记、奇数字节和错误格式不能冒充朗读成功。
  for (const response of [
    new Response(JSON.stringify({ error: "请填写密钥" }), { status: 503 }),
    new Response("html", { headers: { "Content-Type": "text/html" } }),
    new Response([start, audio].map(e => JSON.stringify(e) + "\n").join(""), { headers: { "Content-Type": "application/x-ndjson" } }),
    new Response([start, { event: "audio", data: "AA==" }, done].map(e => JSON.stringify(e) + "\n").join(""), { headers: { "Content-Type": "application/x-ndjson" } }),
    new Response([start, { event: "error", error: "额度不足" }].map(e => JSON.stringify(e) + "\n").join(""), { headers: { "Content-Type": "application/x-ndjson" } }),
  ]) {
    const t = setup(() => response);
    t.speech.begin(); t.speech.update("错误测试。"); t.speech.finish(); await tick();
    assert.equal(t.events.at(-1).state, "error");
    assert.equal(t.speech.active, false);
  }

  const overflow = setup(); overflow.speech.begin(); overflow.speech.update("这是一句较长的测试句。".repeat(40)); await tick();
  assert.equal(overflow.events.at(-1).state, "error");
  const joined = setup(); joined.speech.begin(); joined.speech.update("你好！很高兴见到你。你今天过得怎么样？");
  joined.speech.finish(); await tick();
  assert.equal(joined.requests[0].text, "你好！很高兴见到你。");
  joined.speech.stop();
  const cleaned = setup(); cleaned.speech.begin();
  cleaned.speech.update("**你好**。[资料](https://example.com)\n" + "\x60\x60\x60js\nconsole.log('不要读');\n\x60\x60\x60\n再见。");
  cleaned.speech.finish();
  for (let i = 0; i < 10; i++) {
    await tick();
    const source = cleaned.sources[i];
    if (!source) break;
    cleaned.end(source);
  }
  const text = cleaned.requests.map(r => r.text).join("");
  assert.match(text, /你好/); assert.match(text, /资料/); assert.match(text, /再见/);
  assert.doesNotMatch(text, /console|http|\*/);
  console.log("PASS: first-chunk playback before EOF, bounded prefetch, gap-free scheduling, PCM boundaries, stop/races, timeouts, segmentation, errors, filters. No provider calls.");
})().catch(error => { console.error(error); process.exitCode = 1; });
