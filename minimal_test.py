from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello():
    # This route does almost nothing, just returns text
    return 'Minimal Flask App is Working!'

if __name__ == '__main__':
    print("--- Starting Minimal Flask Test App ---")
    # Using a different port just in case 5000 has issues
    app.run(debug=True, host='0.0.0.0', port=5001)
    print("--- Minimal Flask Test App Stopped ---")