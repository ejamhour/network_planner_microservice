import os

BASE_DIR = 'home'  # Define the base directory

# Method to create user directories
def create_user_directory(username):
    user_path = os.path.join(BASE_DIR, username)
    if not os.path.exists(user_path):
        os.makedirs(user_path)
        subfolders = ['configuration', 'datasets', 'logs', 'projects', 'upload']
        for folder in subfolders:
            os.makedirs(os.path.join(user_path, folder))
        return True
    return False
