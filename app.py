import os
import re
import logging
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import yt_dlp
import webvtt

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)
logging.basicConfig(level=logging.INFO)

# 環境變數 API Keys
ENV_GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
ENV_DEEPL_KEY = os.environ.get("DEEPL_API_KEY", "")

# 語言設定（用於翻譯引擎）
LANG_CONFIG = {
    "zh-TW": {"google": "zh-TW", "deepl": "ZH-HANT", "name": "繁體中文"},
    "zh-CN": {"google": "zh-CN", "deepl": "ZH", "name": "簡體中文"},
    "en": {"google": "en", "deepl": "EN", "name": "英文"},
    "ja": {"google": "ja", "deepl": "JA", "name": "日文"},
    "ko": {"google": "ko", "deepl": "KO", "name": "韓文"},
    "fr": {"google": "fr", "deepl": "FR", "name": "法文"},
    "de": {"google": "de", "deepl": "DE", "name": "德文"}
}

# ---------- 翻譯引擎函式（保持原樣） ----------
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

# ---------- 字幕翻譯輔助函式 ----------
def translate_subtitle_text(text, src_lang, tgt_lang, gemini_key, deepl_key):
    if gemini_key:
        try:
            return translate_gemini(text, src_lang, tgt_lang, gemini_key)
        except Exception as e:
            app.logger.warning(f"Gemini 字幕翻譯失敗: {e}")
    if tgt_lang in ["ja", "ko"] and deepl_key:
        try:
            return translate_deepl(text, src_lang, tgt_lang, deepl_key)
        except Exception as e:
            app.logger.warning(f"DeepL 字幕翻譯失敗: {e}")
    return translate_google(text, src_lang, tgt_lang)

# ---------- YouTube 輔助函式 ----------
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

def parse_vtt_to_text(vtt_content):
    """將 VTT 字幕內容轉為純文字段落（保留時間順序）"""
    captions = []
    for caption in webvtt.read_buffer(io.StringIO(vtt_content)):
        captions.append(caption.text.strip())
    return "\n".join(captions)

# ---------- YouTube 字幕處理路由（使用 yt-dlp） ----------
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

    # yt-dlp 選項
    ydl_opts = {
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en', 'zh-Hant', 'zh-TW', 'ja', 'ko'],
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            subtitles = info.get('subtitles') or info.get('automatic_captions')
            if not subtitles:
                return jsonify({"error": "此影片沒有任何字幕"}), 400

            # 選擇字幕語言（優先英文，其次繁體中文）
            lang_choice = None
            if 'en' in subtitles:
                lang_choice = 'en'
            elif 'zh-Hant' in subtitles:
                lang_choice = 'zh-Hant'
            elif 'zh-TW' in subtitles:
                lang_choice = 'zh-TW'
            else:
                lang_choice = list(subtitles.keys())[0]

            # 取得字幕 URL
            sub_info = subtitles[lang_choice][0]
            sub_url = sub_info.get('url')
            if not sub_url:
                return jsonify({"error": f"無法獲取 {lang_choice} 字幕的下載連結"}), 400

            # 下載字幕內容
            sub_resp = requests.get(sub_url, timeout=30)
            sub_resp.raise_for_status()
            subtitle_content = sub_resp.text

            # 將字幕轉為純文字
            if sub_url.endswith('.vtt') or 'text/vtt' in sub_resp.headers.get('content-type', ''):
                original_text = parse_vtt_to_text(subtitle_content)
            else:
                # 若為其他格式（如 srt），簡單處理
                original_text = subtitle_content

            if not original_text:
                return jsonify({"error": "字幕內容為空"}), 400

            # 分割成句子（簡單按行分割，保留非空行）
            lines = [line.strip() for line in original_text.split('\n') if line.strip()]
            # 將過長的字幕合併（避免一次翻譯太多字數）
            max_chunk = 2000
            chunks = []
            current = ""
            for line in lines:
                if len(current) + len(line) + 1 <= max_chunk:
                    current += line + "\n"
                else:
                    if current:
                        chunks.append(current.strip())
                    current = line + "\n"
            if current:
                chunks.append(current.strip())

            # 逐段翻譯並合併結果
            translated_chunks = []
            for chunk in chunks:
                translated = translate_subtitle_text(chunk, lang_choice, target_lang, gemini_key, deepl_key)
                translated_chunks.append(translated)
            full_translated = "\n".join(translated_chunks)

            # 為下載 SRT 建立含時間戳記的字幕項目（需要詳細解析 VTT，但為了簡化，這裡只提供純文字）
            # 若要完整 SRT，可進一步解析 VTT 的時間資訊，這裡提供簡單版本：將整段文字作為一條字幕
            # 實際上更好的做法是保留原始時間戳，但為了避免過度複雜，我們先回傳純文字，
            # 前端下載 SRT 時使用簡單格式（整段文字作為一條字幕）。
            # 若需要精確 SRT，請使用更完整的 VTT 解析。
            # 這裡我們回傳原始字幕列表（時間+文字）給前端自行組裝 SRT。
            # 以下為解析 VTT 取得逐條字幕的範例：
            captions_list = []
            if sub_url.endswith('.vtt'):
                import io
                vtt = webvtt.read_buffer(io.StringIO(subtitle_content))
                for caption in vtt:
                    start = caption.start_in_seconds
                    duration = caption.end_in_seconds - start
                    original_text_line = caption.text.strip()
                    if original_text_line:
                        translated_line = translate_subtitle_text(original_text_line, lang_choice, target_lang, gemini_key, deepl_key)
                        captions_list.append({
                            "start": start,
                            "duration": duration,
                            "original": original_text_line,
                            "translated": translated_line
                        })
            else:
                # 若無時間資訊，則整段回傳
                captions_list = [{
                    "start": 0,
                    "duration": 10,
                    "original": original_text,
                    "translated": full_translated
                }]

            return jsonify({
                "subtitles": captions_list,
                "video_id": video_id,
                "source_lang": lang_choice,
                "target_lang": target_lang
            })

    except Exception as e:
        app.logger.error(f"YouTube 處理失敗: {e}")
        return jsonify({"error": f"獲取字幕失敗: {str(e)}"}), 500

# ---------- 原有文字翻譯路由 ----------
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
            return jsonify({"result": result, "engine": "Gemini"})
        except Exception as e:
            app.logger.error(f"Gemini 失敗: {e}")

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
