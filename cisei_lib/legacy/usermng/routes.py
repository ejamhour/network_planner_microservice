from flask import Blueprint, render_template, session, request, jsonify, current_app
from werkzeug.utils import secure_filename
import os

app_routes = Blueprint('app_routes', __name__)

@app_routes.route('/file_manager', methods=['GET'])
def file_manager():
    if session.get('user_info') is None:
        return render_template('error.html')
    
    user_id = session.get("user_info")["sub"]
    if not current_app.user_home.check_folders(user_id):
        current_app.user_home.create_folders(user_id)
    return render_template('file_manager.html')

@app_routes.route('/list_files', methods=['POST'])
def list_files():
    # Get the current path and the real path from the request
    user = session.get("user_info")["sub"]
    current_path = request.json.get('current_path', '')
    relative_path = os.path.join(user, os.path.relpath(current_path, '/home')) if current_path != '' else ''
    user_base_dir = current_app.user_home.get_base_dir()
    
    # Create the full path from the current path
    full_path = os.path.join(user_base_dir, relative_path.strip("/"))

    # Verify if the directory exists
    if not os.path.exists(full_path) or not os.path.isdir(full_path):
        return jsonify({"error": "Diretório não encontrado"}), 404

    # List the files and directories
    try:
        items = list()
        if os.path.normpath(full_path) == os.path.normpath(user_base_dir.strip("/")):
            items = [file.name for file in os.scandir(full_path)
            if user in file.name or 'public' in file.name
            ]
        else:
            items = os.listdir(full_path)
        
        items = sorted(items, key=lambda x: (not os.path.isdir(os.path.join(current_path, x))))
        files_and_dirs = []
        
        for item in items:
            item_path = os.path.join(full_path, item)
            files_and_dirs.append({
                "name": item if item != user else 'home',
                "is_directory": os.path.isdir(item_path)
            })

        return jsonify({"current_path": current_path, "contents": files_and_dirs})
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app_routes.route('/upload', methods=['POST'])
def upload_file():
    path = request.form.get('path', '').lstrip('/') # Remove leading slash 
    relative_path = path.replace('home', session.get("user_info")["sub"], 1)

    files = request.files.to_dict(flat=False)
    file_names = list()

    for file in files["files"]:
        file_name = secure_filename(file.filename)
        file_names.append(file_name)
        save_path = os.path.join(current_app.user_home.get_base_dir(), relative_path, file_name)
        file.save(save_path)
    return jsonify(success=True, filename=file_names)

@app_routes.route('/download', methods=['GET'])
def download_file():
    file_name = request.args.get('filename')
    path = request.args.get('path', '').lstrip('/')
    relative_path = path.replace('home', session.get("user_info")["sub"], 1)
    file_path = os.path.join(current_app.user_home.get_base_dir(), relative_path, file_name)

    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return jsonify({"error": "File not found"}), 404

    try: # TODO: study it 
        with open(file_path, 'rb') as file:
            data = file.read()
        response = jsonify(success=True)
        response.headers['Content-Disposition'] = f'attachment; filename={file_name}'
        response.headers['Content-Type'] = 'application/octet-stream'
        response.data = data
        return response
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app_routes.route('/api/directory', methods=['GET'])
def list_directory():
    path = request.args.get('path', '')
    current_path = current_path.lstrip('/')
    directory_path = os.path.join(current_app.user_home.get_base_dir(), path)

    if not os.path.exists(directory_path):
        return jsonify({"error": "Path not found"}), 404

    # Filter the files and directories
    folders = [f for f in os.listdir(directory_path) if os.path.isdir(os.path.join(directory_path, f))]
    files = [f for f in os.listdir(directory_path) if os.path.isfile(os.path.join(directory_path, f))]

    return jsonify({"folders": folders, "files": files})