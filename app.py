from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/')
def index():
    return jsonify(message='Hello, Flask is running!')

if __name__ == '__main__':
    # 直接用 python app.py 啟動開發伺服器
    app.run(host='127.0.0.1', port=5000, debug=True)
