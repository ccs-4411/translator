import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # 允许跨域（开发用，生产同源无需）

# 从环境变量读取 API Keys（Render 设置环境变量）
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DEEPL_API_KEY = os.environ.get("DEEPL_API_KEY", "")

# 语言配置映射（与前端一致）
LANG_CONFIG = {
    "zh-TW": {"googleCode": "zh-TW", "deeplCode": "ZH-HANT", "geminiName": "繁體中文"},
    "zh-CN": {"googleCode": "zh-CN", "deeplCode": "ZH", "geminiName": "簡體中文"},
    "en": {"googleCode": "en", "deeplCode": "EN", "geminiName": "英文"},
    "ja": {"googleCode": "ja", "deeplCode": "JA", "geminiName": "日文"},
    "ko": {"googleCode": "ko", "deeplCode": "KO", "geminiName": "韓文"},
    "fr": {"googleCode": "fr", "deeplCode": "FR", "geminiName": "法文"},
    "de": {"googleCode": "de", "deeplCode": "DE", "geminiName": "德文"}
}

# ---------- 翻译引擎 ----------
def translate_with_google(text, src_lang, tgt_lang):
    """公共 Google 翻译 API (免费，无需 key)"""
    src = LANG_CONFIG.get(src_lang, {}).get("googleCode", "auto")
    tgt = LANG_CONFIG.get(tgt_lang, {}).get("googleCode", "en")
    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={src}&tl={tgt}&dt=t&q={requests.utils.quote(text)}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    translated = "".join(part[0] for part in data[0] if part[0])
    return translated or text

def translate_with_deepl(text, src_lang, tgt_lang, api_key):
    """DeepL 翻译（需要有效 key）"""
    if not api_key:
        raise ValueError("DeepL API Key 未设置")
    tgt_code = LANG_CONFIG.get(tgt_lang, {}).get("deeplCode")
    if not tgt_code:
        raise ValueError(f"不支持 DeepL 目标语言: {tgt_lang}")
    src_code = LANG_CONFIG.get(src_lang, {}).get("deeplCode")
    if src_code == "ZH-HANT":
        src_code = "ZH"
    params = {"text": text, "target_lang": tgt_code}
    if src_code and src_code != "auto":
        params["source_lang"] = src_code
    headers = {"Authorization": f"DeepL-Auth-Key {api_key}"}
    resp = requests.post("https://api-free.deepl.com/v2/translate", data=params, headers=headers, timeout=15)
    resp.raise_for_status()
    result = resp.json()
    return result["translations"][0]["text"]

def translate_with_gemini(text, src_lang, tgt_lang, api_key):
    """Gemini AI 翻译（优先引擎）"""
    if not api_key:
        raise ValueError("Gemini API Key 未设置")
    src_name = LANG_CONFIG.get(src_lang, {}).get("geminiName", src_lang)
    tgt_name = LANG_CONFIG.get(tgt_lang, {}).get("geminiName", tgt_lang)
    prompt = f"請將以下{src_name}內容翻譯成{tgt_name}，只輸出翻譯結果，不要附加任何說明。\n原文: {text}"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2}
    }
    resp = requests.post(url, json=payload, timeout=20)
    if resp.status_code == 429:
        raise Exception("QUOTA_EXCEEDED")
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()

# ---------- 智能调度路由 ----------
@app.route("/translate", methods=["POST"])
def translate():
    data = request.get_json()
    text = data.get("text", "").strip()
    src = data.get("source_lang", "zh-TW")
    tgt = data.get("target_lang", "en")
    if not text:
        return jsonify({"error": "请输入要翻译的文字"}), 400
    if src == tgt:
        return jsonify({"result": text, "engine": "相同语言"})

    # 1. 尝试 Gemini
    gemini_success = False
    final_result = ""
    used_engine = ""
    if GEMINI_API_KEY:
        try:
            final_result = translate_with_gemini(text, src, tgt, GEMINI_API_KEY)
            used_engine = "Gemini AI"
            gemini_success = True
        except Exception as e:
            print(f"Gemini 失败: {e}")
            # 继续降级

    # 2. 降级后备
    if not gemini_success:
        try:
            is_western = tgt in ["en", "fr", "de"]
            is_east_asian = tgt in ["ja", "ko"]
            if is_western:
                final_result = translate_with_google(text, src, tgt)
                used_engine = "Google 翻译 (英法德)"
            elif is_east_asian:
                # 优先 DeepL（若有 Key），否则直接用 Google
                if DEEPL_API_KEY:
                    try:
                        final_result = translate_with_deepl(text, src, tgt, DEEPL_API_KEY)
                        used_engine = "DeepL 翻译 (日韩)"
                    except Exception as e:
                        print(f"DeepL 失败，降级 Google: {e}")
                        final_result = translate_with_google(text, src, tgt)
                        used_engine = "Google 翻译 (DeepL降级)"
                else:
                    final_result = translate_with_google(text, src, tgt)
                    used_engine = "Google 翻译 (日韩无DeepL)"
            else:
                # 中文或其它一律 Google
                final_result = translate_with_google(text, src, tgt)
                used_engine = "Google 翻译"
        except Exception as e:
            return jsonify({"error": f"翻译失败: {str(e)}"}), 500

    return jsonify({"result": final_result, "engine": used_engine})

# 健康检查（Render 需要）
@app.route("/")
def index():
    return app.send_static_file("index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))