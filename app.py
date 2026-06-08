import os
import requests
import re
import logging
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from youtube_transcript_api import YouTubeTranscriptApi

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)
logging.basicConfig(level=logging.INFO)

# 从环境变量读取API Keys作为后备
ENV_GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
ENV_DEEPL_KEY = os.environ.get("DEEPL_API_KEY", "")

# 语言配置
LANG_CONFIG = {
    "zh-TW": {"google": "zh-TW", "deepl": "ZH-HANT", "name": "繁體中文"},
    "zh-CN": {"google": "zh-CN", "deepl": "ZH", "name": "簡體中文"},
    "en": {"google": "en", "deepl": "EN", "name": "英文"},
    "ja": {"google": "ja", "deepl": "JA", "name": "日文"},
    "ko": {"google": "ko", "deepl": "KO", "name": "韓文"},
    "fr": {"google": "fr", "deepl": "FR", "name": "法文"},
    "de": {"google": "de", "deepl": "DE", "name": "德文"}
}

# ---------- 翻译引擎函数 ----------
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
    # 获取可用模型列表
    list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        list_resp = requests.get(list_url, timeout=10)
        list_resp.raise_for_status()
        models_data = list_resp.json()
        available_models = [model['name'].replace('models/', '') for model in models_data.get('models', [])]
    except Exception as e:
        raise Exception(f"无法获取Gemini模型列表: {e}")
    selected_model = None
    for model_name in available_models:
        if 'gemini' in model_name:
            selected_model = model_name
            break
    if not selected_model:
        raise Exception("没有可用的Gemini模型")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{selected_model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2000}
    }
    resp = requests.post(url, json=payload, timeout=30)
    if resp.status_code != 200:
        raise Exception(f"Gemini API 错误 {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    result = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text")
    if not result:
        raise Exception("无法解析Gemini响应")
    return result.strip()

# ---------- YouTube 字幕处理 ----------
def extract_video_id(url):
    patterns = [
        r'(?:youtube\.com\/watch\?v=)([\w-]+)',
        r'(?:youtu\.be\/)([\w-]+)',
        r'(?:youtube\.com\/embed\/)([\w-]+)',
        r'(?:youtube\.com\/v\/)([\w-]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def translate_subtitle_text(text, src_lang, tgt_lang, gemini_key, deepl_key):
    # 优先 Gemini
    if gemini_key:
        try:
            return translate_gemini(text, src_lang, tgt_lang, gemini_key)
        except Exception as e:
            app.logger.warning(f"字幕 Gemini 失败: {e}")
    # 日韩尝试 DeepL
    if tgt_lang in ["ja", "ko"] and deepl_key:
        try:
            return translate_deepl(text, src_lang, tgt_lang, deepl_key)
        except Exception as e:
            app.logger.warning(f"字幕 DeepL 失败: {e}")
    # 降级 Google
    return translate_google(text, src_lang, tgt_lang)

# ---------- API 路由 ----------
@app.route('/translate', methods=['POST'])
def translate():
    data = request.get_json()
    text = data.get('text', '').strip()
    src = data.get('source_lang', 'zh-TW')
    tgt = data.get('target_lang', 'en')
    gemini_key = data.get('gemini_key', '') or ENV_GEMINI_KEY
    deepl_key = data.get('deepl_key', '') or ENV_DEEPL_KEY
    if not text:
        return jsonify({"error": "請輸入文字"}), 400
    if src == tgt:
        return jsonify({"result": text, "engine": "相同語言"})
    # 尝试 Gemini
    if gemini_key:
        try:
            result = translate_gemini(text, src, tgt, gemini_key)
            return jsonify({"result": result, "engine": "Gemini AI"})
        except Exception as e:
            app.logger.error(f"Gemini 失败: {e}")
    # 后备策略
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

@app.route('/process_youtube', methods=['POST'])
def process_youtube():
    data = request.get_json()
    video_url = data.get('url', '').strip()
    gemini_key = data.get('gemini_key', '') or ENV_GEMINI_KEY
    deepl_key = data.get('deepl_key', '') or ENV_DEEPL_KEY
    target_lang = data.get('target_lang', 'zh-TW')
    if not video_url:
        return jsonify({"error": "請提供 YouTube 網址"}), 400
    video_id = extract_video_id(video_url)
    if not video_id:
        return jsonify({"error": "無效的 YouTube 網址"}), 400
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        try:
            transcript = transcript_list.find_transcript(['en'])
        except:
            transcript = list(transcript_list)[0]
        original_entries = transcript.fetch()
        if not original_entries:
            return jsonify({"error": "未偵測到任何字幕內容"}), 400
        src_lang_code = transcript.language_code
        # 映射语言代码到我们的系统（例如 'en' -> 'en', 'ja' -> 'ja', 'zh-Hant' -> 'zh-TW'）
        # 简单处理：如果代码以 'zh' 开头，设为 'zh-TW'；否则直接用前两个字符
        if src_lang_code.startswith('zh'):
            src_lang = 'zh-TW'
        else:
            src_lang = src_lang_code[:2] if len(src_lang_code) >= 2 else 'en'
        translated_entries = []
        for entry in original_entries:
            original_text = entry['text'].strip()
            if not original_text:
                translated_text = ""
            else:
                try:
                    translated_text = translate_subtitle_text(original_text, src_lang, target_lang, gemini_key, deepl_key)
                except Exception as e:
                    app.logger.error(f"翻譯單條字幕失敗: {e}")
                    translated_text = f"[翻譯失敗] {original_text}"
            translated_entries.append({
                "start": entry['start'],
                "duration": entry['duration'],
                "original": original_text,
                "translated": translated_text
            })
        return jsonify({
            "subtitles": translated_entries,
            "video_id": video_id,
            "source_lang": src_lang_code,
            "target_lang": target_lang
        })
    except Exception as e:
        app.logger.error(f"YouTube 處理失敗: {e}")
        if "No transcripts were found" in str(e):
            return jsonify({"error": "此影片沒有任何字幕（CC 字幕）"}), 400
        return jsonify({"error": f"獲取字幕失敗: {str(e)}"}), 500

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
