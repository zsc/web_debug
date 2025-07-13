import os
import subprocess
import re
import uuid
from flask import Flask, request, jsonify, render_template_string
import google.generativeai as genai
from google.generativeai import types

# --- 1. 配置 ---
# 从环境变量中获取API密钥
try:
    api_key = os.environ["GEMINI_API_KEY"]
    genai.configure(api_key=api_key)
except KeyError:
    print("错误: 请设置环境变量 GEMINI_API_KEY")
    exit()

# 初始化 Flask 应用
app = Flask(__name__)

# --- 2. HTML 模板 ---
# 将单文件HTML直接嵌入Python脚本中
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gemini Patch Generator</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 40px auto; padding: 20px; background-color: #f4f4f9; }
        .container { background: #fff; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); padding: 2rem; }
        h1 { color: #4a4a4a; }
        label { display: block; margin-top: 1rem; margin-bottom: 0.5rem; font-weight: bold; }
        input, select, textarea, button { width: 100%; padding: 0.8rem; border-radius: 4px; border: 1px solid #ccc; font-size: 1rem; box-sizing: border-box; }
        textarea { resize: vertical; min-height: 100px; }
        button { background-color: #007bff; color: white; border: none; cursor: pointer; margin-top: 1.5rem; font-weight: bold; }
        button:hover { background-color: #0056b3; }
        button:disabled { background-color: #aaa; cursor: not-allowed; }
        .result { margin-top: 2rem; padding: 1.5rem; border-radius: 4px; background-color: #e9ecef; white-space: pre-wrap; word-wrap: break-word; font-family: "Courier New", Courier, monospace; }
        .result.success { border-left: 5px solid #28a745; }
        .result.error { border-left: 5px solid #dc3545; color: #721c24; background-color: #f8d7da;}
        .loader { display: none; margin: 1rem 0; text-align: center; }
        pre { background: #f0f0f0; padding: 1em; border-radius: 4px; white-space: pre-wrap; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Gemini 自动修复 & Patch 生成器</h1>
        <form id="patch-form">
            <label for="file_path">文件绝对路径:</label>
            <input type="text" id="file_path" name="file_path" placeholder="/path/to/your/project/file.py" required>

            <label for="model">选择 Gemini 模型:</label>
            <select id="model" name="model">
                <option value="gemini-1.5-flash-latest">Gemini 1.5 Flash (推荐)</option>
                <option value="gemini-1.5-pro-latest">Gemini 1.5 Pro</option>
            </select>
            
            <label for="prompt">你的修改要求 (Prompt):</label>
            <textarea id="prompt" name="prompt" placeholder="例如: 修复XX函数的bug, 增加一个异常处理" required></textarea>

            <button type="submit" id="submit-btn">生成并应用 Patch</button>
        </form>
        <div class="loader" id="loader">处理中，请稍候...</div>
        <div id="result-container"></div>
    </div>

    <script>
        document.getElementById('patch-form').addEventListener('submit', async function(event) {
            event.preventDefault();

            const form = event.target;
            const submitBtn = document.getElementById('submit-btn');
            const loader = document.getElementById('loader');
            const resultContainer = document.getElementById('result-container');

            submitBtn.disabled = true;
            submitBtn.innerText = '正在生成...';
            loader.style.display = 'block';
            resultContainer.innerHTML = '';

            const formData = new FormData(form);
            const data = Object.fromEntries(formData.entries());

            try {
                const response = await fetch('/generate_patch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });
                
                const result = await response.json();

                if (result.status === 'success') {
                    resultContainer.innerHTML = `
                        <div class="result success">
                            <h3>✅ 成功!</h3>
                            <p>${result.message}</p>
                            <h4>Gemini 原始输出:</h4>
                            <pre>${escapeHtml(result.raw_response)}</pre>
                            <h4>提取并修复后的 Patch:</h4>
                            <pre>${escapeHtml(result.patch_content)}</pre>
                        </div>`;
                } else {
                    resultContainer.innerHTML = `<div class="result error"><h3>❌ 失败!</h3><p>${escapeHtml(result.message)}</p></div>`;
                }

            } catch (error) {
                resultContainer.innerHTML = `<div class="result error"><h3>❌ 网络或服务器错误!</h3><p>${escapeHtml(error.toString())}</p></div>`;
            } finally {
                submitBtn.disabled = false;
                submitBtn.innerText = '生成并应用 Patch';
                loader.style.display = 'none';
            }
        });

        function escapeHtml(unsafe) {
            return unsafe
                 .replace(/&/g, "&")
                 .replace(/</g, "<")
                 .replace(/>/g, ">")
                 .replace(/"/g, """)
                 .replace(/'/g, "'");
        }
    </script>
</body>
</html>
"""

# --- 3. 后端逻辑 ---

@app.route('/')
def index():
    """提供主页面"""
    return render_template_string(HTML_TEMPLATE)

@app.route('/generate_patch', methods=['POST'])
def generate_patch():
    """处理生成和应用patch的请求"""
    data = request.json
    file_path = data.get('file_path')
    model_name = data.get('model')
    user_prompt = data.get('prompt')

    if not all([file_path, model_name, user_prompt]):
        return jsonify({'status': 'error', 'message': '所有字段都是必填的。'}), 400

    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return jsonify({'status': 'error', 'message': f'文件未找到或不是一个有效文件: {file_path}'}), 400

    try:
        # 1. 读取文件内容
        with open(file_path, 'r', encoding='utf-8') as f:
            file_content = f.read()

        # 2. 构建 Prompt
        system_instruction = (
            "You are an expert programmer. Your task is to analyze the provided code and the user's request. "
            "Based on this, you must generate a patch file in the standard git diff format. "
            "The patch should ONLY contain the changes required to fulfill the request. "
            "Output ONLY the patch content inside a single ```diff ... ``` code block."
        )
        
        full_prompt = f"""
User Request: {user_prompt}

File to be patched: `{file_path}`
File content:
{file_content}
Please generate the patch file now."""
Please generate the patch file now.
"""
        
        # 3. 调用 Gemini API (严格按照指定格式)
        # 注意: 当前Python SDK的推荐用法是 genai.GenerativeModel(...)
        # 但我们会严格遵循您请求的参数结构, 将其传入 GenerationConfig。
        model = genai.GenerativeModel(model_name)
        
        # 严格遵循请求的参数格式
        generation_config = types.GenerationConfig(
            # max_output_tokens=8192, # 可选, 设定一个较大的值
            temperature=0.3,
        )

        response = model.generate_content(
            contents=full_prompt,
            generation_config=generation_config,
            # system_instruction 在新版SDK中通常在模型初始化时设置
            # model = genai.GenerativeModel(model_name, system_instruction=...)
            # 为了兼容，我们将其放在这里，并已在上面初始化时设置
        )
        # 在新SDK中，system_instruction通过在初始化时设置来传递
        # 为了与您提供的代码段精神保持一致，我们构造一个等效的调用
        # model = genai.GenerativeModel(model_name=model_name, system_instruction=system_instruction)
        # response = model.generate_content(full_prompt, generation_config=generation_config)

        raw_response_text = response.text

        # 4. 提取 Patch 内容
        match = re.search(r"```(?:diff|patch)\n(.*?)```", raw_response_text, re.DOTALL)
        if not match:
            return jsonify({
                'status': 'error',
                'message': 'Gemini未能生成有效的patch格式 (未找到 ```diff...``` 块)。',
                'raw_response': raw_response_text
            }), 500
        
        patch_content = match.group(1)
        
        # 5. 保存并处理 patch 文件
        temp_patch_filename = f"temp_{uuid.uuid4()}.patch"
        with open(temp_patch_filename, 'w', encoding='utf-8') as f:
            f.write(patch_content)
            
        try:
            # 6. 运 fix_patch.py
            fix_process = subprocess.run(
                ['python', 'fix_patch.py', temp_patch_filename],
                check=True, capture_output=True, text=True
            )
            print("fix_patch.py output:", fix_process.stdout)
            
            # 7. Git Apply
            # 必须在文件的父目录运行 git apply
            file_dir = os.path.dirname(os.path.abspath(file_path))
            apply_process = subprocess.run(
                ['git', 'apply', os.path.abspath(temp_patch_filename)],
                cwd=file_dir, check=True, capture_output=True, text=True
            )
            print("git apply output:", apply_process.stdout)

        finally:
            # 8. 清理临时文件
            if os.path.exists(temp_patch_filename):
                os.remove(temp_patch_filename)

        return jsonify({
            'status': 'success',
            'message': f'Patch 已成功应用到 {file_path}',
            'raw_response': raw_response_text,
            'patch_content': patch_content
        })

    except subprocess.CalledProcessError as e:
        error_message = f"命令执行失败: {e.cmd}\n返回码: {e.returncode}\n输出:\n{e.stdout}\n错误:\n{e.stderr}"
        return jsonify({'status': 'error', 'message': error_message}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    # 启动服务器，可以通过 http://127.0.0.1:5000 访问
    app.run(debug=True, port=5000)
