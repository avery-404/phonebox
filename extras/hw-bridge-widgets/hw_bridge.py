# -*- coding: utf-8 -*-
"""
PhoneBox 硬體資料橋接程式

用途：
    讀取 MSI Afterburner / RTSS 的共享記憶體（MAHMSharedMemory），
    每秒整理成一包 JSON，推送到本機 PhoneBox（/api/push/hw），
    讓手機上的自訂頁面能顯示 CPU/GPU 溫度、頻率、功耗、風扇轉速與遊戲 FPS。

前提：
    MSI Afterburner 要在執行中（RTSS 會跟著它啟動）。
    只讀取共享記憶體，不會修改 Afterburner 或任何硬體設定。

執行：
    pythonw hw_bridge.py      （無視窗背景執行）
    python  hw_bridge.py -v   （前景執行並印出每次的摘要，除錯用）
"""
import ctypes
import json
import mmap
import struct
import sys
import time
import urllib.request

TAG = "MAHMSharedMemory"          # Afterburner 共享記憶體名稱
PUSH_URL = "http://127.0.0.1:8765/api/push/hw?ttl=6"   # 推送位址；6 秒沒更新就自動消失
INTERVAL = 1.0                    # 推送間隔（秒）
VERBOSE = "-v" in sys.argv


def single_instance():
    """用具名 Mutex 確保只會有一個橋接程式在跑；重複啟動就直接結束。"""
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\PhoneBoxHwBridge")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)
    return handle


def open_mahm():
    """開啟共享記憶體。實際大小不固定，由大到小逐一嘗試直到成功。"""
    for size in range(262144, 16383, -4096):
        try:
            return mmap.mmap(-1, size, tagname=TAG, access=mmap.ACCESS_READ)
        except Exception:
            continue
    return None


def cstr(raw):
    """把以 \\0 結尾的位元組字串轉成文字。"""
    return raw.split(b"\0", 1)[0].decode("mbcs", errors="replace")


def read_entries(mm):
    """解析所有感測項目，回傳 {名稱: 數值}；無效值（FLT_MAX / NaN）記為 None。"""
    sig, ver, hsize, count, esize, _t = struct.unpack_from("<6I", mm, 0)
    if mm[0:4] not in (b"MHAM", b"MAHM"):  # 實際讀到的簽章是 MHAM；兩種寫法都接受
        raise ValueError("簽章不符")
    out = {}
    for i in range(count):
        off = hsize + i * esize
        if off + 1324 > len(mm):
            break
        name = cstr(mm[off:off + 260])
        data = struct.unpack_from("<f", mm, off + 1300)[0]
        out[name] = None if (data != data or abs(data) > 3.0e38) else round(data, 2)
    return out


def pick(e, key):
    """取一個項目的值，沒有就回傳 None。"""
    return e.get(key)


def build_payload(e):
    """把原始感測項目整理成手機頁面要用的精簡結構。"""
    # CPU：Afterburner 以「執行緒」為單位列出（10900K 是 20 條），兩條合成一顆實體核心
    threads = sum(1 for k in e if k.startswith("CPU") and k.endswith(" usage") and k[3:-6].isdigit())
    cores = []
    for c in range(threads // 2):
        a, b = 2 * c + 1, 2 * c + 2
        ua, ub = e.get(f"CPU{a} usage"), e.get(f"CPU{b} usage")
        fa, fb = e.get(f"CPU{a} clock"), e.get(f"CPU{b} clock")
        us = [v for v in (ua, ub) if v is not None]
        fs = [v for v in (fa, fb) if v is not None]
        cores.append([
            round(sum(us) / len(us), 1) if us else None,   # 使用率 %
            round(max(fs)) if fs else None,                # 時脈 MHz
            e.get(f"CPU{a} temperature"),                  # 溫度 °C
        ])
    disks = []
    for n in range(1, 9):
        if f"HDD{n} usage" in e:
            disks.append([e.get(f"HDD{n} usage"), e.get(f"HDD{n} read rate"), e.get(f"HDD{n} write rate")])
    return {
        "t": int(time.time()),
        "cpu": {
            "u": pick(e, "CPU usage"), "t": pick(e, "CPU temperature"),
            "f": pick(e, "CPU clock"), "p": pick(e, "CPU power"), "cores": cores,
        },
        "gpu": {
            "t": pick(e, "GPU temperature"), "u": pick(e, "GPU usage"),
            "fb": pick(e, "FB usage"), "vid": pick(e, "VID usage"), "bus": pick(e, "BUS usage"),
            "mem": pick(e, "Memory usage"),
            "cf": pick(e, "Core clock"), "mf": pick(e, "Memory clock"),
            "p": pick(e, "Power"), "fan": pick(e, "Fan speed"),
            "rpm": pick(e, "Fan tachometer"), "rpm2": pick(e, "Fan tachometer 2"),
            "lt": pick(e, "Temp limit"), "lp": pick(e, "Power limit"), "lv": pick(e, "Voltage limit"),
        },
        "fps": {
            "now": pick(e, "Framerate"), "l1": pick(e, "Framerate 1% Low"),
            "l01": pick(e, "Framerate 0.1% Low"), "ft": pick(e, "Frametime"),
            "min": pick(e, "Framerate Min"), "avg": pick(e, "Framerate Avg"), "max": pick(e, "Framerate Max"),
        },
        "ram": pick(e, "RAM usage"),       # 已用記憶體 MB
        "ping": pick(e, "Ping"),
        "dsk": disks,
    }


FC_DIR = r"H:\FanControl"            # FanControl 安裝資料夾（內含 FanControl.IPC.dll 等）
_fc = {"data": None}                  # 背景執行緒寫入、主迴圈讀取的最新 FanControl 資料
# 要顯示的溫度（名稱 → 顯示用中文標籤）；其餘（SSD 的警告值、DIMM 規格值等）略過
FC_TEMPS = [
    ("CPU Package", "CPU 封裝"), ("Core Max", "CPU 最高核心"), ("CPU", "主機板 CPU"), ("CPU Socket", "CPU 插槽"),
    ("VRM MOS", "VRM"), ("PCH", "PCH 晶片組"), ("System", "機殼內"),
    ("GPU", "GPU 核心"), ("NVIDIA GeForce RTX 3090 - Hot Spot", "GPU 熱點"),
    ("NVIDIA GeForce RTX 3090 - Memory Junction", "GPU 顯存"),
    ("Liquid - Kraken 2024 Plus", "水冷液溫"),
    ("DIMM #1", "記憶體 1"), ("DIMM #3", "記憶體 3"), ("M2 #1", "M.2 #1"),
]


def fc_thread():
    """背景執行緒：每秒透過 FanControl 的 gRPC（命名管道）讀取全部感測器。
    FanControl 沒開或服務未啟動時就清空資料，等下一輪再試。需要 pythonnet（.NET Framework 模式）。"""
    try:
        import pythonnet
        pythonnet.load("netfx")
        sys.path.append(FC_DIR)
        import clr
        import System
        for n in ("Google.Protobuf", "Grpc.Core.Api", "GrpcDotNetNamedPipes", "FanControl.IPC"):
            clr.AddReference(FC_DIR + "\\" + n + ".dll")
        from FanControl.IPC import IPCFactory
        asm = System.Type.GetType("FanControl.IPC.IPCFactory, FanControl.IPC").Assembly
        req_type = asm.GetType("GetAllSensorsRequest")
    except Exception:
        return                         # 沒有 pythonnet 或 FanControl：整個功能略過
    client = None
    while True:
        try:
            if client is None:
                client = IPCFactory.GetSensorClient()
            rep = client.GetAllSensors(System.Activator.CreateInstance(req_type))
            rpm, ctl, temp = {}, {}, {}
            kr = {}
            for x in rep.Sensors:
                t, name, val = str(x.Type), str(x.Name), (float(x.Value) if x.HasValue else None)
                ident = str(x.Identifier)
                if t == "Rpm":
                    rpm[name] = val
                elif t == "Control":
                    ctl[name] = val
                elif t == "Temperature":
                    temp[name] = val
                # 水冷（Kraken 外掛）的液溫／泵／風扇
                if ident.startswith("NzxtKrakenPlugin/liquidtemp"):
                    kr["liq"] = val
                elif ident.startswith("NzxtKrakenPlugin/pumprpm"):
                    kr["pump"] = val
                elif ident.startswith("NzxtKrakenPlugin/fanrpm"):
                    kr["fan"] = val
            fans = [[n, v, ctl.get(n)] for n, v in rpm.items()]            # [名稱, 轉速, 控制輸出%]
            temps = [[lab, temp[n]] for n, lab in FC_TEMPS if n in temp]
            mix = [[n, temp[n]] for n in ("Mix", "Mix 1", "Offset", "Offset 1") if n in temp]
            _fc["data"] = {"fans": fans, "temps": temps, "mix": mix, "kr": kr or None}
        except Exception:
            client = None
            _fc["data"] = None
        time.sleep(1.0)


def read_kraken():
    """水冷液溫／泵轉速／風扇轉速（來自 FanControl 的 Kraken 外掛）；沒有資料回傳 None。"""
    d = _fc["data"]
    return d["kr"] if d else None


def push(payload):
    """POST 到 PhoneBox；value 用 JSON 字串包起來，手機頁面再解開。"""
    body = json.dumps({"value": json.dumps(payload, separators=(",", ":"))}).encode("utf-8")
    req = urllib.request.Request(PUSH_URL, data=body, headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=3).read()


# ---------------- 歌詞（Apple Music 回報的歌手欄位是「歌手 — 專輯」，原生歌詞查不到，改由這裡清理後去 LRCLIB 查） ----------------
STATE_URL = "http://127.0.0.1:8765/api/state"
LYR_URL = "http://127.0.0.1:8765/api/push/lyr?ttl=86400"
_lyr = {"key": None, "payload": None, "pushed": 0.0}


def _get_json(url, timeout=8):
    """GET 並解析 JSON；失敗回傳 None。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PhoneBoxLyrics/1.0"})
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))
    except Exception:
        return None


def clean_artist(raw):
    """「Ado — Ibara - Single」→「Ado」；多位歌手只取第一位。"""
    a = (raw or "").split(" — ")[0].split(" - ")[0]
    for sep in (" & ", ", ", " feat. ", " ft. ", "、"):
        a = a.split(sep)[0]
    return a.strip()


def parse_lrc(text):
    """把 LRC 文字轉成 [[毫秒, 歌詞], ...]。"""
    import re
    out = []
    for line in text.splitlines():
        m = re.match(r"^\[(\d+):(\d+(?:\.\d+)?)\](.*)$", line)
        if m:
            out.append([int((int(m.group(1)) * 60 + float(m.group(2))) * 1000), m.group(3).strip()])
    return out


def clean_title(t):
    """去掉標題裡的括號註記與版本字樣：「Song (feat. X) - Remastered 2011」→「Song」。"""
    import re
    t = re.sub(r"\s*[\(\[（【].*?[\)\]）】]", "", t or "")
    t = re.sub(r"\s+-\s+.*$", "", t)
    return t.strip() or (t or "")


def _lrclib(title, artist, dur_s):
    """LRCLIB：精準查詢（含長度）→ 精準查詢 → 搜尋；回傳 (同步歌詞, 純文字)。"""
    import urllib.parse as up
    base = "https://lrclib.net/api/"
    q = {"track_name": title, "artist_name": artist}
    cands = []
    if dur_s:
        cands.append(base + "get?" + up.urlencode({**q, "duration": int(dur_s)}))
    cands.append(base + "get?" + up.urlencode(q))
    for u in cands:
        r = _get_json(u)
        if r and (r.get("syncedLyrics") or r.get("plainLyrics")):
            return parse_lrc(r.get("syncedLyrics") or ""), r.get("plainLyrics") or ""
    res = _get_json(base + "search?" + up.urlencode(q)) or []
    res.sort(key=lambda x: (not x.get("syncedLyrics"), abs((x.get("duration") or 0) - (dur_s or 0))))
    if res:
        r = res[0]
        return parse_lrc(r.get("syncedLyrics") or ""), r.get("plainLyrics") or ""
    return [], ""


def _title_sim(a, b):
    """兩個標題的字元重疊比例（去掉標點空白後）；繁簡字不同的字會少算一點，所以門檻不要設太高。"""
    import re
    norm = lambda s: re.sub(r"[\W_]+", "", (s or "").lower())
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    common = sum(1 for ch in set(a) if ch in b)
    return common / float(max(len(set(a)), len(set(b))))


def _netease(title, artist, dur_s):
    """網易雲音樂（免帳號的公開介面）：搜尋 → 依「標題相似、歌手相符或排名第一、長度接近」逐筆檢查 → 取第一筆有時間軸歌詞的。
    AM 是繁體、網易雲是簡體，所以歌手比不到時改看搜尋排名；但標題一定要夠像，避免配到長度相近的別首歌。"""
    import urllib.parse as up
    hdr = {"User-Agent": "Mozilla/5.0", "Referer": "https://music.163.com"}

    def get(u):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=hdr), timeout=8).read().decode("utf-8"))
        except Exception:
            return None
    s = get("https://music.163.com/api/search/get?" + up.urlencode({"s": (title + " " + artist).strip(), "type": 1, "limit": 8}))
    songs = ((s or {}).get("result") or {}).get("songs") or []
    skip = ("作词", "作曲", "编曲", "作詞", "編曲", "制作", "製作", "混音", "母带", "录音", "監製", "监制")
    for rank, x in enumerate(songs):
        names = " ".join(a2.get("name", "") for a2 in x.get("artists", [])).lower()
        art_ok = bool(artist) and artist.lower() in names
        dd = abs((x.get("duration") or 0) / 1000.0 - (dur_s or 0)) if dur_s else 0
        if _title_sim(title, x.get("name", "")) < 0.6:
            continue
        if not ((art_ok and dd <= 6) or (rank <= 2 and dd <= 3)):
            continue
        ly = get("https://music.163.com/api/song/lyric?id=%s&lv=1&kv=1&tv=-1" % x["id"]) or {}
        lines = parse_lrc(((ly.get("lrc") or {}).get("lyric")) or "")
        lines = [l for l in lines if not any(k in l[1][:6] for k in skip)]
        if lines:
            return lines, ""
    return [], ""


def find_lyrics(title, artist, dur_s):
    """依序嘗試多個來源與標題寫法，直到找到有「時間軸」的歌詞；只有純文字時保留當備案。
    LRCLIB 先用原標題（它要精確比對）；網易雲先用清理後的標題（括號註記會讓搜尋排名亂掉）。"""
    plain_fallback = ""
    clean = clean_title(title)
    plans = [(_lrclib, [title, clean]), (_netease, [clean, title])]
    for src, titles in plans:
        seen = set()
        for t in titles:
            if t in seen:
                continue
            seen.add(t)
            try:
                lines, plain = src(t, artist, dur_s)
            except Exception:
                continue
            if lines:
                return lines, plain
            plain_fallback = plain_fallback or plain
    return [], plain_fallback



# ---------------- 專輯封面：縮圖 + 主色盤（給「播放中」頁做動態背景、頻譜配色） ----------------
ART_URL = "http://127.0.0.1:8765/api/art?id="
ART_PUSH = "http://127.0.0.1:8765/api/push/art?ttl=86400"
SETTINGS_URL = "http://127.0.0.1:8765/api/settings"
_art = {"id": None, "payload": None, "pushed": 0.0}


def art_payload(art_id):
    """抓封面 → 縮成 240px JPEG（data URI）+ 抽出 4 個鮮豔主色；失敗回傳 None。"""
    import base64, colorsys, io
    from PIL import Image
    raw = urllib.request.urlopen(urllib.request.Request(ART_URL + str(art_id), headers={"User-Agent": "PhoneBoxArt/1.0"}), timeout=8).read()
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    thumb = im.resize((240, 240))
    buf = io.BytesIO()
    thumb.save(buf, "JPEG", quality=82)
    # 主色：量化成 8 色，依「飽和度 × 占比」排序，排除近白／近黑
    q = im.resize((80, 80)).quantize(8, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[:24]
    counts = sorted(q.getcolors(), reverse=True)  # [(數量, 索引)]
    total = float(sum(c for c, _ in counts)) or 1.0
    cands = []
    for c, idx in counts:
        r, g, b = pal[idx * 3: idx * 3 + 3]
        h, sat, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        if v < 0.18:
            continue
        score = (sat * 0.8 + 0.2) * (c / total) ** 0.5 * (0.6 + 0.4 * v)
        cands.append((score, (r, g, b)))
    cands.sort(reverse=True)
    cols = ["#%02x%02x%02x" % c for _, c in cands[:4]]
    while len(cols) < 4:
        cols.append(cols[-1] if cols else "#5b6b8c")
    return {"id": art_id, "img": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), "pal": cols}


def recolor_spectrum(color):
    """把頻譜元件的顏色改成封面主色（讀設定 → 改 → 用版本號寫回；失敗就算了）。"""
    try:
        req = urllib.request.Request(SETTINGS_URL, headers={"User-Agent": "PhoneBoxArt/1.0"})
        r = urllib.request.urlopen(req, timeout=5)
        ver = r.headers.get("X-Config-Version")
        cfg = json.loads(r.read().decode("utf-8"))
        changed = False
        for pg in cfg.get("pages", []):
            for w in pg.get("widgets", []):
                if w.get("type") == "spectrum":
                    o = w.setdefault("options", {})
                    if o.get("color") != color:
                        o["color"] = color
                        changed = True
        if changed:
            body = json.dumps(cfg, ensure_ascii=False).encode("utf-8")
            urllib.request.urlopen(urllib.request.Request(SETTINGS_URL, data=body, method="PUT", headers={"Content-Type": "application/json; charset=utf-8", "X-Config-Version": ver}), timeout=8).read()
    except Exception:
        pass


def lyric_thread():
    """每 2 秒看一次目前播放的歌；換歌就重新查歌詞，推送 key=lyr（手機的歌詞頁讀它）。"""
    while True:
        try:
            st = _get_json(STATE_URL, 3) or {}
            m = st.get("media") or {}
            title, art = m.get("title"), m.get("artist")
            key = (title, art)
            if title and key != _lyr["key"]:
                artist = clean_artist(art)
                dur = (m.get("durMs") or 0) / 1000.0
                lines, plain = find_lyrics(title, artist, dur)
                _lyr["key"] = key
                _lyr["payload"] = {"title": title, "artist": artist, "lines": lines, "plain": plain[:3000]}
                _lyr["pushed"] = 0.0
            # 封面與色盤：封面換了就重抓、重推，並把頻譜改成封面主色
            aid = m.get("artId")
            if aid and aid != _art["id"]:
                try:
                    _art["payload"] = art_payload(aid)
                    _art["id"] = aid
                    _art["pushed"] = 0.0
                except Exception:
                    _art["id"] = aid
                    _art["payload"] = None
            if _art["payload"] and time.time() - _art["pushed"] > 30:
                body = json.dumps({"value": json.dumps(_art["payload"], separators=(",", ":"))}).encode("utf-8")
                urllib.request.urlopen(urllib.request.Request(ART_PUSH, data=body, headers={"Content-Type": "application/json"}, method="POST"), timeout=3).read()
                _art["pushed"] = time.time()
            # 每 30 秒重送一次，讓剛連上的手機也拿得到
            if _lyr["payload"] and time.time() - _lyr["pushed"] > 30:
                body = json.dumps({"value": json.dumps(_lyr["payload"], separators=(",", ":"))}).encode("utf-8")
                urllib.request.urlopen(urllib.request.Request(LYR_URL, data=body, headers={"Content-Type": "application/json"}, method="POST"), timeout=3).read()
                _lyr["pushed"] = time.time()
        except Exception:
            pass
        time.sleep(2)


def main():
    _lock = single_instance()
    import threading
    threading.Thread(target=fc_thread, daemon=True).start()
    threading.Thread(target=lyric_thread, daemon=True).start()
    mm = None
    last_open = 0.0
    while True:
        started = time.time()
        try:
            # 共享記憶體不存在（Afterburner 沒開）或被重建時，每 5 秒重試一次
            if mm is None and started - last_open > 5:
                last_open = started
                mm = open_mahm()
            if mm is not None:
                payload = build_payload(read_entries(mm))
                payload["kr"] = read_kraken()      # 水冷資料（可能為 None）
                payload["fc"] = _fc["data"]        # FanControl 風扇／溫度（可能為 None）
                push(payload)
                if VERBOSE:
                    print(time.strftime("%H:%M:%S"), "CPU", payload["cpu"]["u"], payload["cpu"]["t"],
                          "GPU", payload["gpu"]["u"], payload["gpu"]["t"], "FPS", payload["fps"]["now"])
        except Exception as ex:  # 任何錯誤都不中斷：關掉對應物件，下一輪重來
            if VERBOSE:
                print("錯誤:", ex)
            try:
                if mm is not None:
                    mm.close()
            except Exception:
                pass
            mm = None
        time.sleep(max(0.05, INTERVAL - (time.time() - started)))


if __name__ == "__main__":
    main()
