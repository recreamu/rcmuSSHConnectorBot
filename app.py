from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from ssh_utils import run_ssh_command

app = Flask(__name__)
socketio = SocketIO(app)

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('run_command')
def handle_run_command(data):
    command = data.get('command')
    output, error = run_ssh_command(
        host='your.vds.ip',
        username='your_username',
        password='your_password',
        command=command
    )
    emit('command_result', {'output': output, 'error': error})

if __name__ == '__main__':
    socketio.run(app, debug=True)
