"""本机缓存：JSON 是你编辑的测试台词，SQLite 是程序保存的音频仓库。"""

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from time import time


class CacheError(ValueError):
    """可以展示给网页的缓存错误，不暴露本机路径或数据库原始错误。"""


def digest(value):
    """同样的内容得到同样的编号；不把 API Key 放入缓存。"""
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def load_cases(path):
    """每次重新读取，修改 JSON 后无需重启后端；配置有误时拒绝测试请求。"""
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 65536:
            raise ValueError
        cases = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(cases, list) or not 1 <= len(cases) <= 30:
            raise ValueError
        ids, inputs = set(), set()
        for case in cases:
            if not isinstance(case, dict) or set(case) != {"id", "inputs", "reply"}:
                raise ValueError
            name, aliases, reply = case["id"], case["inputs"], case["reply"]
            if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", name) or name in ids:
                raise ValueError
            ids.add(name)
            if not isinstance(reply, str) or not 1 <= len(reply.strip()) <= 500:
                raise ValueError
            if not isinstance(aliases, list) or not 1 <= len(aliases) <= 20:
                raise ValueError
            for alias in aliases:
                if not isinstance(alias, str) or not 1 <= len(alias.strip()) <= 500 or alias.strip() in inputs:
                    raise ValueError
                inputs.add(alias.strip())
        return cases, digest(cases)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        raise CacheError("test_cases.json 格式有误：检查 id 是否唯一、inputs 是否重复、reply 是否为 1～500 字文本。") from None


class AudioCache:
    def __init__(self, path, max_bytes=100 * 1024 * 1024):
        self.path = Path(path)
        self.max_bytes = max_bytes  # 限制有效音频数据量；数据库还会有少量索引开销。

    @contextmanager
    def connect(self, create=False):
        connection = None
        try:
            if create:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=3)
            if create:
                connection.execute("""CREATE TABLE IF NOT EXISTS audio (
                    key TEXT PRIMARY KEY, text TEXT NOT NULL, profile TEXT NOT NULL,
                    audio BLOB NOT NULL, checksum TEXT NOT NULL, size INTEGER NOT NULL,
                    created REAL NOT NULL, used REAL NOT NULL, hits INTEGER NOT NULL DEFAULT 0,
                    pinned INTEGER NOT NULL DEFAULT 0)""")
            yield connection
            connection.commit()
        except (OSError, sqlite3.Error):
            if connection:
                connection.rollback()
            raise CacheError("无法读写音频缓存，请检查 data 目录权限或数据库文件是否损坏。") from None
        finally:
            if connection:
                connection.close()

    def get(self, key, touch=True):
        if not self.path.exists():
            return None
        with self.connect() as db:
            row = db.execute("SELECT audio, checksum, size FROM audio WHERE key=?", (key,)).fetchone()
            if row is None:
                return None
            audio, checksum, size = row
            if not isinstance(audio, bytes) or not audio or len(audio) != size or sha256(audio).hexdigest() != checksum:
                db.execute("DELETE FROM audio WHERE key=?", (key,))
                return None  # 损坏的一条音频不能继续播放，也不影响其他条目。
            if touch:
                db.execute("UPDATE audio SET used=?, hits=hits+1 WHERE key=?", (time(), key))
            return audio

    def put(self, key, text, profile, audio, pinned=False):
        if not audio or len(audio) > 4 * 1024 * 1024:
            raise CacheError("音频为空或超过单条 4MB 上限，未保存。")
        with self.connect(create=True) as db:
            db.execute("BEGIN IMMEDIATE")
            # 一个事务完成淘汰和写入：保存失败时不会留下半条录音。
            previous = db.execute("SELECT pinned FROM audio WHERE key=?", (key,)).fetchone()
            protected = pinned or bool(previous and previous[0])
            total = db.execute("SELECT COALESCE(SUM(size),0) FROM audio WHERE key<>?", (key,)).fetchone()[0]
            for old_key, size in db.execute("SELECT key,size FROM audio WHERE pinned=0 AND key<>? ORDER BY used", (key,)).fetchall():
                if total + len(audio) <= self.max_bytes:
                    break
                db.execute("DELETE FROM audio WHERE key=?", (old_key,))
                total -= size
            if total + len(audio) > self.max_bytes:
                db.rollback()
                raise CacheError("缓存额度已被保留的测试音频占用，请减少测试素材或调整容量。")
            now = time()
            db.execute("""INSERT OR REPLACE INTO audio
                (key,text,profile,audio,checksum,size,created,used,hits,pinned)
                VALUES (?,?,?,?,?,?,?,?,0,?)""",
                (key, text, json.dumps(profile, sort_keys=True), audio, sha256(audio).hexdigest(),
                 len(audio), now, now, int(protected)))

    def pin(self, keys):
        """只保留当前测试台词对应的音频；改掉的旧台词可以正常淘汰。"""
        if not self.path.exists():
            return
        with self.connect() as db:
            db.execute("UPDATE audio SET pinned=0")
            db.executemany("UPDATE audio SET pinned=1 WHERE key=?", ((key,) for key in keys))

    def stats(self):
        if not self.path.exists():
            return {"entries": 0, "bytes": 0, "hits": 0}
        with self.connect() as db:
            count, size, hits = db.execute("SELECT COUNT(*),COALESCE(SUM(size),0),COALESCE(SUM(hits),0) FROM audio").fetchone()
            return {"entries": count, "bytes": size, "hits": hits}
