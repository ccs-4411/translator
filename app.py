import os
import re
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import logging

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
    "de": {"google": "de", "deepl": "DE", "name": "德文"}
}

# ---------- 原有翻译函数（保持不变） ----------
def translate_google(text, src, tgt):
    src_code = LANG_CONFIG.get(src, {}).get("google", "auto")
    tgt_code = LANG_CONFIG.get(tgt, {}).get("google", "en")
    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={src_code}&tl={tgt_code}&dt=t&q={requests.utils.quote(text)}"
    resp = requests.get(url, timeout=10)
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
    resp = requests.post("https://api-free.deepl.com/v2/translate", data=params, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()["translations"][0]["text"]

def translate_gemini(text, src, tgt, api_key):
    if not api_key:
        raise ValueError("Gemini API Key 未設定")
    src_name = LANG_CONFIG[src]["name"]
    tgt_name = LANG_CONFIG[tgt]["name"]
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
        raise Exception("沒有可用的 Gemini 模型")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{selected_model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2000}
    }
    resp = requests.post(url, json=payload, timeout=30)
    if resp.status_code != 200:
        raise Exception(f"Gemini API 錯誤 {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    result = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text")
    if not result:
        raise Exception("無法解析 Gemini 回應")
    return result.strip()

# ---------- SRT 解析与生成辅助函数 ----------
def parse_srt(content):
    """解析 SRT 内容，返回列表 [{'start':秒, 'end':秒, 'text':原文}]"""
    pattern = r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\d+\n|\n*$)'
    blocks = re.findall(pattern, content, re.DOTALL)
    subtitles = []
    for block in blocks:
        idx, start_str, end_str, text = block
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

# ---------- 新增 SRT 翻译路由 ----------
@app.route('/translate_srt', methods=['POST'])
def translate_srt():
    data = request.get_json()
    srt_content = data.get('srt_content', '').strip()
    gemini_key = data.get('gemini_key', '') or ENV_GEMINI_KEY
    deepl_key = data.get('deepl_key', '') or ENV_DEEPL_KEY
    target_lang = data.get('target_lang', 'zh-TW')   # 预设繁体中文

    if not srt_content:
        return jsonify({"error": "请贴上 SRT 内容"}), 400

    # 解析 SRT
    try:
        subtitles = parse_srt(srt_content)
        if not subtitles:
            return jsonify({"error": "无法解析 SRT 格式，请检查内容"}), 400
    except Exception as e:
        return jsonify({"error": f"SRT 解析失败: {str(e)}"}), 400

    # 逐句翻译（原文假设为英文，也可让用户选择源语言，这里简化）
    translated_subs = []
    for sub in subtitles:
        original = sub['text']
        if not original.strip():
            translated = ""
        else:
            # 使用现有的翻译函数，源语言预设为英文 'en'
            try:
                if gemini_key:
                    try:
                        translated = translate_gemini(original, 'en', target_lang, gemini_key)
                    except:
                        translated = translate_google(original, 'en', target_lang)
                else:
                    translated = translate_google(original, 'en', target_lang)
            except Exception as e:
                translated = f"[翻译失败] {original}"
        translated_subs.append({
            'start': sub['start'],
            'end': sub['end'],
            'original': original,
            'translated': translated
        })

    # 生成双语 SRT
    srt_output = ""
    for i, sub in enumerate(translated_subs, 1):
        start_str = format_srt_time(sub['start'])
        end_str = format_srt_time(sub['end'])
        # 双语格式：原文 + 换行 + 翻译
        text = f"{sub['original']}\n{sub['translated']}" if sub['translated'] else sub['original']
        srt_output += f"{i}\n{start_str} --> {end_str}\n{text}\n\n"

    return jsonify({
        "srt_output": srt_output,
        "count": len(translated_subs)
    })

# ---------- 原有路由 ----------
@app.route("/translate", methods=["POST"])
def translate():
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

    try:
        if tgt in ["en", "fr", "de"]:
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
        return jsonify({"error": f"翻譯失敗: {str(e)}"}), 500

@app.route("/")
def index():
    return send_from_directory('.', 'index.html')

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
