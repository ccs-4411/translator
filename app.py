import os
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# 从环境变量读取 API Keys（Render 上设置）
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DEEPL_API_KEY = os.environ.get("DEEPL_API_KEY", "")

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
    
    # 使用您原本可工作的模型名称
    model_name = "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2000}
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=30)
        print(f"[Gemini] HTTP Status: {resp.status_code}")
        if resp.status_code != 200:
            print(f"[Gemini] Error Response: {resp.text[:500]}")
            if resp.status_code == 404:
                raise Exception(f"模型 {model_name} 不存在，請檢查名稱")
            elif resp.status_code == 403:
                raise Exception("API Key 無效或沒有權限")
            elif resp.status_code == 429:
                raise Exception("QUOTA_EXCEEDED")
            else:
                raise Exception(f"API 錯誤 {resp.status_code}: {resp.text[:200]}")
        
        data = resp.json()
        # 解析結果
        result = None
        if "candidates" in data and len(data["candidates"]) > 0:
            candidate = data["candidates"][0]
            if "content" in candidate and "parts" in candidate["content"]:
                result = candidate["content"]["parts"][0].get("text")
        if not result:
            raise Exception("無法從回應中提取翻譯結果")
        return result.strip()
    except requests.exceptions.RequestException as e:
        print(f"[Gemini] Request Exception: {e}")
        raise Exception(f"網路請求失敗: {str(e)}")
    except Exception as e:
        print(f"[Gemini] Exception: {e}")
        raise

@app.route("/translate", methods=["POST"])
def translate():
    data = request.get_json()
    text = data.get("text", "").strip()
    src = data.get("source_lang", "zh-TW")
    tgt = data.get("target_lang", "en")
    if not text:
        return jsonify({"error": "請輸入要翻譯的文字"}), 400
    if src == tgt:
        return jsonify({"result": text, "engine": "相同語言"})

    # 優先使用 Gemini（如果有 API Key）
    if GEMINI_API_KEY:
        try:
            result = translate_gemini(text, src, tgt, GEMINI_API_KEY)
            return jsonify({"result": result, "engine": "Gemini AI (2.5 Flash)"})
        except Exception as e:
            print(f"Gemini 失敗: {e}")
            # 繼續降級

    # 後備翻譯
    try:
        if tgt in ["en", "fr", "de"]:
            result = translate_google(text, src, tgt)
            engine = "Google 翻譯 (英法德)"
        elif tgt in ["ja", "ko"]:
            if DEEPL_API_KEY:
                try:
                    result = translate_deepl(text, src, tgt, DEEPL_API_KEY)
                    engine = "DeepL 翻譯 (日韓)"
                except Exception as e:
                    print(f"DeepL 失敗，降級 Google: {e}")
                    result = translate_google(text, src, tgt)
                    engine = "Google 翻譯 (DeepL降級)"
            else:
                result = translate_google(text, src, tgt)
                engine = "Google 翻譯 (日韓無DeepL)"
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
    app.run(host="0.0.0.0", port=port)
