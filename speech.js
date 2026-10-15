// 语音播放层：PCM 音频边到边播，最多提前准备一句，不安装新依赖。
(() => {
  // 准备与播放共用一个分句器，修改台词后不会出现音频对不上的情况。
  class SpeechParts {
    constructor(prefix = "") {
      this.text = prefix;
      this.offset = prefix.length;
      this.inCode = (prefix.split(String.fromCharCode(96).repeat(3)).length - 1) % 2 === 1;
      this.firstQueued = false;
    }

    clean(segment) {
      const parts = segment.split(String.fromCharCode(96).repeat(3));
      let text = "";
      parts.forEach((part, index) => {
        if (index) this.inCode = !this.inCode;
        if (!this.inCode) text += part;
      });
      return text.replace(/!\[[^\]]*\]\([^)]*\)/g, "")
        .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
        .replace(/https?:\/\/\S+/g, "")
        .replace(/[*_\x60#>]/g, "").trim();
    }

    next(tail) {
      while (this.offset < this.text.length) {
        const rest = this.text.slice(this.offset);
        // 第一段可在至少 12 字后的逗号开口；后续仍按整句，避免声音过碎。
        const firstPause = !this.firstQueued ? /[，,：:]/g : null;
        let clause = null, match;
        while (firstPause && (match = firstPause.exec(rest))) {
          if (Array.from(rest.slice(0, match.index)).length >= 12) { clause = match; break; }
        }
        // “你好！”过短，单独合成会让下一句赶不上播放；尽量合并到至少 10 字。
        const boundary = [...rest.matchAll(/[。！？!?；;\n]|\.(?=\s)/g)]
          .find(item => Array.from(rest.slice(0, item.index + 1)).length >= 10);
        const pause = clause && (!boundary || clause.index < boundary.index) ? clause : boundary;
        const limit = Array.from(rest).slice(0, this.firstQueued ? 60 : 32).join("").length;
        let length;
        if (pause && pause.index < limit) length = pause.index + 1;
        else if (rest.length > limit) {
          const head = rest.slice(0, limit);
          const comma = Math.max(head.lastIndexOf("，"), head.lastIndexOf(","), head.lastIndexOf(" "));
          length = comma >= 12 ? comma + 1 : limit;
        } else if (tail) length = rest.length;
        else return null;
        // 不从 Markdown 链接中间切开，否则 URL 的后半截可能被当成正文朗读。
        for (const link of rest.matchAll(/!?\[[^\]]*\]\([^)]*\)/g)) {
          if (link.index < length && link.index + link[0].length > length) {
            length = link.index || link[0].length;
            break;
          }
        }
        const raw = rest.slice(0, length);
        this.offset += length;
        const text = this.clean(raw);
        if (!/[\p{L}\p{N}]/u.test(text)) continue;
        this.firstQueued = true;
        return text;
      }
      return null;
    }
  }

  class ReplySpeech {
    constructor({ fetcher = fetch,
      createContext = typeof AudioContext === "function" ? () => new AudioContext({ latencyHint: "interactive" }) : null,
      onState = () => {}, onMetric = () => {}, timers = window }) {
      this.fetcher = (...args) => fetcher(...args);
      this.createContext = createContext;
      this.onState = onState;
      this.onMetric = onMetric;
      this.timers = timers;
      this.supported = Boolean(fetcher && createContext);
      this.ready = false;
      this.epoch = 0;
      this.queue = [];
      this.jobs = [];
      this.accepting = false;
      this.finished = true;
      this.context = null; // 整个页面复用一个音频时钟；空闲时挂起，节省计算。
      this.text = "";
      this.parts = new SpeechParts();
    }

    get active() {
      return Boolean(this.jobs.length || this.queue.length || (this.accepting && !this.finished));
    }

    emit(state, text = "") { this.onState({ state, text, active: this.active }); }

    warmup() {
      // 勾选语音时提前初始化音频设备，不请求模型/语音；发送时不用再等冷启动。
      if (!this.supported || !this.ready) return;
      try {
        this.context ||= this.createContext();
        void this.context.resume().then(() => {
          if (!this.active) this.park();
        }).catch(() => this.emit("error", "not-allowed"));
      } catch { this.emit("error", "not-allowed"); }
    }

    begin(prefix = "", { cacheOnly = false } = {}) {
      this.stop(false, false); // 新一轮只清理旧声音，保留已预热的设备。
      this.text = prefix;
      this.parts = new SpeechParts(prefix);
      this.cacheOnly = cacheOnly;
      this.firstQueued = false;
      this.scheduledAudio = false;
      this.finished = false;
      this.accepting = this.supported && this.ready;
      if (!this.accepting) return;
      try {
        this.context ||= this.createContext();
        // begin 必须由点击/提交事件直接触发，提前解锁浏览器的声音权限。
        this.audioReady = this.context.resume();
        this.audioReady.catch(() => {}); // loadAudio 内处理错误，避免未捕获的 Promise。
        this.nextTime = this.context.currentTime;
        this.emit("waiting");
      } catch { this.fail("not-allowed"); }
    }

    update(text) {
      if (!this.accepting || this.finished) return;
      if (!text.startsWith(this.text)) {
        this.stop(false);
        this.emit("replaced");
        return;
      }
      this.text = text;
      this.parts.text = text;
      this.timers.clearTimeout(this.flushTimer);
      this.flush(false);
      if (this.accepting && this.parts.offset < this.text.length) {
        const epoch = this.epoch;
        this.flushTimer = this.timers.setTimeout(() => {
          if (epoch === this.epoch && this.accepting) this.flush(true);
        }, 450);
      }
    }

    static split(text) {
      const parts = new SpeechParts();
      parts.text = text;
      const result = [];
      let part;
      while ((part = parts.next(true)) !== null) {
        if (result.length >= 24) throw new Error("测试回答过长，请缩短到 24 段以内。");
        result.push(part);
      }
      return result;
    }

    flush(tail) {
      while (this.accepting) {
        const text = this.parts.next(tail);
        if (text === null) break;
        if (this.queue.length >= 24) { this.fail("queue-full"); return; }
        if (!this.firstQueued) this.onMetric("first_clause");
        this.firstQueued = true;
        this.queue.push(text);
        this.pump();
      }
    }

    finish() {
      if (!this.accepting) return;
      this.timers.clearTimeout(this.flushTimer);
      this.flush(true);
      this.finished = true;
      this.settle();
    }

    settle() {
      if (!this.accepting || this.jobs.length || this.queue.length) return;
      this.emit(this.finished ? "idle" : "waiting");
      if (this.finished) this.park();
    }

    park() {
      this.timers.clearTimeout(this.idleTimer);
      // 连续测试时不反复冷启动；空闲10秒再挂起。显式停止仍立即挂起。
      this.idleTimer = this.timers.setTimeout(() => {
        if (!this.active) void this.context?.suspend().catch(() => {});
      }, 10000);
    }

    pump() {
      // 上游串行合成；当前句播放时，最多提前合成下一句。绝不同时播两句话。
      if (this.downloading || this.jobs.length >= 2 || !this.queue.length || !this.accepting) return;
      const job = { text: this.queue.shift(), epoch: this.epoch, sources: new Set(), timers: new Set(),
        controller: new AbortController(), cacheOnly: this.cacheOnly, done: false, started: false, bytes: 0, carry: null };
      this.jobs.push(job);
      this.downloading = job;
      const valid = () => this.accepting && this.epoch === job.epoch && this.jobs.includes(job);
      job.valid = valid;
      this.later(job, () => { if (!job.started) this.fail("start-timeout"); }, 35000);
      this.later(job, () => this.fail("playback-timeout"), Math.max(60000, job.text.length * 1000));
      void this.loadAudio(job);
    }

    later(job, callback, delay) {
      const id = this.timers.setTimeout(() => {
        job.timers.delete(id);
        if (job.valid()) callback();
      }, delay);
      job.timers.add(id);
    }

    // 播放器和准备按钮共用解析器；必须收到 done，断网不能冒充保存成功。
    static async consume(response, onEvent = () => {}, valid = () => true) {
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || "语音请求失败。");
      }
      if (!response.body || !response.headers.get("Content-Type")?.includes("application/x-ndjson")) {
        throw new Error("请重启最新 Python 后端，以使用流式语音与缓存。");
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8", { fatal: true });
      let buffer = "", started = false, bytes = 0;
      try {
        while (valid()) {
          const { value, done } = await reader.read();
          if (!valid()) return null;
          buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
          if (buffer.length > 1048576) throw new Error("语音数据过大。");
          let newline;
          while ((newline = buffer.indexOf("\n")) >= 0) {
            const line = buffer.slice(0, newline).trim();
            buffer = buffer.slice(newline + 1);
            if (!line) continue;
            const event = JSON.parse(line);
            if (event.event === "error") throw new Error(event.error || "语音生成失败。");
            if (event.event === "start") {
              if (started || event.format !== "pcm_s16le" || event.sample_rate !== 24000) throw new Error("语音格式不正确。");
              started = true;
            } else if (event.event === "audio") {
              if (!started || typeof event.data !== "string") throw new Error("语音片段顺序不正确。");
              event.raw = atob(event.data);
              bytes += event.raw.length;
              if (bytes > 4 * 1024 * 1024) throw new Error("语音数据过大。");
            } else if (event.event === "done") {
              if (!started || !bytes || bytes % 2) throw new Error("语音数据不完整。");
              onEvent(event);
              return event;
            } else throw new Error("未知语音事件。");
            onEvent(event);
          }
          if (done) throw new Error("语音连接中断，本轮已停止，请手动重试。");
        }
        return null;
      } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
      }
    }

    async loadAudio(job) {
      try {
        await this.audioReady;
        if (!job.valid()) return;
        if (this.context.state !== "running") throw new Error("not-allowed");
        this.onMetric("tts_request");
        // 独立测试语音入口强制只读缓存，绝不因为缺少音频而调用云端。
        const response = await this.fetcher(job.cacheOnly ? "/api/test/tts" : "/api/tts", {
          method: "POST",
          headers: { "Content-Type": "application/json", "Accept": "application/x-ndjson" },
          body: JSON.stringify({ text: job.text }), signal: job.controller.signal,
        });
        await ReplySpeech.consume(response, event => {
          if (event.event === "start" && event.cache) this.onMetric("cache_" + event.cache);
          if (event.event === "audio") this.schedule(job, event.raw);
          if (event.event === "done" && event.warning) this.onMetric("cache_unsaved");
        }, job.valid);
        if (!job.valid()) return;
        job.done = true;
        this.downloading = null;
        this.nextTime += 0.08;
        this.complete(job);
        this.pump();
      } catch (error) {
        if (job.valid()) this.fail(error.name === "NotAllowedError" ? "not-allowed" : error.message);
      }
    }

    schedule(job, raw) {
      if (!raw.length) return;
      if (!job.bytes) this.onMetric("first_audio");
      job.bytes += raw.length;
      if (job.bytes > 4 * 1024 * 1024) throw new Error("语音数据过大。");
      // PCM16 为小端有符号整数。网络可能在一个采样中间分包，保留多余的一字节。
      const bytes = new Uint8Array(raw.length + (job.carry !== null ? 1 : 0));
      let offset = 0;
      if (job.carry !== null) bytes[offset++] = job.carry;
      for (let i = 0; i < raw.length; i++) bytes[offset + i] = raw.charCodeAt(i);
      job.carry = bytes.length % 2 ? bytes[bytes.length - 1] : null;
      const count = Math.floor(bytes.length / 2);
      if (!count) return;
      const audio = this.context.createBuffer(1, count, 24000);
      const samples = audio.getChannelData(0);
      const view = new DataView(bytes.buffer);
      for (let i = 0; i < count; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
      const source = this.context.createBufferSource();
      source.buffer = audio;
      source.connect(this.context.destination);
      // 用音频时钟排队，120ms 小缓冲吸收网络抖动，避免逐块 play 造成缝隙。
      const when = Math.max(this.nextTime, this.context.currentTime + 0.12);
      if (this.scheduledAudio && this.nextTime < this.context.currentTime) this.onMetric("underrun");
      this.scheduledAudio = true;
      this.nextTime = when + audio.duration;
      job.sources.add(source);
      if (!job.scheduled) {
        job.scheduled = true;
        const announce = () => {
          // 定时器只更新字幕，声音由音频线程调度；标签页短暂停顿不会错排音频。
          if (this.context.currentTime + 0.005 < when) {
            this.later(job, announce, Math.max(10, (when - this.context.currentTime) * 1000)); return;
          }
          job.started = true;
          this.onMetric("first_play");
          this.emit("speaking", job.text);
        };
        this.later(job, announce, Math.max(0, (when - this.context.currentTime) * 1000));
      }
      source.onended = () => {
        source.disconnect();
        job.sources.delete(source);
        if (job.valid()) this.complete(job);
      };
      source.start(when);
    }

    complete(job) {
      if (!job.done || job.sources.size || !job.valid()) return;
      for (const id of job.timers) this.timers.clearTimeout(id);
      this.jobs.splice(this.jobs.indexOf(job), 1);
      this.pump();
      this.settle();
    }

    fail(reason) {
      this.stop(false);
      const hints = {
        "queue-full": "待播内容过多，本轮朗读已停止。",
        "start-timeout": "语音等待过久，本轮朗读已停止。",
        "playback-timeout": "语音播放超时，本轮朗读已停止。",
      };
      this.emit("error", hints[reason] || reason || "语音播放失败。");
    }

    stop(notify = true, suspend = true) {
      this.epoch += 1;
      this.accepting = false;
      this.finished = true;
      this.queue = [];
      this.timers.clearTimeout(this.flushTimer);
      this.timers.clearTimeout(this.idleTimer);
      for (const job of this.jobs) {
        job.controller.abort();
        for (const id of job.timers) this.timers.clearTimeout(id);
        for (const source of job.sources) {
          source.onended = null;
          try { source.stop(); } catch {}
          source.disconnect();
        }
      }
      this.jobs = [];
      this.downloading = null;
      if (suspend) void this.context?.suspend().catch(() => {});
      if (notify) this.emit("stopped");
    }
  }
  window.ReplySpeech = ReplySpeech;
})();
