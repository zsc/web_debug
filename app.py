import os
import subprocess
import re
import uuid
from flask import Flask, request, jsonify, render_template
from google import genai
import logging

logging.basicConfig(level=logging.INFO)

# --- 1. 配置 ---
# 从环境变量中获取API密钥
client = genai.Client()

# 初始化 Flask 应用
app = Flask(__name__)

# --- 2. 核心逻辑 ---

@app.route("/")
def index():
    """提供主页面"""
    return render_template("index.html")


@app.route("/generate_patch", methods=["POST"])
def generate_patch():
    """处理生成和应用patch的请求"""
    data = request.json
    file_path = data.get("file_path")
    model_name = data.get("model")
    user_prompt = data.get("prompt")

    if not all([file_path, model_name, user_prompt]):
        return jsonify({"status": "error", "message": "所有字段都是必填的。"}), 400

    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"文件未找到或不是一个有效文件: {file_path}",
                }
            ),
            400,
        )

    try:
        # 1. 读取文件内容
        logging.info(f"正在读取文件: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
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

File to be patched: `{os.path.basename(file_path)}`
File content:
{file_content}
Please generate the patch file now.
"""

        # 3. 调用 Gemini API (严格按照指定格式)
        response = client.models.generate_content(
            contents=full_prompt,
            model = "gemini-2.5-flash",
        )

        raw_response_text = response.text
        logging.info("收到 Gemini 的响应。")

        # 4. 提取 Patch 内容
        match = re.search(r"```(?:diff|patch)\n(.*?)```", raw_response_text, re.DOTALL)
        if not match:
            logging.error("Gemini 未能生成有效的 patch 格式。")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Gemini未能生成有效的patch格式 (未找到 ```diff...``` 块)。",
                        "raw_response": raw_response_text,
                    }
                ),
                500,
            )

        patch_content = match.group(1)

        # 5. 保存并处理 patch 文件
        temp_patch_filename = f"temp_{uuid.uuid4()}.patch"
        with open(temp_patch_filename, "w", encoding="utf-8") as f:
            f.write(patch_content)

        try:
            # 6. (可选但推荐) 运行外部脚本修复 patch
            # 确保 fix_patch.py 存在且可执行
            fix_script_path = "fix_patch.py"
            if os.path.exists(fix_script_path):
                logging.info(f"正在运行修复脚本: {fix_script_path}")
                fix_process = subprocess.run(
                    ["python3", fix_script_path, temp_patch_filename], # 使用 python3 更通用
                    check=True,
                    capture_output=True,
                    text=True,
                )
                logging.info(f"fix_patch.py stdout: {fix_process.stdout}")
                with open(temp_patch_filename, 'w', encoding="utf-8") as f:
                    f.write(fix_process.stdout)

            # 读取最终（可能已被修复）的 patch 内容用于返回给前端
            with open(temp_patch_filename, 'r', encoding='utf-8') as f:
                fixed_patch_content = f.read()

            # 7. Git Apply
            # 必须在文件的父目录运行 git apply
            file_dir = os.path.dirname(os.path.abspath(file_path))
            logging.info(f"在目录 {file_dir} 中应用补丁...")
            apply_process = subprocess.run(
                ["git", "apply", "--check", os.path.abspath(temp_patch_filename)],
                cwd=file_dir, check=True, capture_output=True, text=True
            )
            apply_process = subprocess.run(
                ["git", "apply", os.path.abspath(temp_patch_filename)],
                cwd=file_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            logging.info(f"git apply output: {apply_process.stdout}")

        finally:
            # 8. 清理临时文件
            if os.path.exists(temp_patch_filename):
                os.remove(temp_patch_filename)
                logging.info(f"已删除临时文件: {temp_patch_filename}")

        return jsonify(
            {
                "status": "success",
                "message": f"Patch 已成功应用到 {file_path}",
                "patch_content": fixed_patch_content, # 返回修复后的patch
            }
        )

    except subprocess.CalledProcessError as e:
        error_message = f"命令执行失败: {e.cmd}\n返回码: {e.returncode}\n错误:\n{e.stderr}"
        logging.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500
    except Exception as e:
        logging.error(f"发生未知错误: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    # 启动服务器，可以通过 http://127.0.0.1:5003 访问
    app.run(host="0.0.0.0", port=5003, debug=True)
