import os
import re
import requests
import logging
import traceback
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)
logging.basicConfig(level=logging.INFO)

ENV_GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
ENV_DEEPL_KEY = os.environ.get("DEEPL_API_KEY", "")

LANG_CONFIG = {
    "zh-TW": {"google": "zh-TW", "deepl": "ZH-HANT", "name": "繁體中文"},
    "zh-CN": {"google": "zh-CN", "deepl": "ZH", "name": "簡體中文"},
    "en": {"google": "en", "deepl": "EN", "name": "英文"},
    "ja": {"google": "ja", "deepl": "JA", "name": "日文"},
    "ko": {"google": "ko", "deepl": "KO", "name": "韓文"},
    "fr": {"google": "fr", "deepl": "FR", "name": "法文"},
    "de": {"google": "de", "deepl": "DE", "name": "德文"},
    "es": {"google": "es", "deepl": "ES", "name": "西班牙文"}
}

# ---------- 翻译引擎 ----------
def translate_google(text, src, tgt):
    src_code = LANG_CONFIG.get(src, {}).get("google", "auto")
    tgt_code = LANG_CONFIG.get(tgt, {}).get("google", "en")
    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={src_code}&tl={tgt_code}&dt=t&q={requests.utils.quote(text)}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return "".join(part[0] for part in data[0] if part[0]) or text

def translate_deepl(text, src, tgt, api_key):
    if not api_key:
        raise ValueError("DeepL API Key 未設定")
    tgt_code = LANG_CONFIG[tgt]["deepl"]
    src_code = LANG_CONFIG[src]["deepl"]
    if src_code == "ZH-HANT":
        src_code = "ZH"
    params = {"text": text, "target_lang": tgt_code}
    if src_code and src_code != "auto":
        params["source_lang"] = src_code
    headers = {"Authorization": f"DeepL-Auth-Key {api_key}"}
    resp = requests.post("https://api-free.deepl.com/v2/translate", data=params, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.json()["translations"][0]["text"]

def translate_gemini(text, src, tgt, api_key):
    if not api_key:
        raise ValueError("Gemini API Key 未設定")
    src_name = LANG_CONFIG.get(src, {}).get("name", src)
    tgt_name = LANG_CONFIG.get(tgt, {}).get("name", tgt)
    prompt = f"請將以下{src_name}內容翻譯成{tgt_name}，只輸出翻譯結果，不要附加任何說明。\n原文: {text}"
    list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    list_resp = requests.get(list_url, timeout=10)
    list_resp.raise_for_status()
    models_data = list_resp.json()
    available_models = [model['name'].replace('models/', '') for model in models_data.get('models', [])]
    selected_model = None
    for model_name in available_models:
        if 'gemini' in model_name:
            selected_model = model_name
            break
    if not selected_model:
        raise Exception("没有可用的 Gemini 模型")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{selected_model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2000}
    }
    resp = requests.post(url, json=payload, timeout=30)
    if resp.status_code != 200:
        raise Exception(f"Gemini API 错误 {resp.status_code}")
    data = resp.json()
    result = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text")
    if not result:
        raise Exception("无法解析 Gemini 回应")
    return result.strip()

# ---------- SRT 辅助 ----------
def parse_srt(content):
    pattern = r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\d+\n|\n*$)'
    blocks = re.findall(pattern, content, re.DOTALL)
    subtitles = []
    for block in blocks:
        _, start_str, end_str, text = block
        def to_seconds(t):
            h, m, s = t.split(':')
            s, ms = s.split(',')
            return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000
        start = to_seconds(start_str)
        end = to_seconds(end_str)
        text = text.replace('\n', ' ').strip()
        if text:
            subtitles.append({'start': start, 'end': end, 'text': text})
    return subtitles

def format_srt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

# ---------- API 路由 ----------
@app.route("/translate", methods=["POST"])
def translate():
    try:
        data = request.get_json()
        text = data.get("text", "").strip()
        src = data.get("source_lang", "zh-TW")
        tgt = data.get("target_lang", "en")
        gemini_key = data.get("gemini_key", "") or ENV_GEMINI_KEY
        deepl_key = data.get("deepl_key", "") or ENV_DEEPL_KEY

        if not text:
            return jsonify({"error": "請輸入文字"}), 400
        if src == tgt:
            return jsonify({"result": text, "engine": "相同語言"})

        if gemini_key:
            try:
                result = translate_gemini(text, src, tgt, gemini_key)
                return jsonify({"result": result, "engine": "Gemini AI"})
            except Exception as e:
                app.logger.error(f"Gemini 失败: {e}")

        if tgt in ["en", "fr", "de", "es"]:
            result = translate_google(text, src, tgt)
            engine = "Google 翻譯"
        elif tgt in ["ja", "ko"] and deepl_key:
            try:
                result = translate_deepl(text, src, tgt, deepl_key)
                engine = "DeepL 翻譯"
            except:
                result = translate_google(text, src, tgt)
                engine = "Google 翻譯 (DeepL降級)"
        else:
            result = translate_google(text, src, tgt)
            engine = "Google 翻譯"
        return jsonify({"result": result, "engine": engine})
    except Exception as e:
        app.logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@app.route('/translate_srt', methods=['POST'])
def translate_srt():
    try:
        data = request.get_json()
        srt_content = data.get('srt_content', '').strip()
        gemini_key = data.get('gemini_key', '') or ENV_GEMINI_KEY
        deepl_key = data.get('deepl_key', '') or ENV_DEEPL_KEY
        target_lang = data.get('target_lang', 'zh-TW')
        original_first = data.get('original_first', True)
        source_lang = data.get('source_lang', 'auto')

        if not srt_content:
            return jsonify({"error": "请贴上 SRT 内容"}), 400

        subtitles = parse_srt(srt_content)
        if not subtitles:
            return jsonify({"error": "无法解析 SRT 格式，请检查内容"}), 400

        translated_subs = []
        for idx, sub in enumerate(subtitles, 1):
            original = sub['text']
            if not original.strip():
                translated = ""
            else:
                translated = None
                if gemini_key:
                    try:
                        translated = translate_gemini(original, source_lang, target_lang, gemini_key)
                    except Exception as e:
                        app.logger.warning(f"Gemini 第{idx}条失败: {e}")
                if not translated:
                    try:
                        translated = translate_google(original, source_lang, target_lang)
                    except Exception as e:
                        app.logger.error(f"Google 第{idx}条失败: {e}")
                        translated = None
                if not translated or not translated.strip():
                    translated = f"[未翻译] {original}"
            translated_subs.append({
                'start': sub['start'],
                'end': sub['end'],
                'original': original,
                'translated': translated
            })

        srt_output = ""
        for i, sub in enumerate(translated_subs, 1):
            start_str = format_srt_time(sub['start'])
            end_str = format_srt_time(sub['end'])
            if original_first:
                text = f"{sub['original']}\n{sub['translated']}"
            else:
                text = f"{sub['translated']}\n{sub['original']}"
            srt_output += f"{i}\n{start_str} --> {end_str}\n{text}\n\n"

        return jsonify({
            "srt_output": srt_output,
            "count": len(translated_subs)
        })
    except Exception as e:
        app.logger.error(traceback.format_exc())
        return jsonify({"error": f"SRT 处理失败: {str(e)}"}), 500

@app.route("/")
def index():
    return send_from_directory('.', 'index.html')

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
