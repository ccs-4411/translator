import os
import re
import requests
import logging
import json
import traceback
import time
import unicodedata
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# 嘗試導入 googletrans（若未安裝則略過）
try:
    from googletrans import Translator
    HAS_GOOGLETRANS = True
except ImportError:
    HAS_GOOGLETRANS = False
    logging.warning("googletrans 未安裝，Google 翻譯備援將僅限 requests 模式。")

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

ENV_GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
ENV_DEEPL_KEY = os.environ.get("DEEPL_API_KEY", "")
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

# =========================================================
# 語系對照表
# =========================================================
LANG_CONFIG = {
    "auto": {"google": "auto", "deepl": "auto", "mymemory": "auto", "name": "自動"},
    "zh-TW": {"google": "zh-TW", "deepl": "ZH-HANT", "mymemory": "zh-TW", "name": "繁體中文"},
    "zh-CN": {"google": "zh-CN", "deepl": "ZH", "mymemory": "zh-CN", "name": "簡體中文"},
    "en": {"google": "en", "deepl": "EN", "mymemory": "en", "name": "英文"},
    "ja": {"google": "ja", "deepl": "JA", "mymemory": "ja", "name": "日文"},
    "ko": {"google": "ko", "deepl": "KO", "mymemory": "ko", "name": "韓文"},
    "fr": {"google": "fr", "deepl": "FR", "mymemory": "fr", "name": "法文"},
    "de": {"google": "de", "deepl": "DE", "mymemory": "de", "name": "德文"},
    "es": {"google": "es", "deepl": "ES", "mymemory": "es", "name": "西班牙文"}
}

# =========================================================
# 領域設定
# =========================================================
DOMAIN_CONFIG = {
    "general": {"role": "翻譯專家", "rules": ["保持自然流暢"]},
    "baseball": {"role": "棒球轉播翻譯", "rules": ["遵守術語對照"]},
    "basketball": {"role": "籃球評述", "rules": ["使用籃球術語"]},
    "gaming": {"role": "遊戲在地化", "rules": ["使用玩家慣用詞"]}
}

def generate_dynamic_prompt(domain, src_name, tgt_name):
    cfg = DOMAIN_CONFIG.get(domain, DOMAIN_CONFIG["general"])
    return f"角色：{cfg['role']}\n任務：將{src_name}譯為{tgt_name}\n規則：{'；'.join(cfg['rules'])}\n只回傳翻譯結果，不加任何解釋。"

def build_batch_instruction(domain, src_name, tgt_name):
    return (generate_dynamic_prompt(domain, src_name, tgt_name) +
            "\n請將輸入的JSON陣列中每個text翻譯，回傳相同結構的JSON陣列，只輸出JSON。")

# =========================================================
# 輔助函數（SRT 解析、文字清理、分塊等）
# =========================================================
def parse_srt(srt):
    srt = srt.replace('\r\n', '\n').replace('\r', '\n')
    pattern = r'(\d+)\n([0-9:, \t\-衰>]+)\n(.*?)(?=\n\s*\n|\n\d+\n[0-9:, \t\-衰>]+|\Z)'
    subs = []
    for m in re.finditer(pattern, srt, re.DOTALL):
        sid = m.group(1).strip()
        ts = m.group(2).strip()
        if '衰>' in ts:
            ts = ts.replace('衰>', '-->')
        elif '-->' not in ts:
            continue
        subs.append({"id": sid, "time": ts, "text": m.group(3).strip()})
    if not subs:
        for block in re.split(r'\n\s*\n', srt.strip()):
            lines = [l.strip() for l in block.split('\n') if l.strip()]
            if len(lines) >= 2 and '-->' in lines[1]:
                subs.append({"id": lines[0], "time": lines[1], "text": "\n".join(lines[2:])})
    return subs

def normalize_text(text):
    if not text:
        return ""
    return "\n".join([line.strip() for line in str(text).replace('\r\n', '\n').replace('\r', '\n').split('\n') if line.strip()]).strip()

def chunk_subtitles(subs, domain="general"):
    max_items = 8 if domain in ["baseball", "basketball"] else 12
    max_chars = 2500
    chunks, cur, chars = [], [], 0
    for sub in subs:
        t = normalize_text(sub.get("text", ""))
        if cur and (len(cur) >= max_items or chars + len(t) > max_chars):
            chunks.append(cur)
            cur = []
            chars = 0
        cur.append(sub)
        chars += len(t)
    if cur:
        chunks.append(cur)
    return chunks

# =========================================================
# 增強版「未翻譯檢測」
# =========================================================
def looks_untranslated(src, trans, tgt_lang):
    """
    判斷翻譯結果是否仍為原文或僅部分翻譯。
    針對目標為中文時，檢查中文字元比例。
    """
    if not trans or trans.strip() == src.strip():
        return True
    if tgt_lang in ["zh-TW", "zh-CN"]:
        total = len(trans)
        if total == 0:
            return True
        # 計算中文字元（CJK Unified Ideographs）數量
        cjk_count = sum(1 for c in trans if '\u4e00' <= c <= '\u9fff')
        # 若中文字元佔比低於 30%，視為未翻譯（可能仍是原文或夾雜大量外文）
        if cjk_count / total < 0.3:
            return True
    return False

# =========================================================
# 翻譯引擎（三層備援）
# =========================================================

# ----- 第一層：Gemini（含重試機制）-----
def translate_gemini(text, src, tgt, api_key, domain="general", retry=1):
    try:
        if not api_key:
            return ""
        src_name = LANG_CONFIG.get(src, {}).get("name", src)
        tgt_name = LANG_CONFIG.get(tgt, {}).get("name", tgt)
        prompt = generate_dynamic_prompt(domain, src_name, tgt_name)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{DEFAULT_GEMINI_MODEL}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": text}]}],
            "system_instruction": {"parts": [{"text": prompt}]},
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1500}
        }
        for attempt in range(retry + 1):
            try:
                resp = requests.post(url, json=payload, timeout=25)
                if resp.status_code == 200:
                    return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                elif resp.status_code == 503 and attempt < retry:
                    app.logger.warning(f"Gemini 503，等待 2 秒後重試 (嘗試 {attempt+1}/{retry})")
                    time.sleep(2)
                    continue
                else:
                    app.logger.warning(f"Gemini 狀態 {resp.status_code}")
                    break
            except Exception as e:
                app.logger.warning(f"Gemini 請求異常: {e}")
                if attempt < retry:
                    time.sleep(1)
                    continue
                break
    except Exception as e:
        app.logger.warning(f"Gemini 單句異常: {e}")
    return ""

def translate_gemini_batch(subs, src, tgt, api_key, domain="general", retry=1):
    try:
        if not api_key or not subs:
            return []
        src_name = LANG_CONFIG.get(src, {}).get("name", src)
        tgt_name = LANG_CONFIG.get(tgt, {}).get("name", tgt)
        instruction = build_batch_instruction(domain, src_name, tgt_name)
        input_data = [{"id": str(s.get("id")), "text": normalize_text(s.get("text", ""))} for s in subs]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{DEFAULT_GEMINI_MODEL}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": json.dumps(input_data, ensure_ascii=False)}]}],
            "system_instruction": {"parts": [{"text": instruction}]},
            "generationConfig": {"temperature": 0.15, "responseMimeType": "application/json", "maxOutputTokens": 3500}
        }
        for attempt in range(retry + 1):
            try:
                resp = requests.post(url, json=payload, timeout=35)
                if resp.status_code == 200:
                    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                    if "```" in raw:
                        raw = re.sub(r'^```json\s*|^```\s*|\s*```$', '', raw, flags=re.IGNORECASE).strip()
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return parsed
                    else:
                        return []
                elif resp.status_code == 503 and attempt < retry:
                    app.logger.warning(f"Gemini 批次 503，等待 2 秒後重試 (嘗試 {attempt+1}/{retry})")
                    time.sleep(2)
                    continue
                else:
                    app.logger.warning(f"Gemini 批次狀態 {resp.status_code}")
                    break
            except Exception as e:
                app.logger.warning(f"Gemini 批次請求異常: {e}")
                if attempt < retry:
                    time.sleep(1)
                    continue
                break
    except Exception as e:
        app.logger.warning(f"Gemini 批次異常: {e}")
    return []

# ----- 第二層：DeepL（優先）-----
def translate_deepl(text, src, tgt, api_key):
    try:
        if not api_key:
            return ""
        tgt_code = LANG_CONFIG[tgt]["deepl"]
        src_code = LANG_CONFIG[src]["deepl"]
        params = {"text": text, "target_lang": tgt_code}
        if src_code and src_code != "auto":
            params["source_lang"] = src_code
        headers = {"Authorization": f"DeepL-Auth-Key {api_key}"}
        resp = requests.post("https://api-free.deepl.com/v2/translate", data=params, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json()["translations"][0]["text"]
    except Exception as e:
        app.logger.warning(f"DeepL 異常: {e}")
    return ""

# ----- 第三層：Google 翻譯（requests + googletrans 雙模式）-----
def translate_google(text, src, tgt):
    result = ""
    # 模式一：requests 直連
    try:
        src_code = LANG_CONFIG.get(src, {}).get("google", "auto")
        tgt_code = LANG_CONFIG.get(tgt, {}).get("google", "en")
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "webapp", "sl": src_code, "tl": tgt_code, "dt": "t", "q": text}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            # 解析多種可能結構
            if isinstance(data, list):
                if len(data) > 0 and isinstance(data[0], list):
                    parts = []
                    for seg in data[0]:
                        if isinstance(seg, list) and len(seg) > 0 and seg[0] is not None:
                            parts.append(str(seg[0]))
                        elif isinstance(seg, str):
                            parts.append(seg)
                    if parts:
                        result = "".join(parts)
                elif all(isinstance(item, str) for item in data):
                    result = "".join(data)
            elif isinstance(data, dict):
                result = data.get("translatedText", "")
            if result:
                app.logger.info(f"Google (requests) 翻譯成功: {result[:30]}...")
                return result
    except Exception as e:
        app.logger.warning(f"Google (requests) 異常: {e}")

    # 模式二：googletrans 備援
    if HAS_GOOGLETRANS and not result:
        try:
            translator = Translator()
            trans = translator.translate(text, src=src_code, dest=tgt_code)
            if trans and trans.text:
                result = trans.text
                app.logger.info(f"Google (googletrans) 翻譯成功: {result[:30]}...")
        except Exception as e:
            app.logger.warning(f"Google (googletrans) 異常: {e}")
    return result

# ----- 第四層：MyMemory（免費）-----
def translate_mymemory(text, src, tgt):
    try:
        src_code = LANG_CONFIG.get(src, {}).get("mymemory", "auto")
        tgt_code = LANG_CONFIG.get(tgt, {}).get("mymemory", "en")
        url = "https://api.mymemory.translated.net/get"
        params = {"q": text, "langpair": f"{src_code}|{tgt_code}"}
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("responseStatus") == 200:
                trans = data.get("responseData", {}).get("translatedText", "")
                if trans:
                    app.logger.info(f"MyMemory 翻譯成功: {trans[:30]}...")
                    return trans
    except Exception as e:
        app.logger.warning(f"MyMemory 異常: {e}")
    return ""

# ----- 安全單句翻譯（依序嘗試所有引擎）-----
def safe_single_translate(text, src, tgt, domain, gemini_key, deepl_key, allow_gemini=False):
    text = normalize_text(text)
    if not text:
        return ""
    # 1. Gemini（若允許）
    if allow_gemini and gemini_key:
        r = translate_gemini(text, src, tgt, gemini_key, domain, retry=1)
        if r and not looks_untranslated(text, r, tgt):
            return normalize_text(r)
    # 2. DeepL（優先於 Google，若提供金鑰）
    if deepl_key:
        r = translate_deepl(text, src, tgt, deepl_key)
        if r and not looks_untranslated(text, r, tgt):
            return normalize_text(r)
    # 3. Google
    r = translate_google(text, src, tgt)
    if r and not looks_untranslated(text, r, tgt):
        return normalize_text(r)
    # 4. MyMemory
    r = translate_mymemory(text, src, tgt)
    if r and not looks_untranslated(text, r, tgt):
        return normalize_text(r)
    # 全部失敗或未翻譯，回傳原文
    app.logger.warning(f"所有引擎失敗或未翻譯，保留原文: {text[:50]}...")
    return text

# =========================================================
# Flask 路由
# =========================================================
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/test_translate', methods=['GET'])
def test_translate():
    test = "Hello, how are you?"
    google = translate_google(test, "en", "zh-TW")
    mymemory = translate_mymemory(test, "en", "zh-TW")
    return jsonify({
        "google": google,
        "mymemory": mymemory,
        "has_googletrans": HAS_GOOGLETRANS
    })

@app.route('/translate', methods=['POST'])
def translate_text():
    try:
        data = request.get_json() or {}
        text = normalize_text(data.get("text", ""))
        src = data.get("source_lang", "auto")
        tgt = data.get("target_lang", "zh-TW")
        domain = data.get("domain", "general")
        gemini_key = data.get("gemini_key") or ENV_GEMINI_KEY
        deepl_key = data.get("deepl_key") or ENV_DEEPL_KEY
        if not text:
            return jsonify({"result": ""}), 200
        # 嘗試 Gemini
        if gemini_key:
            r = translate_gemini(text, src, tgt, gemini_key, domain, retry=1)
            if r and not looks_untranslated(text, r, tgt):
                return jsonify({"result": r, "engine": "Gemini"})
        # 備援
        r = safe_single_translate(text, src, tgt, domain, gemini_key, deepl_key, allow_gemini=False)
        return jsonify({"result": r, "engine": "備援"})
    except Exception as e:
        app.logger.error(traceback.format_exc())
        return jsonify({"result": "", "engine": "Error"}), 200

@app.route('/translate_srt', methods=['POST'])
def translate_srt():
    try:
        data = request.get_json() or {}
        srt_content = data.get("srt_content", "").strip()
        src = data.get("source_lang", "auto")
        tgt = data.get("target_lang", "zh-TW")
        domain = data.get("domain", "general")
        layout = data.get("layout_mode", "original_first")
        gemini_key = data.get("gemini_key") or ENV_GEMINI_KEY
        deepl_key = data.get("deepl_key") or ENV_DEEPL_KEY
        if not srt_content:
            return jsonify({"srt_output": "", "error": "無內容"}), 200
        subs = parse_srt(srt_content)
        if not subs:
            return jsonify({"srt_output": srt_content, "error": "解析失敗"}), 200

        trans_map = {}
        fallback = False
        gemini_ok = bool(gemini_key)
        chunks = chunk_subtitles(subs, domain)

        for idx, chunk in enumerate(chunks, 1):
            batch_done = False
            if gemini_ok:
                batch_res = translate_gemini_batch(chunk, src, tgt, gemini_key, domain, retry=1)
                if batch_res:
                    try:
                        expected = {str(x["id"]) for x in chunk}
                        got = {str(item.get("id")) for item in batch_res}
                        if expected == got:
                            for sub in chunk:
                                sid = str(sub["id"])
                                src_text = normalize_text(sub["text"])
                                trans_text = ""
                                for item in batch_res:
                                    if str(item.get("id")) == sid:
                                        trans_text = normalize_text(item.get("text", ""))
                                        break
                                if looks_untranslated(src_text, trans_text, tgt):
                                    trans_text = safe_single_translate(src_text, src, tgt, domain, gemini_key, deepl_key, allow_gemini=False)
                                trans_map[sid] = trans_text
                            batch_done = True
                        else:
                            gemini_ok = False
                            fallback = True
                    except Exception:
                        gemini_ok = False
                        fallback = True
                else:
                    gemini_ok = False
                    fallback = True

            if not batch_done:
                fallback = True
                for sub in chunk:
                    sid = str(sub["id"])
                    src_text = normalize_text(sub["text"])
                    trans_map[sid] = safe_single_translate(src_text, src, tgt, domain, gemini_key, deepl_key, allow_gemini=False)

        # 組裝 SRT
        out = []
        for sub in subs:
            sid, ts = str(sub["id"]), sub["time"]
            orig = normalize_text(sub["text"])
            trans = normalize_text(trans_map.get(sid, ""))
            block = [sid, ts]
            if layout == "original_first":
                block.append(orig)
                if trans:
                    block.append(trans)
            elif layout == "translated_first":
                if trans:
                    block.append(trans)
                block.append(orig)
            elif layout == "translated_only":
                block.append(trans if trans else orig)
            else:
                block.append(orig)
                if trans:
                    block.append(trans)
            out.append("\n".join(block))
        final_srt = "\n\n".join(out).strip() + "\n"

        engine_label = "Gemini" if not fallback else "備援 (DeepL/Google/MyMemory)"
        return jsonify({
            "srt_output": final_srt,
            "count": len(subs),
            "translated_count": len(trans_map),
            "engine": engine_label,
            "logs": ["備援已啟動" if fallback else "全部 Gemini"]
        }), 200
    except Exception as e:
        app.logger.error(traceback.format_exc())
        return jsonify({"srt_output": srt_content, "engine": "緊急救援"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))


